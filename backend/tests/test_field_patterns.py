"""The highlighter must light the places a labeler actually needs to look.

Written 2026-08-17 after measuring the gap rather than assuming it: of the 43
anchors chosen by hand in the first two sittings, **18 were never lit by the
highlighter**. The labeler had been finding them with Ctrl+F, which is the
finder's job done manually on 42% of instances.

The specific case that prompted this: `dividends_declared_per_share` is
reported by Amcor as `Dividends declared ($0.4975 per share)`. None of the
three original patterns match that string -- `dividends? declared per` wants
"declared per", and the money amount sits in between. So the one field with the
most award for being found was invisible, and a miss there does not merely cost
time: it converts a real value into a wrong `not_addressed`, which lands
directly on the false-extraction denominator.

Every form below was observed in a real filing in this corpus or is a standard
alternative for the same line. Sixth instance of the standing rule -- suspect
the pattern before the data.
"""

import pathlib
import re
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation.labeling import FIELD_PATTERNS  # noqa: E402
from evaluation.label_view import highlight_all  # noqa: E402


def matches(field: str, text: str) -> bool:
    return any(re.search(p, text, re.I | re.M)
               for p in FIELD_PATTERNS.get(field, ()))


# ---------------------------------------------------------------- must match

DIVIDEND_FORMS = (
    "Dividends declared ($0.4975 per share)",          # AMCR FY2024, real anchor
    "Dividends declared ($0.5075 per share)",          # AMCR FY2025, real anchor
    "Dividend yield 0%",                               # APP, real anchor
    "Cash dividends declared per common share",
    "Dividends declared per common share",
    "Dividends per ordinary share",                    # plc / foreign filers
    "Dividends declared and paid per share",
    "Dividends per Class A common share",
    "Distributions declared per unit",                 # REITs, partnerships
    "$0.4975 per share",
    "We have never declared or paid any cash dividends",
    "no dividends were declared",
)

REVENUE_FORMS = (
    "Net sales $ 13,640",                              # AMCR, real anchor
    "Revenue $ 4,709,248",                             # APP, real anchor
    "REVENUES $ 55,085",                               # CHTR, real anchor
    "Total net sales",
    "Total revenues",
    "Total sales and revenues",
)

COMPANY_FORMS = (
    "AppLovin Corporation",                            # APP, real anchor
    "AMCOR PLC",
    "Charter Communications, Inc.",
    "exact name of registrant as specified in its charter",
)


@pytest.mark.parametrize("text", DIVIDEND_FORMS)
def test_dividend_forms_are_visible(text):
    assert matches("dividends_declared_per_share", text), (
        f"a labeler looking for the dividend would not be shown {text!r}")


@pytest.mark.parametrize("text", REVENUE_FORMS)
def test_revenue_forms_are_visible(text):
    assert matches("revenue_most_recent_fy", text), (
        f"the income-statement top line {text!r} is not lit")


@pytest.mark.parametrize("text", COMPANY_FORMS)
def test_company_name_forms_are_visible(text):
    assert matches("company_name", text), f"{text!r} is not lit"


# ------------------------------------------------------------ must not match

def test_dividend_pattern_ignores_bare_per_share_metrics():
    """`earnings per share` is on every income statement and is not a dividend.

    The dividend patterns are deliberately wide -- a miss costs a wrong label,
    while a spurious hit costs one keypress -- but wide is not unbounded.
    """
    for text in ("Earnings per share", "Diluted net income per share",
                 "Weighted average shares outstanding"):
        assert not matches("dividends_declared_per_share", text), text


def test_dividend_pattern_ignores_par_value():
    """`par value $0.01 per share` is share capital, never a dividend.

    It sits on every cover page, and CHTR FY2024's merger description repeats
    it five times. Left in, it accounted for 5 of 12 hits on that filing and
    pushed the sentence that decides the label to the bottom of the cycle.
    """
    for text in ("par value $0.01 per share",
                 "common stock, par value $0.001 per share",
                 "preferred stock, par value of $0.01 per share"):
        assert not matches("dividends_declared_per_share", text), text
    # The exclusion must not swallow a real per-share amount.
    assert matches("dividends_declared_per_share", "$0.4975 per share")


def test_dividend_pattern_ignores_earnings_per_share():
    """`per diluted share` is earnings, never a dividend.

    Measured on DGX FY2024: 11 of 21 dividend highlights were EPS figures from
    the MD&A, which pushed the four real per-share dividend rates into a crowd.
    Burying the candidates is the same failure as showing none.
    """
    for text in ("$0.84 per diluted share", "$0.42 per diluted share",
                 "$1.10 per basic share", "$2.00 per diluted common share"):
        assert not matches("dividends_declared_per_share", text), text
    # The real forms must survive the exclusion.
    for text in ("$0.75 per common share", "$0.80 per share",
                 "$0.4975 per share"):
        assert matches("dividends_declared_per_share", text), text


def test_revenue_pattern_ignores_deferred_and_segment_language():
    for text in ("Deferred revenue", "Cost of revenue"):
        assert not matches("revenue_most_recent_fy", text), text


# -------------------------------------------- per-filing literals (ticker)

def test_literals_light_the_ticker_symbol():
    """The ticker's own text is the anchor a labeler picks, not the header.

    `AMCR`, `APP` and `CHTR` were all anchored to the bare symbol, and the
    patterns only ever lit `trading symbol` / `title of each class`. The symbol
    is known per filing from the manifest, so it is passed in as a literal.
    """
    html = "<p>Trading Symbol(s)</p><table><tr><td>AMCR</td></tr></table>"
    marked, counts = highlight_all(html, literals={"ticker": ["AMCR"]})
    assert counts.get("ticker", 0) >= 2, counts
    assert "<mark" in marked


def test_literals_are_case_sensitive():
    """`APP` must not light every "app" in the prose.

    AppLovin's filing says "app" and "apps" constantly. Matching the symbol
    case-insensitively would bury the one occurrence that matters under
    hundreds that do not, which is the same failure as showing nothing.
    """
    html = "<p>our app and the app store and applications</p><td>APP</td>"
    _marked, counts = highlight_all(html, literals={"ticker": ["APP"]})
    assert counts.get("ticker", 0) == 1, (
        f"expected only the uppercase symbol to light, got {counts}")


def test_literals_are_regex_escaped():
    """Punctuation in a symbol must be literal, not a metacharacter.

    `Charter Communications, Inc.` is a poor probe -- unescaped, its `.` still
    matches its own text, so the test passes either way and proves nothing.
    A dotted class symbol is the case that separates them: unescaped, `BRK.B`
    matches `BRK-B` too, and a labeler would be shown the wrong share class.
    """
    html = "<td>BRK-B</td><td>BRK.B</td>"
    _marked, counts = highlight_all(html, literals={"ticker": ["BRK.B"]})
    assert counts.get("ticker", 0) == 1, (
        f"expected only the literal BRK.B to light, got {counts}")


def test_literals_are_optional():
    """The old two-argument call must keep working -- label_filings.py uses it."""
    marked, counts = highlight_all("<p>Total assets 1,000</p>")
    assert counts.get("total_assets", 0) == 1, counts
    assert "<mark" in marked


def test_blank_literals_do_not_match_everything():
    """A whitespace literal would light every gap between words.

    `""` alone is a weak probe: a zero-width match is already dropped by the
    span filter, so the test passes with or without the guard. A single space
    is the real hazard -- unfiltered it compiles into the alternation and marks
    every space in a 3 MB filing, which is indistinguishable from the tool
    being broken.
    """
    html = "<p>nothing of interest here at all</p>"
    _marked, counts = highlight_all(html, literals={"ticker": ["", " "]})
    assert counts.get("ticker", 0) == 0, (
        f"a blank literal lit {counts.get('ticker', 0)} spans")
