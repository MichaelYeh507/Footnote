"""Section-aware chunking for 10-K filings, with page attribution.

Plan §4 asks for two things: chunk on Item boundaries rather than a fixed
window, and carry page numbers through, because the page is what a cited answer
points at.

Neither is available for free. EDGAR filings mark pages only as styled `<hr>`
rules, and they mark section headings not at all -- the anchors in these
documents are inline-XBRL context ids, not `id="item1"`. So both signals are
recovered from the document, and both were measured across the whole corpus
before this module was written rather than assumed from what a 10-K is supposed
to look like:

* **Pages.** All 44 corpus documents carry `<hr>` page rules, and in every one
  the `<hr>` count equals the `page-break-*` CSS count exactly (51 to 115 per
  filing). That agreement is why the rule is trusted as the page signal.
* **Headings.** Item headings are prose, and the shapes vary by filer in ways
  that defeat the obvious rules. The title sits inline for AMCR and PGR
  ("Item 1. Business") and on the *following* line for CHTR, HON and PG
  ("Item 1." / "Business"), so "has a title on the same line" is not a
  discriminator. Most filings open with a table of contents that repeats every
  Item, so each one appears at least twice. PGR carries no contents table at all
  and instead writes cross-references at line start ("Item 1A, Risk Factors -
  II. Insurance Risks"). DVN and SO combine items into one heading
  ("Items 1 and 2. Business and Properties").

What survives all of that is structure rather than wording: **the real headings
are the run of Items, in canonical order, that is backed by the most text.**
Cross-references are dropped first, by the comma that follows the item number.

AMENDMENT 2 (2026-08-18) replaced "the longest run, ties toward later
occurrences" with that rule, because length is the one contest a contents table
always wins: it lists every Item by construction, while a body prints some
headings in a form no pattern here matches. Chaining twenty table entries to
the two or three headings near the end beat every body chain on length, and SO
filed 91% of its document under `Item 13` as a result. HON, whose `Item N` lines
appear only in a cross-reference index after its signature block, delivered 0.7%
of its text. Both numbers, the repair, and its acceptance criteria are in the
plan; the corrected figures are published in `EVALUATION-SPEC.md`.

Measured over all 44 documents after the repair: **100% of the corpus text sits
inside a detected section**, and 42 of 44 filings yield Items 1, 1A, 7 and 8.
HON is the exception and is asserted as one -- its document order is not
canonical, so no run in Item order can hold both its MD&A (printed pages 17-25)
and its Risk Factors (page 41). The detector this project retired --
`item_8_by_reference` -- failed because it asked prose a question prose cannot
answer. This one asks only where the headings are, which the document does
record, and the corpus tests in tests/test_chunking.py are what keep that claim
honest.
"""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from services.html_parser import (
    _HIDDEN_STYLE, _IXBRL_METADATA_TAG, _decode, extract_text_from_html,
)

# Canonical 10-K item order. A plain sort is wrong twice over: "10" sorts before
# "2" lexically, and "1A" must follow "1" rather than precede it.
ITEM_ORDER = (
    "1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8",
    "9", "9A", "9B", "9C", "10", "11", "12", "13", "14", "15", "16",
)
_RANK = {item: index for index, item in enumerate(ITEM_ORDER)}

# The sections §4 names for chunking; the corpus test asserts every filing has them.
MAJOR_ITEMS = ("1", "1A", "7", "8")

# Below this many headings in canonical order, a document has no detectable
# structure and this module says so instead of inventing boundaries.
MIN_SECTIONS = 5

# How far past a heading to look for a title on its own line. Filers put a blank
# line between the two; three lines covers every layout in the corpus without
# reaching into the section body.
_TITLE_LOOKAHEAD = 3

# One heading line. The item list allows the combined forms DVN and SO use
# ("Items 1 and 2.", "Items 10, 11, 12, and 13"); `items?` allows the plural
# that comes with them.
#
# The trailing \b below is what keeps "Items 1 and 2." from parsing as Item 1A.
# The optional [a-c] does take the "a" of "and" on its first attempt, but then
# no word boundary exists between that "a" and the "nd" that follows, so the
# engine backtracks and the item list matches "1 and 2" as intended. Verified
# by comparing both forms directly; a negative lookahead on the suffix was
# tried here and removed as redundant.
_ITEM_NUMBER = r"\d{1,2}(?:\s*[a-c])?"
_HEADING = re.compile(
    r"^items?\s+"
    rf"({_ITEM_NUMBER}(?:\s*(?:,|and|&)\s*{_ITEM_NUMBER})*)"
    r"\b\s*(.*)$",
    re.I,
)
_ITEM_PART = re.compile(r"(\d{1,2})(?:\s*([a-c])(?![a-z]))?", re.I)

# The item titles Form 10-K itself specifies. Added by AMENDMENT 2 (2026-08-18)
# because HON marks its body sections with the title alone -- "Properties" on
# printed page 53, "Controls and Procedures" on page 125 -- and prints "Item N"
# only in a cross-reference index after its signature block. Before the
# amendment that cost HON 99.3% of its text.
#
# This list is the form's, not any filer's. Nothing in it was added to make a
# particular document parse, which is the difference between reading the
# regulation and fitting the corpus.
ITEM_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity, Related Stockholder Matters "
         "and Issuer Purchases of Equity Securities",
    "6": "Selected Financial Data",
    "7": "Management's Discussion and Analysis of Financial Condition and "
         "Results of Operations",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants on Accounting and "
         "Financial Disclosure",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "9C": "Disclosure Regarding Foreign Jurisdictions that Prevent Inspections",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership of Certain Beneficial Owners and Management and "
          "Related Stockholder Matters",
    "13": "Certain Relationships and Related Transactions, and Director "
          "Independence",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
    "16": "Form 10-K Summary",
}

# A title heading is matched on its alphanumerics alone. Filers abbreviate from
# the right ("Management's Discussion and Analysis", "Exhibits"), so a line is
# accepted when it is a prefix of the canonical title -- and comparing stripped
# text sidesteps the apostrophes, which arrive from EDGAR as U+FFFD often
# enough that "Management's" and "Management?s" have to be the same heading.
_MIN_TITLE_CHARS = 8
_MIN_TITLE_WORDS = 3
_ALPHANUMERIC = re.compile(r"[^a-z0-9 ]+")

# A heading is "backed by text" when this many characters separate it from the
# next candidate heading anywhere in the document. Measured before the rule was
# written, over SO, DVN, MPC and GWW: consecutive contents-table entries sit 18
# to 41 characters apart, while the smallest gap that holds prose is 1,085. The
# threshold sits between the two populations with roughly five times' margin on
# each side, which is what makes it a boundary rather than a dial.
#
# Measured against the *next candidate*, not the next chosen heading, so the
# answer belongs to the heading rather than to the chain. Scoring whole sections
# was tried first and is wrong: it rewards dropping a heading whenever doing so
# merges two thin sections into one thick one, which is a bounty on losing
# boundaries. The synthetic filing in the tests lost four of eleven Items that
# way before this was measured.
_BACKED_CHARS = 200

# The signature block. Form 10-K prescribes the sentence, so that is the
# reliable half; the bare heading is checked too because DGX prints "Signatures"
# above a variant ("Sections 13 or 15(d)") that the prescribed wording misses.
_SIGNATURE_HEADING = re.compile(r"^signatures?$", re.I)
_SIGNATURE_SENTENCE = re.compile(
    r"pursuant to the requirements of sections?\s+13\s+or\s+15\s?\(\s?d\s?\)",
    re.I,
)

# Marks a page rule while the text is being built, then is removed. Chosen to be
# a single token on its own line and to survive whitespace normalization.
_PAGE_TOKEN = "RAGPAGEBREAK"

_PAGE_BREAK_STYLE = re.compile(r"page-break-(?:before|after)\s*:\s*always", re.I)


@dataclass(frozen=True)
class TextWithPages:
    """Extracted text, plus the line at which each page starts.

    `text` is byte-identical to `extract_text_from_html`'s output. That is not a
    nicety: the published Phase 2 numbers were computed over exactly that text,
    so a page-aware extractor that returned anything else would quietly make
    RESULTS.md unreproducible.
    """

    text: str
    page_starts: tuple[int, ...]

    @property
    def page_count(self) -> int:
        return len(self.page_starts)


@dataclass(frozen=True)
class Section:
    """One Item section: where it starts, where the next one does, what it says."""

    item: str
    covers: tuple[str, ...]
    title: str
    start_line: int
    end_line: int


def canonical_rank(item: str) -> int:
    """Position of an item in the 10-K's own order. Raises on an unknown item
    rather than sorting it somewhere arbitrary."""
    return _RANK[item.upper()]


def _collapse(lines: list[str]) -> tuple[list[str], list[int]]:
    """Apply html_parser's blank-line collapse, reporting where each kept line
    landed. `re.sub(r"\\n{3,}", "\\n\\n", ...)` leaves at most one blank line
    between blocks, so that is what this reproduces line-wise."""
    kept: list[str] = []
    mapping: list[int] = []
    blanks = 0
    for line in lines:
        if line:
            blanks = 0
            mapping.append(len(kept))
            kept.append(line)
            continue
        blanks += 1
        mapping.append(len(kept))
        if blanks == 1:
            kept.append(line)
    return kept, mapping


def extract_text_with_pages(file_bytes: bytes) -> TextWithPages:
    """Extract text and locate page boundaries.

    The text is taken from `extract_text_from_html` directly, so equality with
    the published extractor holds by construction rather than by matching its
    behaviour twice. A second parse, with the page rules marked, supplies only
    the line numbers.
    """
    text = extract_text_from_html(file_bytes)

    soup = BeautifulSoup(_decode(file_bytes), "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for tag in soup.find_all(_IXBRL_METADATA_TAG):
        tag.decompose()
    for tag in list(soup.find_all(style=_HIDDEN_STYLE)):
        tag.decompose()

    marks = soup.find_all("hr")
    marks += [
        tag for tag in soup.find_all(style=_PAGE_BREAK_STYLE)
        if tag.name != "hr"
    ]
    for tag in marks:
        tag.replace_with(_PAGE_TOKEN)

    marked = soup.get_text(separator="\n")
    marked = re.sub(r"[ \t\xa0  ]+", " ", marked)
    lines = [line.strip() for line in marked.splitlines()]

    content: list[str] = []
    break_after: list[int] = []
    for line in lines:
        if _PAGE_TOKEN in line:
            remainder = line.replace(_PAGE_TOKEN, "").strip()
            break_after.append(len(content))
            if remainder:
                content.append(remainder)
            continue
        content.append(line)

    kept, mapping = _collapse(content)

    # html_parser strips the whole document at the end, which can only remove
    # leading and trailing blank lines.
    lead = 0
    while lead < len(kept) and not kept[lead]:
        lead += 1
    trail = len(kept)
    while trail > lead and not kept[trail - 1]:
        trail -= 1

    page_starts = [0]
    for index in break_after:
        target = mapping[index] if index < len(mapping) else len(kept)
        line = min(max(target - lead, 0), max(trail - lead - 1, 0))
        if line > page_starts[-1]:
            page_starts.append(line)

    return TextWithPages(text=text, page_starts=tuple(page_starts))


def page_of_line(result: TextWithPages, line: int) -> int:
    """The 1-indexed page a line falls on."""
    page = 1
    for index, start in enumerate(result.page_starts):
        if line >= start:
            page = index + 1
        else:
            break
    return page


def _normalize_title(line: str) -> str:
    return " ".join(_ALPHANUMERIC.sub("", line.strip().lower()).split())


_NORMALIZED_TITLES = tuple(
    (item, _normalize_title(title)) for item, title in ITEM_TITLES.items()
)


def _title_item(raw: str) -> str | None:
    """The Item a bare title line names, or None.

    A line qualifies two ways: it is the canonical title exactly, or it is a
    prefix of one at least three words long. Filers abbreviate from the right --
    "Management's Discussion and Analysis" for Item 7, "Market for Registrant's
    Common Equity" for Item 5 -- so prefixes have to be admitted, but admitting
    short ones is how HON's stray "Changes in" became an Item 9 heading and
    swallowed 4,609 lines of financial statements. Three words is what separates
    an abbreviated title from a fragment of a sentence.

    Where a prefix fits more than one title the earlier Item wins, and
    _best_chain decides whether the line was a heading at all.
    """
    normalized = _normalize_title(raw)
    if len(normalized) < _MIN_TITLE_CHARS:
        return None
    for item, title in _NORMALIZED_TITLES:
        if title == normalized:
            return item
    if len(normalized.split()) < _MIN_TITLE_WORDS:
        return None
    for item, title in _NORMALIZED_TITLES:
        if title.startswith(normalized + " "):
            return item
    return None


def _candidates(text: str) -> list[tuple[int, list[str], str]]:
    """Every line that could be an Item heading, cross-references removed."""
    found = []
    for index, raw in enumerate(text.splitlines()):
        match = _HEADING.match(raw.strip())
        if not match:
            # A filing may print the title alone -- see ITEM_TITLES.
            item = _title_item(raw)
            if item is not None:
                found.append((index, [item], raw.strip()))
            continue
        rest = match.group(2).strip()
        # "Item 1A, Risk Factors - II. Insurance Risks" is a pointer, not a
        # heading. The comma is what distinguishes it, and combined headings do
        # not reach here with one because their commas sit inside the item list.
        if rest.startswith(","):
            continue
        items = [
            number + (letter or "").upper()
            for number, letter in _ITEM_PART.findall(match.group(1))
        ]
        items = [item for item in items if item in _RANK]
        if not items:
            continue
        found.append((index, items, rest))
    return found


def _best_chain(candidates: list[tuple[int, list[str], str]],
                line_ends: list[int]) -> list[int]:
    """Indices of the run in canonical order that best partitions the document.

    Ranked by how many of its headings are backed by text, then by length, then
    by how early it starts. AMENDMENT 2 (2026-08-18) put the first term in front
    and reversed the third, and both reasons are measured rather than aesthetic.

    A contents table is a run in canonical order just as the body is, and it is
    a *complete* one -- every Item, by construction -- while a body is not,
    because some headings are printed in a form this pattern does not match. So
    a chain of contents-table entries stitched to the two or three headings
    printed near the end is longer than any chain drawn from the body alone,
    and under the superseded rule it won on length before the later-occurrence
    tie-break was ever consulted. SO put 91% of its filing under Item 13 that
    way, DVN 82% under Item 9C.

    Counting headings backed by text inverts that: a contents table's entries
    are 18 to 41 characters apart and back nothing, while a body's headings each
    open a passage. No density threshold and no per-filer rule -- the contents
    table loses on what it is, not on where it sits.

    Preferring later lines was the superseded rule's way of beating the contents
    table, and once "backed by text" does that job the preference is not merely
    unnecessary but wrong. HON repeats each section's title as a running page
    header -- "FINANCIAL STATEMENTS AND SUPPLEMENTARY DATA" on some eighty
    consecutive pages -- so preferring later starts Item 8 at printed page 122
    instead of 57. A section begins where it begins, so ties now go to the
    earliest start.
    """
    if not candidates:
        return []

    # Chain-independent, so a heading's worth cannot change with the company it
    # keeps -- see _BACKED_CHARS for the merging bounty that alternative buys.
    backed = []
    for index, (line, _items, _rest) in enumerate(candidates):
        following = (candidates[index + 1][0] if index + 1 < len(candidates)
                     else len(line_ends) - 1)
        backed.append(line_ends[following] - line_ends[line] > _BACKED_CHARS)

    best: list[tuple[int, int, int, int]] = []
    for index, (line, items, _rest) in enumerate(candidates):
        populated, length, total, previous = int(backed[index]), 1, -line, -1
        for other in range(index):
            other_line, other_items, _ = candidates[other]
            if other_line >= line:
                continue
            # Compare on the first item of each heading, not the last. A
            # combined heading covers a range ("Items 1 and 2"), but the items
            # that follow it in the document can rank inside that range --
            # DVN prints Items 1 and 2, then 1A, 1B, 1C. Ordering by where each
            # heading begins is what admits that perfectly ordinary layout.
            if _RANK[other_items[0]] >= _RANK[items[0]]:
                continue
            populated_so_far, length_so_far, total_so_far, _ = best[other]
            score = (populated_so_far + int(backed[index]),
                     length_so_far + 1,
                     total_so_far - line)
            if score > (populated, length, total):
                populated, length, total = score
                previous = other
        best.append((populated, length, total, previous))

    end = max(range(len(candidates)),
              key=lambda i: (best[i][0], best[i][1], best[i][2]))
    if best[end][1] < MIN_SECTIONS:
        return []

    chain = []
    cursor = end
    while cursor != -1:
        chain.append(cursor)
        cursor = best[cursor][3]
    return list(reversed(chain))


def _line_ends(lines: list[str]) -> list[int]:
    """Cumulative character offset of each line start, with a final total.

    Lets _best_chain price a candidate section in characters at O(1), which is
    what makes "does this section carry text" affordable inside the O(n^2) scan.
    """
    ends = [0]
    for line in lines:
        ends.append(ends[-1] + len(line) + 1)
    return ends


def find_signature_line(lines: list[str], after: int = -1) -> int | None:
    """Where the signature block starts, or None.

    Answered relative to `after` -- normally the last Item heading -- because
    every filing lists SIGNATURES in its contents table too, and the first match
    in the document is almost always that entry rather than the block itself.
    """
    for index in range(max(after + 1, 0), len(lines)):
        stripped = lines[index].strip()
        if _SIGNATURE_HEADING.match(stripped) or _SIGNATURE_SENTENCE.search(stripped):
            return index
    return None


def find_sections(text: str) -> list[Section]:
    """The filing's Item sections, in canonical order.

    Returns an empty list when no run of headings is found. A caller can fall
    back to another strategy; a caller cannot detect an invented boundary, so
    this never guesses.
    """
    lines = text.splitlines()
    total_lines = len(lines)
    candidates = _candidates(text)
    chain = _best_chain(candidates, _line_ends(lines))
    if not chain:
        return []

    # AMENDMENT 2 (2026-08-18): the last Item section stops at the signature
    # block instead of running to the end of the document. For the 12 filings
    # that incorporate Item 8 by reference the financial statements are printed
    # *after* the signatures, and absorbing them made CHTR and DGX carry half
    # their chunks under "Item 16 Form 10-K Summary" -- a citation naming a
    # section the text is not in.
    signature = find_signature_line(lines, candidates[chain[-1]][0])
    last_end = signature if signature is not None else total_lines

    sections = []
    for position, index in enumerate(chain):
        start, items, rest = candidates[index]
        end = candidates[chain[position + 1]][0] if position + 1 < len(chain) else last_end
        title = rest.lstrip(".-:—– \t").strip()
        if not title:
            # Filers such as CHTR, HON and PG put the title on its own line, and
            # a blank line often sits between the two ("Item 1." / "" /
            # "Business."). Measured: looking only at start + 1 left 40 of 1000
            # sections untitled across the corpus.
            for offset in range(1, _TITLE_LOOKAHEAD + 1):
                if start + offset >= total_lines:
                    break
                following = lines[start + offset].strip()
                # Blank lines sit between heading and title, and CTSH puts the
                # heading's own full stop on a line by itself ("Item 1B" / "." /
                # "Unresolved Staff Comments").
                if not following.strip(".-:—– \t"):
                    continue
                if not _HEADING.match(following):
                    title = following
                break
        sections.append(Section(
            item=items[0],
            covers=tuple(items),
            title=title.rstrip("."),
            start_line=start,
            end_line=end,
        ))

    # What sits before the first Item heading is chunked too, on the same
    # reasoning as the tail and with the same empty label. It is the cover page,
    # the contents table and the forward-looking-statements notice -- outside
    # the Item structure, genuinely un-Item-able, and dropping it silently cost
    # MA 994 lines including the shares-outstanding and fiscal-year facts a
    # reader is most likely to ask for.
    head = candidates[chain[0]][0]
    if head > 0:
        sections.insert(0, Section(
            item="",
            covers=(),
            title="",
            start_line=0,
            end_line=head,
        ))

    # The tail is chunked, never dropped: for CHTR, CTSH, DGX, QCOM and VICI it
    # is the financial statements. It carries an empty item because labelling it
    # `Item 8` would be an inference -- those filings do say in Item 8 that the
    # statements appear later, but reading a label off that sentence is the
    # class of prose-reading detector this project already retired once. An
    # empty label costs Item-level filtering; a wrong one costs the citation.
    if signature is not None and signature < total_lines:
        sections.append(Section(
            item="",
            covers=(),
            title="",
            start_line=signature,
            end_line=total_lines,
        ))
    return sections
