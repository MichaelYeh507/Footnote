"""Contracts for the extraction run that produces predictions.jsonl.

The run happens BEFORE labeling, and its output must not reach the labeler.
That is not a matter of care at the console: if the run prints what it
extracted, then anyone watching it has seen model output for filings they are
about to label by hand, and the labels are no longer independent. There is no
way to un-see it and no way to detect it afterward in the numbers.

So the reporting path is built to be incapable of emitting a value, and these
tests are what hold that. The rest cover the shape predictions must have for
evaluation.summarize() to align them against labels.
"""

import json
import pathlib

import pytest

from evaluation.extraction_run import (
    EVAL_FIELDS,
    completed_accessions,
    eval_filings,
    prediction_records,
    progress_line,
    prompt_fingerprint,
)

CALIBRATION_CIKS = {320193, 909832, 1058090, 19617, 200406, 34088, 18230, 753308}

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "corpus"


@pytest.fixture(scope="module")
def manifest_fixture():
    """The committed manifest. These tests assert against the real corpus."""
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def filing(ticker="ZZZ", accession="0000000000-00-000000", fits=True, tokens=50_000,
           cik=111, period="2025-12-31"):
    return {
        "ticker": ticker, "cik": cik, "accession": accession, "period": period,
        "fits_context_window": fits, "tokens": tokens, "name": f"{ticker} Inc",
        "sector": "Industrials", "primary_document": f"{ticker.lower()}.htm",
        "url": f"https://www.sec.gov/Archives/{ticker}.htm",
    }


# --- selection -------------------------------------------------------------

def test_over_window_filings_are_excluded():
    """The 5 over-window filings have nothing to compare against until a chunker
    exists, so they are not extracted and not labeled."""
    manifest = {"filings": [
        filing("AAA", "1", fits=True),
        filing("BBB", "2", fits=False, tokens=258_874),
        filing("CCC", "3", fits=True),
    ]}
    selected = eval_filings(manifest)
    assert [f["ticker"] for f in selected] == ["AAA", "CCC"]


def test_selection_matches_the_committed_manifest_count(manifest_fixture):
    """39 of 44. If this changes, the pre-registered labeling scope changed."""
    assert len(eval_filings(manifest_fixture)) == 39


def test_selection_is_deterministically_ordered(manifest_fixture):
    once = [f["accession"] for f in eval_filings(manifest_fixture)]
    twice = [f["accession"] for f in eval_filings({"filings": list(reversed(
        manifest_fixture["filings"]))})]
    assert once == twice, "run order must not depend on manifest order"


def test_both_years_of_an_issuer_are_adjacent(manifest_fixture):
    """Ordered by issuer so a resumed run stays interpretable per company."""
    tickers = [f["ticker"] for f in eval_filings(manifest_fixture)]
    first_seen = {}
    for i, ticker in enumerate(tickers):
        if ticker in first_seen and i - first_seen[ticker] != 1:
            pytest.fail(f"{ticker} filings are not adjacent: {tickers}")
        first_seen[ticker] = i


def test_no_calibration_issuer_reaches_extraction(manifest_fixture):
    """Dev/test split, enforced at the last point before an API call."""
    ciks = {f["cik"] for f in eval_filings(manifest_fixture)}
    assert not (ciks & CALIBRATION_CIKS)


# --- prediction records ----------------------------------------------------

def test_one_record_per_eval_field():
    records = prediction_records(filing(), {f: 1 for f in EVAL_FIELDS})
    assert len(records) == 9
    assert [r["field"] for r in records] == list(EVAL_FIELDS)


def test_records_carry_the_join_keys_labels_use():
    records = prediction_records(filing(accession="0000320193-25-000079"), {})
    for record in records:
        assert record["accession"] == "0000320193-25-000079"
        assert set(record) >= {"accession", "ticker", "period", "field", "value"}


def test_absent_field_becomes_null_not_dropped():
    """A missing key and an explicit null are the same prediction: the model
    returned nothing. Dropping the record instead would misalign the join."""
    records = prediction_records(filing(), {"ticker": "ZZZ"})
    values = {r["field"]: r["value"] for r in records}
    assert values["total_assets"] is None
    assert len(values) == 9


def test_extra_model_keys_are_not_carried_into_predictions():
    """The prompt returns risks, management, description. None are measured."""
    records = prediction_records(
        filing(), {"total_assets": 1.0, "risks": [{"risk_name": "x"}],
                   "description": "a business", "sector": "Tech"})
    assert {r["field"] for r in records} == set(EVAL_FIELDS)


# --- the blindness contract ------------------------------------------------

# Deliberately disjoint from the manifest identifiers below. The line is
# expected to print the filing's OWN ticker, period, and accession -- those come
# from the committed manifest, the labeler already has them, and without them a
# failure cannot be traced to a filing. What must never appear is anything the
# MODEL returned. So the sentinel ticker differs from the filing ticker: if the
# two matched, the test could not tell a leak from a legitimate identifier.
SENTINELS = {
    "company_name": "SENTINELCORP", "ticker": "SNTL",
    "fiscal_year_end": "December 31, 2999", "employees": "approximately 123456",
    "total_assets": 987654.321, "revenue_most_recent_fy": 456789.123,
    "ceo_name": "Sentinel Q Person", "dividends_declared_per_share": 7.77,
    "goodwill_impairment": 31337.0,
}


@pytest.mark.parametrize("status", ["ok", "failed", "skipped"])
def test_progress_line_never_contains_an_extracted_value(status):
    line = progress_line(filing(ticker="AAA"), status,
                         extracted=SENTINELS, detail="some detail")
    for field, value in SENTINELS.items():
        assert str(value) not in line, f"{field} value leaked into progress output"


def test_progress_line_still_reports_enough_to_diagnose():
    line = progress_line(filing(ticker="AAA", accession="0000-1"), "ok",
                         extracted=SENTINELS)
    assert "AAA" in line and "0000-1" in line


def test_progress_line_does_not_report_how_many_fields_were_populated():
    """The populated count is not mechanical once aggregated.

    Per filing "1/9 populated" looks harmless. Across 39 filings the aggregate
    IS the abstention rate -- a reported result, and the one that anchors the
    value/stated_none/not_addressed decision on the two fields the plan expects
    to be frequently absent. An earlier version of this run printed it.
    """
    one = progress_line(filing(), "ok", extracted={"total_assets": 987654.321})
    nine = progress_line(filing(), "ok", extracted=SENTINELS)
    assert one == nine, "output must not vary with how much was extracted"
    for count in ("1/9", "9/9", "1 of 9", "populated"):
        assert count not in one


def test_progress_line_is_identical_whether_or_not_extraction_happened():
    """The strongest form of the contract: the line is a function of the filing
    and the status, and of nothing the model returned."""
    assert (progress_line(filing(), "ok", extracted=SENTINELS)
            == progress_line(filing(), "ok", extracted=None))


# --- resume ----------------------------------------------------------------

def test_completed_accessions_reads_finished_filings():
    lines = [
        json.dumps({"accession": "A", "field": "ticker", "value": "X"}),
        json.dumps({"accession": "A", "field": "total_assets", "value": 1}),
        json.dumps({"accession": "B", "field": "ticker", "value": "Y"}),
    ]
    assert completed_accessions(lines) == {"A", "B"}


def test_blank_and_malformed_lines_do_not_silently_vanish():
    with pytest.raises(ValueError):
        completed_accessions(["{not json"])


def test_completed_accessions_tolerates_trailing_blank_line():
    assert completed_accessions([json.dumps({"accession": "A"}), "", "  "]) == {"A"}


# --- provenance ------------------------------------------------------------

def test_prompt_fingerprint_changes_when_the_prompt_changes():
    a = prompt_fingerprint("prompt one", "gpt-4o-mini", 0.0)
    b = prompt_fingerprint("prompt two", "gpt-4o-mini", 0.0)
    assert a != b and len(a) == 64


def test_prompt_fingerprint_changes_when_temperature_changes():
    """Predictions produced at a different temperature are a different run."""
    assert (prompt_fingerprint("p", "gpt-4o-mini", 0.0)
            != prompt_fingerprint("p", "gpt-4o-mini", 0.1))


def test_prompt_fingerprint_is_stable_across_calls():
    assert prompt_fingerprint("p", "m", 0.0) == prompt_fingerprint("p", "m", 0.0)
