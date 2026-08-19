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
are the longest run of Items in canonical order across the document.** A
contents table is such a run too, so ties are broken toward later occurrences,
which is what separates the body from the front matter. Cross-references are
dropped first, by the comma that follows the item number.

Measured over all 44 documents: 44 of 44 yield Items 1, 1A, 7 and 8. The
detector this project retired -- `item_8_by_reference` -- failed because it
asked prose a question prose cannot answer. This one asks only where the
headings are, which the document does record, and the corpus test in
tests/test_chunking.py is what keeps that claim honest.
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


def _candidates(text: str) -> list[tuple[int, list[str], str]]:
    """Every line that could be an Item heading, cross-references removed."""
    found = []
    for index, raw in enumerate(text.splitlines()):
        match = _HEADING.match(raw.strip())
        if not match:
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


def _best_chain(candidates: list[tuple[int, list[str], str]]) -> list[int]:
    """Indices of the longest run in canonical order, preferring later lines.

    The tie-break is the whole trick. A contents table is a run in canonical
    order just as the body is, and on a filing that has both they are the same
    length -- so length alone cannot choose. Summing line numbers prefers the
    run that sits later in the document, which is the body.
    """
    if not candidates:
        return []

    best: list[tuple[int, int, int]] = []
    for index, (line, items, _rest) in enumerate(candidates):
        length, total, previous = 1, line, -1
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
            length_so_far, total_so_far, _ = best[other]
            if (length_so_far + 1, total_so_far + line) > (length, total):
                length, total, previous = length_so_far + 1, total_so_far + line, other
        best.append((length, total, previous))

    end = max(range(len(candidates)), key=lambda i: (best[i][0], best[i][1]))
    if best[end][0] < MIN_SECTIONS:
        return []

    chain = []
    cursor = end
    while cursor != -1:
        chain.append(cursor)
        cursor = best[cursor][2]
    return list(reversed(chain))


def find_sections(text: str) -> list[Section]:
    """The filing's Item sections, in canonical order.

    Returns an empty list when no run of headings is found. A caller can fall
    back to another strategy; a caller cannot detect an invented boundary, so
    this never guesses.
    """
    candidates = _candidates(text)
    chain = _best_chain(candidates)
    if not chain:
        return []

    total_lines = len(text.splitlines())
    lines = text.splitlines()
    sections = []
    for position, index in enumerate(chain):
        start, items, rest = candidates[index]
        end = candidates[chain[position + 1]][0] if position + 1 < len(chain) else total_lines
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
    return sections
