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
        "consolidated statement. REPORT IN MILLIONS.",
    "revenue_most_recent_fy":
        "Top-line total from the consolidated statement of operations. Labels "
        "vary: Total net sales, Total revenues, Total sales and revenues, Total "
        "net revenue. TRAP: two or three years sit side by side -- confirm the "
        "column header rather than assuming the first column. Do not use a "
        "total folding in non-operating income. REPORT IN MILLIONS.",
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
        "MILLIONS. States none occurred -> stated none. Never addressed -> not "
        "addressed.",
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


def highlight(html: str, field: str) -> tuple[str, int]:
    """Wrap this field's candidate passages in <mark id="hit-N">.

    Operates on text nodes only. Editing the serialized HTML with a regex would
    eventually match inside an attribute value -- `title="Total assets"` -- and
    rewrite the markup around it. That does not crash; it silently changes the
    document the labeler is reading, which is the worst available failure here.
    """
    patterns = FIELD_PATTERNS.get(field, ())
    if not patterns:
        return html, 0

    combined = re.compile("|".join(f"(?:{p})" for p in patterns), re.I)
    soup = BeautifulSoup(html, "html.parser")
    counter = 0

    from bs4 import NavigableString

    # Snapshot first: the tree is mutated during iteration.
    text_nodes = [node for node in soup.find_all(string=True)
                  if node.parent.name not in ("script", "style", "mark")
                  and str(node).strip()]

    for node in text_nodes:
        text = str(node)
        if not combined.search(text):
            continue

        pieces, cursor = [], 0
        for match in combined.finditer(text):
            if match.start() > cursor:
                pieces.append(NavigableString(text[cursor:match.start()]))
            mark = soup.new_tag("mark")
            mark["id"] = f"hit-{counter}"
            mark["class"] = "hit"
            mark.string = match.group(0)
            pieces.append(mark)
            counter += 1
            cursor = match.end()
        if cursor < len(text):
            pieces.append(NavigableString(text[cursor:]))

        node.replace_with(*pieces)

    return (str(soup), counter) if counter else (html, 0)
