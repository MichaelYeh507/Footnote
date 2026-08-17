"""Rendering the filing for a human labeler: sanitize, highlight, guide.

Imports nothing that can reach model output. Same guarantee as
evaluation.labeling, and it matters more here because this module feeds a web
page: a bigger surface, and one where a mistake is less visible.

Sanitization is not paranoia about EDGAR. The filings are fetched over HTTPS
from sec.gov and their sha256 is recorded. It is that the labeling page and the
filing share an origin, so any script surviving in a filing would be able to
drive the labeling API -- submit labels, advance the queue -- and the labels are
the one artifact in this project with no independent check.
"""

import re

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning  # noqa: F401

from evaluation.labeling import FIELD_PATTERNS

# Tags that execute, navigate, or load remote content. Dropped entirely.
_ACTIVE_TAGS = ("script", "iframe", "object", "embed", "applet", "link", "base",
                "form", "meta")

_EVENT_ATTR = re.compile(r"^on", re.I)
_DANGEROUS_URL = re.compile(r"^\s*(javascript|vbscript|data)\s*:", re.I)
_URL_ATTRS = ("href", "src", "action", "formaction", "xlink:href")

# Where the value lives, and the specific way each field is misread. Shown next
# to the field so the labeler is not working from memory or from chat.
#
# These mirror the field definitions given to the extractor. That is
# deliberate: if the labeler and the model are answering different questions,
# the result measures definitional disagreement rather than extraction skill.
FIELD_GUIDANCE = {
    "company_name":
        "Cover page, immediately above \"(Exact name of registrant as specified "
        "in its charter)\". If two registrants file jointly (parent plus a "
        "subsidiary), take the first listed.",
    "ticker":
        "Cover page, the \"Trading Symbol(s)\" column. Filings often register "
        "notes or preferred series alongside the common stock -- take the "
        "common stock symbol, not the notes.",
    "fiscal_year_end":
        "Cover page, \"For the fiscal year ended ___\". The \"transition period "
        "from ___ to ___\" line directly below it is a different field and is "
        "usually blank. Any date format is fine; both sides are parsed to ISO.",
    "employees":
        "Item 1, Human Capital. May be prose or a table row. TRAP: if the table "
        "header says \"(in thousands)\", a literal read is wrong by 1000x. If "
        "broken down by region, only the total is the answer.",
    "total_assets":
        "\"Total assets\" on the CONSOLIDATED BALANCE SHEET, Item 8. TRAP: the "
        "same words appear in segment notes, guarantor/obligor supplemental "
        "tables, and intermediate subtotals. Confirm you are in the "
        "consolidated statement. REPORT IN MILLIONS -- read the caption above "
        "the table: 12 of 39 corpus filings report in THOUSANDS, and those "
        "figures must be divided by 1,000.",
    "revenue_most_recent_fy":
        "Top-line total from the consolidated statement of operations. Labels "
        "vary: Total net sales, Total revenues, Total sales and revenues, Total "
        "net revenue. TRAP: two or three years sit side by side -- confirm the "
        "column header rather than assuming the first column. Do not use a "
        "total folding in non-operating income. REPORT IN MILLIONS -- read the "
        "caption: many filings report in THOUSANDS, divide those by 1,000.",
    "ceo_name":
        "The person whose title is Chief Executive Officer -- signature page or "
        "Part III. TRAP: \"principal executive offices\" on the cover page is a "
        "street address, not a person. If a transition is disclosed, take the "
        "one the filing presents as current.",
    "dividends_declared_per_share":
        "Per-share amount declared on common stock. Item 5, the statement of "
        "equity, or the income statement. TRAP: return the PER SHARE amount, "
        "never total dollars paid. Dollars per share, not millions. Filing says "
        "none declared -> stated none. Filing never addresses dividends -> not "
        "addressed.",
    "goodwill_impairment":
        "Goodwill impairment charge for the most recent year -- the goodwill "
        "note. TRAP: risk-factor language (\"we could be required to record an "
        "impairment\") is hypothetical, not a statement that none occurred. The "
        "goodwill carrying balance is not an impairment, and impairment of any "
        "other asset is not goodwill impairment. States a charge -> value in "
        "MILLIONS, dividing by 1,000 if the table is in thousands. States none "
        "occurred -> stated none. Never addressed -> not addressed.",
}


def sanitize_filing_html(raw: bytes | str) -> str:
    """Strip anything active, keep everything readable.

    Tables and inline styles are kept deliberately: a 10-K is mostly tables,
    and rendering them is the entire reason this exists rather than the
    terminal tool it replaces.
    """
    soup = BeautifulSoup(raw, "html.parser")

    for tag in soup(list(_ACTIVE_TAGS)):
        tag.decompose()

    # Only the primary document is fetched, so every referenced image 404s and
    # renders as a broken icon. Replaced with a visible marker rather than
    # removed silently: the labeler should be able to tell that something was
    # dropped, in the rare case a figure carries the value they are looking for.
    for image in soup.find_all("img"):
        image.replace_with(
            BeautifulSoup(
                f'<span class="omitted-image">[image omitted: '
                f'{(image.get("alt") or image.get("src") or "unnamed")[:60]}]</span>',
                "html.parser"))

    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            if _EVENT_ATTR.match(attribute):
                del tag[attribute]
            elif attribute.lower() in _URL_ATTRS:
                value = tag.get(attribute)
                if isinstance(value, str) and _DANGEROUS_URL.match(value):
                    del tag[attribute]

    return str(soup)


def highlight_all(html: str,
                  literals: dict[str, list[str]] | None = None,
                  ) -> tuple[str, dict[str, int]]:
    """Mark every field's candidate passages in one pass.

    `literals` carries per-filing exact strings the manifest already knows --
    the ticker symbol and the registrant name. Both were being anchored by hand
    because the patterns can only light the surrounding header (`Trading
    Symbol(s)`), never the symbol itself. Matched **case-sensitively**, unlike
    the patterns: `APP` matched case-insensitively lights every "app" in
    AppLovin's filing, which buries the one occurrence that matters just as
    effectively as showing nothing.

    All nine fields at once, each mark tagged with the fields it belongs to, so
    the document is parsed once per filing rather than once per field. That is
    the whole reason for the shape: parsing a 3 MB 10-K costs ~1.4s, and doing
    it on every field change cost 3.3s of dead time nine times per filing --
    about nineteen minutes across the corpus, and a broken rhythm on every
    single instance. The client toggles which field's marks are lit.

    Operates on text nodes only. Editing serialized HTML with a regex would
    eventually match inside an attribute value -- `title="Total assets"` -- and
    rewrite the markup around it. That does not crash; it silently changes the
    document the labeler is reading, which is the worst failure available here.

    Overlapping matches from different fields merge into a single mark carrying
    both field names, because nested marks would not survive serialization
    intact.
    """
    from bs4 import NavigableString

    compiled = {
        field: re.compile("|".join(f"(?:{p})" for p in patterns), re.I | re.M)
        for field, patterns in FIELD_PATTERNS.items() if patterns
    }

    # Case-sensitive, escaped, and blank entries dropped. An empty string
    # compiled into an alternation matches at every position, which would mark
    # the entire filing and light nothing usefully at all.
    for field, values in (literals or {}).items():
        wanted = [re.escape(v) for v in values if v and v.strip()]
        if not wanted:
            continue
        compiled[f"\x00lit:{field}"] = re.compile("|".join(wanted), re.M)
    soup = BeautifulSoup(html, "html.parser")
    counts: dict[str, int] = {}

    # Snapshot first: the tree is mutated during iteration.
    text_nodes = [node for node in soup.find_all(string=True)
                  if node.parent.name not in ("script", "style", "mark")
                  and str(node).strip()]

    for node in text_nodes:
        text = str(node)
        spans: list[list] = []
        for field, pattern in compiled.items():
            # Literal groups are keyed apart so they compile separately, but
            # they mark the same field the patterns do.
            name = field.split(":", 1)[1] if field.startswith("\x00lit:") else field
            for match in pattern.finditer(text):
                if match.end() > match.start():
                    spans.append([match.start(), match.end(), {name}])
        if not spans:
            continue

        spans.sort(key=lambda s: (s[0], -s[1]))
        merged: list[list] = []
        for span in spans:
            if merged and span[0] < merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], span[1])
                merged[-1][2] |= span[2]
            else:
                merged.append(span)

        pieces, cursor = [], 0
        for start, end, fields in merged:
            if start > cursor:
                pieces.append(NavigableString(text[cursor:start]))
            mark = soup.new_tag("mark")
            mark["class"] = "hit"
            mark["data-fields"] = " ".join(sorted(fields))
            mark.string = text[start:end]
            pieces.append(mark)
            for field in fields:
                counts[field] = counts.get(field, 0) + 1
            cursor = end
        if cursor < len(text):
            pieces.append(NavigableString(text[cursor:]))

        node.replace_with(*pieces)

    return (str(soup), counts) if counts else (html, {})
