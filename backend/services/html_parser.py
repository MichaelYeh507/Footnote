"""Text extraction from EDGAR HTML / inline XBRL filings.

EDGAR serves 10-Ks as HTML, not PDF. Since roughly 2019 the primary document is
inline XBRL: the machine-readable facts are tags wrapped around the same words a
human reads, so the prose and the structured data occupy the same elements.
Keeping the text and dropping the markup is therefore the whole job.

Two things are worth doing deliberately rather than by default:

* iXBRL filings carry a hidden block of contexts, units, and axis definitions.
  It is not prose, it never answers a question, and left in it costs thousands
  of tokens per filing.
* Filings are mostly tables. Adjacent cells must not concatenate, or a balance
  sheet row becomes "Total assets391,035" and the value is unreadable.
"""

import re
import warnings

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Some filers emit XHTML with an XML declaration. The HTML parser handles it
# correctly; the warning is noise on every single document.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

_HIDDEN_STYLE = re.compile(r"display\s*:\s*none", re.I)

# lxml lowercases and keeps the namespace prefix, so ix:header parses as a tag
# named "ix:header". Match on the suffix so a different prefix still hits.
_IXBRL_METADATA_TAG = re.compile(r"(^|:)(header|hidden)$", re.I)

# Encodings actually seen on EDGAR, in the order worth trying. latin-1 decodes
# any byte sequence, so it terminates the loop.
_ENCODINGS = ("utf-8", "cp1252", "latin-1")


def _decode(raw: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_text_from_html(file_bytes: bytes) -> str:
    """Extract readable text from an EDGAR HTML or iXBRL document."""
    soup = BeautifulSoup(_decode(file_bytes), "lxml")

    for tag in soup(["script", "style"]):
        tag.decompose()

    for tag in soup.find_all(_IXBRL_METADATA_TAG):
        tag.decompose()

    # list() first: decomposing a parent invalidates its descendants mid-iteration.
    for tag in list(soup.find_all(style=_HIDDEN_STYLE)):
        tag.decompose()

    # separator="\n" is what keeps adjacent table cells apart.
    text = soup.get_text(separator="\n")

    # Non-breaking spaces are pervasive in filing markup and are not meaningful.
    text = re.sub(r"[ \t\xa0  ]+", " ", text)
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
