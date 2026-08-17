"""Reusing last year's location without reusing last year's answer.

An issuer's two 10-Ks are near-duplicates: same layout, same section order,
`Total assets` on the same balance-sheet row. Measured on the labels so far,
the digit-stripped anchor is *identical* across the two years for the numeric
table fields -- `Total assets`, `Net sales`, `Dividends declared ( per share)`.
So the second year's hunt is wasted motion and can be skipped.

What must NOT be skipped is the read. §2 sized the corpus at 22 issuers x 2
years rather than 4 issuers x 10 years precisely because consecutive filings
from one issuer are correlated, and protocol rule 3 names the resulting
carry-over risk explicitly: the second year's locator must anchor into the
second year's document. Five of the nine fields change value every year, so a
carried-over answer is not merely weaker evidence -- it is wrong data.

The line this module draws: **the location crosses years, the value never
does.** Matching happens server-side and the response carries integers only,
so no year-one text reaches the browser at all. That matters beyond tidiness:
for `ceo_name` the anchor *is* the answer, and digit-stripping would not hide
it.
"""

import pathlib
import re
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation.labeling import best_mark_index, prior_anchor_key  # noqa: E402


# ------------------------------------------------------------ the key itself

def test_key_strips_the_value_from_a_numeric_anchor():
    assert prior_anchor_key("Total assets\t\t$\t16,524") == "total assets"


def test_key_is_identical_across_years_for_the_same_row():
    """The property the whole feature rests on, on real anchors."""
    for a, b in (("Total assets\t\t$\t16,524", "Total assets\t\t$\t17,102"),
                 ("Net sales\t\t$\t13,640", "Net sales\t\t$\t15,009"),
                 ("Dividends declared ($0.4975 per share)",
                  "Dividends declared ($0.5075 per share)")):
        assert prior_anchor_key(a) == prior_anchor_key(b), (a, b)


def test_key_keeps_no_digits_at_all():
    """A digit surviving into the key would be part of last year's answer."""
    for anchor in ("Total assets $ 16,524", "approximately 41,000 employees",
                   "Dividends declared ($0.4975 per share)", "2024-06-30"):
        assert not re.search(r"\d", prior_anchor_key(anchor)), anchor


def test_key_of_a_blank_anchor_is_empty():
    for anchor in ("", "   ", None, "$ 1,234", "12,345"):
        assert prior_anchor_key(anchor) == ""


# --------------------------------------------------------- choosing the mark

MARKS = ["Total revenues", "Total assets", "Total current assets",
         "Total assets and liabilities"]


def test_best_index_finds_the_exact_row():
    assert best_mark_index(MARKS, "total assets") == 1


def test_best_index_prefers_exact_over_containing():
    """`Total current assets` contains the key's words and is the wrong row.

    This is the failure mode that matters: the balance sheet carries several
    rows whose text is a superset of `Total assets`, and landing on a subtotal
    is exactly the trap the field guidance warns about.
    """
    assert best_mark_index(["Total current assets", "Total assets"],
                           "total assets") == 1


def test_best_index_returns_none_when_nothing_is_close():
    assert best_mark_index(["Item 5", "Signatures"], "total assets") is None


def test_best_index_handles_no_marks_and_no_key():
    assert best_mark_index([], "total assets") is None
    assert best_mark_index(MARKS, "") is None


def test_best_index_is_whitespace_and_case_insensitive():
    assert best_mark_index(["TOTAL   ASSETS"], "total assets") == 0


# ------------------------------------- phrase (anchor) vs keyword (pattern)

def test_a_mark_contained_in_the_anchor_still_matches():
    """The shapes differ, and this is the case that proves it.

    A pattern matched `goodwill was not impaired`; the labeler selected the
    whole sentence around it. Similarity alone scores that as a miss because
    the lengths are so different, so containment has to be tested explicitly.
    Real AMCR anchor.
    """
    marks = ["impairment charge", "goodwill was not impaired"]
    key = "the company concluded that goodwill was not impaired"
    assert best_mark_index(marks, key) == 1


def test_context_separates_marks_that_read_identically():
    """Thirteen marks say `chief executive officer`. Only one is the answer.

    The mark text cannot discriminate at all here -- every candidate reduces to
    the same string. The surrounding text can, because the signature page
    carries the officer's name and the cover page does not, and last year's
    anchor carried that name too.
    """
    marks = ["Chief Executive Officer"] * 3
    contexts = [
        "Item 1. Business ... our Chief Executive Officer believes ...",
        "the address of our principal executive offices Chief Executive Officer",
        "/s/ Peter Konieczny  Peter Konieczny  Chief Executive Officer  Signatures",
    ]
    key = "peter konieczny interim chief executive officer"
    assert best_mark_index(marks, key, contexts) == 2


def test_context_only_raises_a_score_never_lowers_one():
    """A correct exact mark must survive a misleading context beside it."""
    marks = ["Total revenues", "Total assets"]
    contexts = ["Total assets and liabilities of the segment", "balance sheet"]
    assert best_mark_index(marks, "total assets", contexts) == 1


def test_context_decides_between_two_identical_exact_matches():
    """`Total assets` is a row caption twice: balance sheet and segment note.

    Both are exact matches on the mark, so the mark cannot choose. Restricting
    the pool to the exact matches and ranking them on context is what keeps the
    hint off the segment table -- the trap the `total_assets` guidance names.
    """
    marks = ["Total assets", "Total assets"]
    contexts = ["Segment reporting Total assets by reportable segment",
                "CONSOLIDATED BALANCE SHEETS Total assets Total liabilities"]
    assert best_mark_index(marks, "total assets consolidated balance sheets",
                           contexts) == 1


def test_nothing_close_returns_none_even_with_contexts():
    """The floor still applies once contexts are in play.

    Without it every field would always get a hint, and a confident jump to an
    unrelated row is the one outcome worse than no jump.
    """
    assert best_mark_index(["Signatures", "Exhibit index"],
                           "total assets",
                           ["signature page", "list of exhibits"]) is None


# ------------------------------------------------------------- the leak guard

def test_hint_payload_carries_no_year_one_text():
    """The response shape is the guarantee, so assert on the shape.

    `progress_line` had the same requirement during extraction and four leak
    paths were found by perturbing it. Here the risk is worse in one respect:
    for `ceo_name` the anchor string *is* the value, so any scheme that ships
    the anchor to the client and relies on the UI not rendering it is one
    template edit away from showing last year's answer.
    """
    from evaluation.labeling import prior_hint

    prior = {"field": "ceo_name", "period": "2024-06-30",
             "value": "Peter Konieczny",
             "locator": {"anchor": "Peter Konieczny (59) Chief Executive Officer"}}
    marks = ["Peter Konieczny (60)\t\tChief Executive Officer", "Signatures"]

    hint = prior_hint(prior, marks)
    assert hint is not None
    assert set(hint) == {"index", "of", "period"}, hint
    assert hint["index"] == 0 and hint["of"] == 2

    flat = repr(hint).lower()
    for leaked in ("konieczny", "peter", "chief executive"):
        assert leaked not in flat, f"{leaked!r} reached the client in {hint}"


def test_no_prior_label_yields_no_hint():
    from evaluation.labeling import prior_hint
    assert prior_hint(None, ["Total assets"]) is None


def test_prior_label_with_no_anchor_yields_no_hint():
    """`not_addressed` records carry searched terms, not an anchor."""
    from evaluation.labeling import prior_hint
    prior = {"field": "dividends_declared_per_share", "period": "2024-12-31",
             "locator": {"searched": ["dividends", "per share"]}}
    assert prior_hint(prior, ["Dividend yield"]) is None


@pytest.mark.parametrize("field", ["company_name", "ticker", "ceo_name",
                                   "total_assets", "dividends_declared_per_share"])
def test_hint_never_returns_a_value_key_for_any_field(field):
    from evaluation.labeling import prior_hint
    prior = {"field": field, "period": "2024-06-30", "value": "SENTINEL-VALUE",
             "locator": {"anchor": "Total assets 1,000"}}
    hint = prior_hint(prior, ["Total assets 2,000"])
    assert hint is None or "SENTINEL-VALUE" not in repr(hint)
