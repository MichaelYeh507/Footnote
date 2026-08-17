"""Two defects a script can catch that anchor-existence checking cannot.

Both were found the same way: the labeler noticed something looked wrong and
asked. `verify_labels.py` had reported "no mechanical defects found" over both
of them, because an anchor that exists in the filing passes provenance no
matter what it says.

**1. The anchor does not support the field.** `goodwill_impairment` was labeled
`6085` from the anchor `Total goodwill $ 6,085` on both CTSH filings. That is
the carrying balance, which the field guidance names by name -- "The goodwill
carrying balance is not an impairment." The anchor is real, occurs in the right
filing, and is evidence for a different quantity entirely.

**2. A value carried across an issuer's two fiscal years.** Both CTSH labels
recorded the same number from the same anchor text, 49 minutes apart. Protocol
rule 3 names carry-over as the risk created by labeling both years
consecutively, and `/api/prior-hint` now deliberately starts the second year on
the first year's row -- which makes this check the counterweight to that
feature rather than a nicety.

Neither is proof of an error. Both are worth a human's second look, so they
report as WARN and do not fail the run.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation.labeling import (  # noqa: E402
    anchor_supports_field, carried_over_pairs, drop_labels,
    unexplained_ambiguities,
)


def label(ticker, period, field, value, anchor, kind="value", **extra):
    row = {"ticker": ticker, "period": period, "field": field, "value": value,
           "answer_kind": kind, "locator": {"anchor": anchor}}
    row.update(extra)
    return row


# ------------------------------------------- an ambiguity needs its reason

def test_ambiguous_without_a_note_is_flagged():
    """RESOLUTION 1 puts the arithmetic for a summed dividend in the note.

    A computed value marked ambiguous with no note is unauditable: the reader
    sees `3.00` anchored to a span reading `$0.75 per common share` and cannot
    tell a deliberate sum from a misread.
    """
    rows = [label("DGX", "2024-12-31", "dividends_declared_per_share", "3.00",
                  "quarterly cash dividend of $0.75 per common share",
                  ambiguous=True, note="")]
    assert unexplained_ambiguities(rows) == [("DGX", "2024-12-31",
                                              "dividends_declared_per_share")]


def test_ambiguous_with_a_note_is_accepted():
    rows = [label("DGX", "2024-12-31", "dividends_declared_per_share", "3.00",
                  "quarterly cash dividend of $0.75 per common share",
                  ambiguous=True, note="4 quarters x $0.75 stated in Item 5")]
    assert unexplained_ambiguities(rows) == []


def test_a_note_is_only_required_when_ambiguous_is_set():
    """Most labels are unambiguous and owe no explanation."""
    rows = [label("XYZ", "2024-12-31", "total_assets", "1", "Total assets 1")]
    assert unexplained_ambiguities(rows) == []


def test_whitespace_is_not_a_note():
    rows = [label("XYZ", "2024-12-31", "total_assets", "1", "Total assets 1",
                  ambiguous=True, note="   ")]
    assert len(unexplained_ambiguities(rows)) == 1


# ------------------------------------------- the anchor must fit the field

def test_goodwill_carrying_balance_is_rejected():
    """The real defect, verbatim from the labels file."""
    assert not anchor_supports_field("goodwill_impairment", "value",
                                     "Total goodwill\t\t$\t6,085")


def test_goodwill_charge_is_accepted():
    assert anchor_supports_field(
        "goodwill_impairment", "value",
        "recorded a non-cash goodwill impairment charge of $188.9 million")


@pytest.mark.parametrize("anchor", [
    "the Company concluded that goodwill was not impaired",
    "No goodwill impairment was recorded for any period presented.",
    "there were no impairments of goodwill",
])
def test_statements_that_none_occurred_are_accepted(anchor):
    """`stated_none` is a claim about text, so its anchor is checked too."""
    assert anchor_supports_field("goodwill_impairment", "stated_none", anchor)


def test_dividend_anchor_must_mention_dividends():
    assert not anchor_supports_field("dividends_declared_per_share", "value",
                                     "Total assets $ 16,524")
    assert anchor_supports_field("dividends_declared_per_share", "value",
                                 "Dividends declared ($0.4975 per share)")
    assert anchor_supports_field("dividends_declared_per_share", "stated_none",
                                 "Dividend yield 0%")


def test_total_assets_anchor_must_mention_assets():
    assert not anchor_supports_field("total_assets", "value",
                                     "Total goodwill $ 6,085")
    assert anchor_supports_field("total_assets", "value",
                                 "Total assets\t\t$\t16,524")


def test_fields_with_no_rule_are_left_alone():
    """Only fields with a defensible keyword get a rule.

    `ceo_name` and `company_name` are names; there is no word that must appear
    beside them, and inventing one would fail correct labels. A check that
    cries wolf sends the labeler back over work that was already right.
    """
    for field in ("ceo_name", "company_name", "ticker", "fiscal_year_end"):
        assert anchor_supports_field(field, "value", "anything at all")


def test_not_addressed_is_never_flagged():
    """`not_addressed` carries searched terms, not an anchor into the text."""
    assert anchor_supports_field("goodwill_impairment", "not_addressed", "")


# --------------------------------------------------- carry-over between years

def test_identical_value_and_anchor_across_years_is_flagged():
    rows = [label("CTSH", "2024-12-31", "goodwill_impairment", "6085",
                  "Total goodwill\t\t$\t6,085"),
            label("CTSH", "2025-12-31", "goodwill_impairment", "6085",
                  "Total goodwill\t\t$\t6,085")]
    assert carried_over_pairs(rows) == [("CTSH", "goodwill_impairment")]


def test_a_genuinely_unchanged_value_with_a_different_anchor_is_not_flagged():
    """Values legitimately repeat. Anchors copied verbatim are the signal.

    A company can report the same headcount two years running. What it does not
    do is print the identical sentence with identical spacing in both filings
    -- and if it did, the labeler still read it twice.
    """
    rows = [label("XYZ", "2024-12-31", "employees", "41000",
                  "As of December 31, 2024, we had approximately 41,000"),
            label("XYZ", "2025-12-31", "employees", "41000",
                  "As of December 31, 2025, we had approximately 41,000")]
    assert carried_over_pairs(rows) == []


def test_stable_fields_are_exempt():
    """`ticker` and `company_name` SHOULD be identical across both years.

    Flagging them would bury the real signal under two guaranteed false alarms
    per issuer -- 44 of them across the corpus.
    """
    rows = [label("XYZ", "2024-12-31", "ticker", "XYZ", "XYZ"),
            label("XYZ", "2025-12-31", "ticker", "XYZ", "XYZ"),
            label("XYZ", "2024-12-31", "company_name", "XYZ Inc", "XYZ Inc"),
            label("XYZ", "2025-12-31", "company_name", "XYZ Inc", "XYZ Inc")]
    assert carried_over_pairs(rows) == []


def test_absent_kinds_are_exempt():
    """Two years of `stated_none` on the same anchor text is normal.

    A filing that says "goodwill was not impaired" says it the same way every
    year, and both are correct labels of a real absence.
    """
    rows = [label("XYZ", "2024-12-31", "goodwill_impairment", None,
                  "goodwill was not impaired", kind="stated_none"),
            label("XYZ", "2025-12-31", "goodwill_impairment", None,
                  "goodwill was not impaired", kind="stated_none")]
    assert carried_over_pairs(rows) == []


def test_one_year_only_is_not_flagged():
    rows = [label("XYZ", "2024-12-31", "total_assets", "100", "Total assets 100")]
    assert carried_over_pairs(rows) == []


# ------------------------------------------------------ targeted redo

ROWS = [
    label("CTSH", "2024-12-31", "goodwill_impairment", "6085", "Total goodwill"),
    label("CTSH", "2025-12-31", "goodwill_impairment", "6085", "Total goodwill"),
    label("CTSH", "2024-12-31", "total_assets", "1", "Total assets"),
    label("DGX", "2024-12-31", "goodwill_impairment", "2", "goodwill impairment"),
]


def test_drop_selects_one_field_across_both_years():
    kept, removed = drop_labels(ROWS, "CTSH", "goodwill_impairment")
    assert len(removed) == 2 and len(kept) == 2
    assert {r["ticker"] for r in removed} == {"CTSH"}
    assert all(r["field"] == "goodwill_impairment" for r in removed)


def test_drop_can_narrow_to_one_period():
    _kept, removed = drop_labels(ROWS, "CTSH", "goodwill_impairment", "2025-12-31")
    assert len(removed) == 1 and removed[0]["period"] == "2025-12-31"


def test_drop_without_a_field_takes_the_whole_issuer():
    kept, removed = drop_labels(ROWS, "CTSH")
    assert len(removed) == 3
    assert {r["ticker"] for r in kept} == {"DGX"}


def test_drop_never_touches_another_issuer():
    """The check that matters: a redo must not silently shrink the corpus."""
    kept, _removed = drop_labels(ROWS, "CTSH", "goodwill_impairment")
    assert any(r["ticker"] == "DGX" for r in kept)


def test_drop_preserves_order_and_loses_nothing():
    kept, removed = drop_labels(ROWS, "CTSH", "goodwill_impairment")
    assert len(kept) + len(removed) == len(ROWS)
    assert kept == [r for r in ROWS if r in kept]


def test_drop_on_no_match_returns_everything():
    kept, removed = drop_labels(ROWS, "NOPE", "goodwill_impairment")
    assert removed == [] and len(kept) == len(ROWS)
