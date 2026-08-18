"""Cross-checking a hand label against the filer's own tagged facts.

`verify_labels.py` checks that a record is well-formed and that its anchor
occurs in its own filing. Neither can tell that a value is the **wrong year's
figure**, which got through twice on DGX: `3.20` in the FY2024 filing and
`3.44` in FY2025 are both real tagged numbers, both anchored in real text, and
both belong to the following fiscal year.

Every case below is drawn from a filing in this corpus.
"""

import pathlib
import re
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation.field_audit import (  # noqa: E402
    audit_verdict, close, is_fiscal_year, is_quarter_within, undimensioned,
)

FY = {"start": "2024-01-01", "end": "2024-12-31"}
PRIOR = {"start": "2023-01-01", "end": "2023-12-31"}
NEXT = {"start": "2025-01-01", "end": "2025-12-31"}
Q4 = {"start": "2024-10-01", "end": "2024-12-31"}


def fact(text, period, value=None, dims=(), instant=None):
    row = {"name": "us-gaap:X", "text": text,
           "value": float(text.replace(",", "")) if value is None else value,
           "unit": "usd", "instant": instant, "start": None, "end": None,
           "dims": list(dims), "offset": 0}
    row.update(period or {})
    return row


def label(value=None, kind="value"):
    return {"answer_kind": kind, "value": value, "ambiguous": False, "note": ""}


# ------------------------------------------------------------- period logic

def test_a_full_year_ending_on_the_period_end_is_the_fiscal_year():
    assert is_fiscal_year(fact("1", FY), "2024-12-31", "duration")


def test_a_quarter_is_not_the_fiscal_year():
    assert not is_fiscal_year(fact("1", Q4), "2024-12-31", "duration")


def test_next_years_annualised_scenario_is_not_the_fiscal_year():
    """The DGX defect in one assertion."""
    assert not is_fiscal_year(fact("3.44", NEXT), "2024-12-31", "duration")


def test_an_instant_matches_only_on_the_period_end():
    balance = fact("1", None, instant="2024-12-31")
    assert is_fiscal_year(balance, "2024-12-31", "instant")
    assert not is_fiscal_year(balance, "2023-12-31", "instant")


def test_a_duration_is_never_accepted_where_an_instant_is_wanted():
    assert not is_fiscal_year(fact("1", FY), "2024-12-31", "instant")


def test_quarters_are_recognised_inside_the_fiscal_year_only():
    assert is_quarter_within(fact("0.75", Q4), "2024-12-31")
    assert not is_quarter_within(fact("0.75", {"start": "2025-10-01",
                                               "end": "2025-12-31"}), "2024-12-31")


def test_undimensioned_keeps_only_the_consolidated_facts():
    consolidated = fact("100", FY)
    segment = fact("40", FY, dims=["StatementBusinessSegmentsAxis"])
    assert undimensioned([consolidated, segment]) == [consolidated]


def test_close_uses_a_relative_tolerance():
    assert close(1_000_000, 1_000_500)
    assert not close(1_000_000, 1_100_000)


# ----------------------------------------------------------------- verdicts

def test_matching_the_fiscal_year_fact_is_ok():
    code, _ = audit_verdict(label("1.20"), [fact("1.20", FY)], "2024-12-31")
    assert code == "OK"


def test_formatting_differences_do_not_matter():
    code, _ = audit_verdict(label("1.2"), [fact("1.20", FY)], "2024-12-31")
    assert code == "OK"


def test_a_figure_from_the_following_year_is_flagged():
    code, detail = audit_verdict(
        label("3.44"), [fact("0.80", Q4), fact("3.44", NEXT)], "2024-12-31")
    assert code == "PERIOD"
    assert "2025" in detail


def test_a_prior_year_comparative_is_flagged():
    code, _ = audit_verdict(label("6.00"),
                            [fact("6.48", FY), fact("6.00", PRIOR)], "2024-12-31")
    assert code == "PERIOD"


def test_a_dimensioned_figure_is_reported_as_such():
    """DOW tags Assets for the parent and for its subsidiary at one instant."""
    code, detail = audit_verdict(
        label("57000"),
        [fact("60000", None, value=60_000e6, instant="2024-12-31"),
         fact("57000", None, value=57_000e6, instant="2024-12-31",
              dims=["LegalEntityAxis"])],
        "2024-12-31", field="total_assets")
    assert code == "DIMS"
    assert "LegalEntityAxis" in detail


def test_four_times_a_quarterly_rate_is_recognised():
    """RESOLUTION 1: summing stated quarterly declarations is permitted."""
    code, detail = audit_verdict(label("3.00"), [fact("0.75", Q4)], "2024-12-31")
    assert code == "OK-SUM"
    assert "0.75" in detail


def test_a_sum_that_does_not_divide_evenly_is_not_claimed_as_one():
    code, _ = audit_verdict(label("3.10"), [fact("0.75", Q4)], "2024-12-31")
    assert code != "OK-SUM"


DVN_QUARTERS = [
    fact("0.44", {"start": "2024-01-01", "end": "2024-03-31"}),
    fact("0.35", {"start": "2024-04-01", "end": "2024-06-30"}),
    fact("0.44", {"start": "2024-07-01", "end": "2024-09-30"}),
    fact("0.22", {"start": "2024-10-01", "end": "2024-12-31"}),
]


def test_quarters_that_differ_are_summed():
    """Devon's fixed-plus-variable dividend changes every quarter.

    No single rate times four can match, so the four stated amounts are added.
    RESOLUTION 1 says "sum the ones the filing states", which covers a varying
    rate exactly as it covers a constant one.
    """
    code, detail = audit_verdict(label("1.45"), DVN_QUARTERS, "2024-12-31")
    assert code == "OK-SUM"
    assert "4 stated quarterly" in detail


def test_a_quarter_tagged_twice_is_counted_once():
    """The same quarter appears in the MD&A and again in the note."""
    doubled = DVN_QUARTERS + list(DVN_QUARTERS)
    code, _ = audit_verdict(label("1.45"), doubled, "2024-12-31")
    assert code == "OK-SUM"


def test_a_single_quarter_is_never_read_as_a_year():
    """One quarterly amount labelled as the annual figure must still flag."""
    code, _ = audit_verdict(label("0.44"), DVN_QUARTERS, "2024-12-31")
    assert code != "OK-SUM"


def test_next_years_announced_rate_is_not_summed_in():
    """A subsequent-event quarter carries an axis and is excluded."""
    quarters = DVN_QUARTERS + [
        fact("0.24", {"start": "2025-01-01", "end": "2025-03-31"},
             dims=["SubsequentEventTypeAxis"])]
    code, _ = audit_verdict(label("1.45"), quarters, "2024-12-31")
    assert code == "OK-SUM"


def test_a_value_matching_nothing_is_flagged_with_what_was_tagged():
    code, detail = audit_verdict(label("0.01"), [fact("6.48", FY)], "2024-12-31")
    assert code == "DIFFERS"
    assert "6.48" in detail


def test_nothing_tagged_is_unverified_not_wrong():
    """Filers tag inconsistently; a prose-only figure is untagged."""
    code, _ = audit_verdict(label("3.00"), [], "2024-12-31")
    assert code == "UNVERIFIED"


def test_absent_while_tagged_is_the_worst_case():
    """It moves a real value into the false-extraction denominator."""
    code, _ = audit_verdict(label(None, kind="stated_none"),
                            [fact("6.48", FY)], "2024-12-31")
    assert code == "ABSENT-BUT-TAGGED"


def test_a_tagged_zero_does_not_contradict_stated_none():
    code, _ = audit_verdict(label(None, kind="stated_none"),
                            [fact("0", FY, value=0.0)], "2024-12-31")
    assert code == "ABSENT-OK"


def test_absent_with_nothing_tagged_is_consistent():
    code, _ = audit_verdict(label(None, kind="not_addressed"), [], "2024-12-31")
    assert code == "ABSENT-OK"


def test_unlabelled_instances_report_as_missing():
    code, _ = audit_verdict(None, [fact("6.48", FY)], "2024-12-31")
    assert code == "MISSING"


def test_ceo_name_reports_that_no_concept_exists():
    """US GAAP does not tag officer names. Say so rather than guess."""
    code, detail = audit_verdict(label("Peter Konieczny"), [], "2024-12-31",
                                 field="ceo_name")
    assert code == "NO-CONCEPT"
    assert "no XBRL concept" in detail


# ------------------------------------------------------------ money scaling

def test_money_labels_are_compared_in_millions():
    """Labels store millions; XBRL reports actual currency units."""
    code, _ = audit_verdict(label("16524"),
                            [fact("16524", None, value=16_524_000_000.0,
                                  instant="2024-12-31")],
                            "2024-12-31", field="total_assets")
    assert code == "OK"


def test_a_thousandfold_scale_slip_is_caught():
    """The 1000x error the scale declaration exists to prevent."""
    code, _ = audit_verdict(label("16524000"),
                            [fact("16524", None, value=16_524_000_000.0,
                                  instant="2024-12-31")],
                            "2024-12-31", field="total_assets")
    assert code == "DIFFERS"


def test_per_share_values_are_not_scaled():
    code, _ = audit_verdict(label("6.48"), [fact("6.48", FY)], "2024-12-31")
    assert code == "OK"


@pytest.mark.parametrize("junk", ["n/a", "", "see note"])
def test_a_non_numeric_label_never_crashes(junk):
    code, _ = audit_verdict(label(junk), [fact("6.48", FY)], "2024-12-31")
    assert code in {"DIFFERS", "UNVERIFIED"}


# ------------------------------------------- what the app is allowed to show

def test_a_smaller_revenue_concept_is_reported_not_passed():
    """EXR tags two consolidated revenue figures 27x apart.

    `Revenues` is $3,256,902,000 while
    `RevenueFromContractWithCustomerExcludingAssessedTax` is $120,855,000,
    because property rental is lease revenue under ASC 842 and only management
    fees are contract revenue under ASC 606. Matching *any* tagged concept
    would accept the subtotal as the income-statement top line.
    """
    facts = [
        {"name": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
         "text": "120,855", "value": 120_855_000.0, "unit": "usd",
         "instant": None, "start": "2024-01-01", "end": "2024-12-31",
         "dims": [], "offset": 0},
        {"name": "us-gaap:Revenues", "text": "3,256,902",
         "value": 3_256_902_000.0, "unit": "usd", "instant": None,
         "start": "2024-01-01", "end": "2024-12-31", "dims": [], "offset": 0},
    ]
    subtotal, _ = audit_verdict(label("120.855"), facts, "2024-12-31",
                                field="revenue_most_recent_fy")
    assert subtotal == "CONCEPT-SPLIT"

    topline, _ = audit_verdict(label("3256.902"), facts, "2024-12-31",
                               field="revenue_most_recent_fy")
    assert topline == "OK"


def test_one_revenue_concept_alone_is_never_split():
    """GWW tags exactly one consolidated revenue fact and must pass cleanly."""
    facts = [{"name": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
              "text": "17,168", "value": 17_168_000_000.0, "unit": "usd",
              "instant": None, "start": "2024-01-01", "end": "2024-12-31",
              "dims": [], "offset": 0}]
    code, _ = audit_verdict(label("17168"), facts, "2024-12-31",
                            field="revenue_most_recent_fy")
    assert code == "OK"


def test_the_in_app_hint_never_carries_a_figure():
    """The banner sends the labeler back to the filing; it never answers.

    A hint carrying the tagged value would make every instance "type something
    and let the app correct it", and the labels would be XBRL-derived rather
    than read -- the LLM-assisted labeling §5 rejects, arriving by side door.
    """
    from evaluation.field_audit import audit_hint

    for code in ("PERIOD", "DIMS", "DIFFERS", "ABSENT-BUT-TAGGED",
                 "CONCEPT-SPLIT"):
        hint = audit_hint(code)
        assert hint, code
        assert not re.search(r"\d", hint), f"{code} hint leaks a digit: {hint!r}"


def test_no_hint_is_offered_when_nothing_is_wrong():
    from evaluation.field_audit import audit_hint

    for code in ("OK", "OK-SUM", "ABSENT-OK", "UNVERIFIED", "NO-CONCEPT",
                 "MISSING"):
        assert audit_hint(code) is None, code


def test_every_flagged_verdict_has_a_hint():
    """A verdict the app cannot explain would surface as an empty banner."""
    from evaluation.field_audit import AUDIT_HINTS

    for code in ("PERIOD", "DIMS", "DIFFERS", "ABSENT-BUT-TAGGED",
                 "CONCEPT-SPLIT"):
        assert code in AUDIT_HINTS
