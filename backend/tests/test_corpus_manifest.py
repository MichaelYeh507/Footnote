"""The committed corpus must match the pre-registered selection rule.

`corpus/issuers.json` and `corpus/manifest.json` are the corpus definition. They
are ordinary JSON files, so nothing stops a hand edit -- and a hand-edited corpus
silently invalidates every number computed from it, with no other symptom.

These run offline against the committed files. No network, no filings.
"""

import json
import pathlib

import pytest

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "corpus"

# Dev set: filings read while calibrating the extraction prompt. Must never
# appear in the eval corpus. Mirrors services/openai_structurer.py.
CALIBRATION_CIKS = {320193, 909832, 1058090, 19617, 200406, 34088, 18230, 753308}

EXPECTED_SECTORS = 11
PER_SECTOR = 2
OVERFLOW_THRESHOLD = 0.25


@pytest.fixture(scope="module")
def issuers():
    return json.loads((CORPUS / "issuers.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def test_every_gics_sector_has_exactly_two_issuers(issuers):
    counts: dict[str, int] = {}
    for issuer in issuers["issuers"]:
        counts[issuer["sector"]] = counts.get(issuer["sector"], 0) + 1
    assert len(counts) == EXPECTED_SECTORS, f"sectors: {sorted(counts)}"
    assert all(n == PER_SECTOR for n in counts.values()), counts


def test_no_calibration_issuer_is_in_the_corpus(issuers):
    """The dev/test split. Leakage here inflates accuracy invisibly."""
    leaked = [i["ticker"] for i in issuers["issuers"] if i["cik"] in CALIBRATION_CIKS]
    assert not leaked, f"calibration issuers in eval corpus: {leaked}"


def test_selection_is_reproducible(issuers):
    """Seed and universe snapshot pin the draw. Without both recorded, the
    corpus cannot be regenerated and 'reproducible' is an unbacked claim."""
    assert issuers["seed"] == 20260809
    assert issuers["universe"]["source"].startswith("https://")
    assert issuers["universe"]["fetched"]
    assert issuers["universe"]["count"] > 480


def test_manifest_covers_every_selected_filing(issuers, manifest):
    expected = {
        (i["ticker"], f["period"])
        for i in issuers["issuers"] for f in i["filings"]
    }
    actual = {(r["ticker"], r["period"]) for r in manifest["filings"]}
    assert actual == expected, f"missing: {expected - actual}, extra: {actual - expected}"


def test_accessions_are_unique(manifest):
    """A duplicate would double-count one filing in every per-field denominator."""
    accessions = [r["accession"] for r in manifest["filings"]]
    assert len(accessions) == len(set(accessions))


def test_two_distinct_fiscal_years_per_issuer(manifest):
    by_ticker: dict[str, set[str]] = {}
    for row in manifest["filings"]:
        by_ticker.setdefault(row["ticker"], set()).add(row["period"])
    bad = {t: sorted(p) for t, p in by_ticker.items() if len(p) != 2}
    assert not bad, f"issuers without exactly two distinct periods: {bad}"


def test_every_filing_has_an_integrity_hash(manifest):
    """EDGAR accessions are immutable, so sha256 is what makes a re-fetch
    verifiable rather than merely repeated."""
    for row in manifest["filings"]:
        assert len(row["sha256"]) == 64, row["ticker"]
        assert row["document_bytes"] > 0
        assert row["tokens"] > 0


# The corpus's one known statement-free pair, found after labeling and
# disclosed in plan §5 (CORPUS DEFECT, 2026-08-18): PGR incorporates its
# consolidated statements into Item 8 by reference from the Annual Report
# exhibit, so its primary documents carry only Schedule II parent-company
# figures. The owner relabeled the four affected instances `not_addressed`
# the same day.
DISCLOSED_STATEMENT_FREE = {"PGR 2025-12-31", "PGR 2024-12-31"}


def test_no_undisclosed_filing_is_missing_its_financial_statements(manifest):
    """Guards Item 8 incorporated by reference: the primary document is short
    enough to pass any size check while total_assets and revenue are absent.
    A filing appearing here that is not in the disclosed set means the corpus
    changed or the detector regressed; a disclosed one missing means the
    defect was papered over rather than decided."""
    defective = {
        f"{r['ticker']} {r['period']}"
        for r in manifest["filings"]
        if not (r["has_balance_sheet"] and r["has_income_statement"])
    }
    assert defective == DISCLOSED_STATEMENT_FREE, (
        f"undisclosed: {sorted(defective - DISCLOSED_STATEMENT_FREE)}, "
        f"papered over: {sorted(DISCLOSED_STATEMENT_FREE - defective)}")
    assert set(manifest["defects"]["missing_financial_statements"]) == defective


def test_the_retired_by_reference_flag_stays_retired(manifest):
    """`item_8_by_reference` was wrong in both directions on the committed
    manifest -- true for eight healthy filings via Item 3 cross-references
    into Item 8, false for the two defective PGR rows -- and prose cannot
    decide it (PG prints the same sentence as PGR while containing its
    statements). Statement presence is decided from the filer's own
    undimensioned facts; see scripts/fetch_filings.py."""
    assert "item_8_by_reference" not in manifest["defects"]
    holdouts = [r["ticker"] for r in manifest["filings"]
                if "item_8_by_reference" in r]
    assert not holdouts, f"retired field re-appeared on: {holdouts}"


def test_context_window_coverage_is_recorded_and_within_threshold(manifest):
    """Coverage is a reported number, not a filter. The threshold was
    pre-registered before any filing was fetched (plan §2)."""
    coverage = manifest["coverage"]
    assert coverage["fits"] + coverage["over_window"] == manifest["filing_count"]
    assert coverage["threshold"] == OVERFLOW_THRESHOLD
    assert not coverage["threshold_exceeded"], (
        f"{coverage['over_window']}/{manifest['filing_count']} filings exceed the "
        f"context window. Per plan §2 this makes section-targeted extraction a "
        f"Phase 2 prerequisite: {coverage['over_window_filings']}"
    )


def test_manifest_contains_no_filing_content(manifest):
    """Enforces the hard stop mechanically. Identifiers and measurements are
    committable; filing text is not, at any size."""
    longest = max(
        ((len(v), k) for row in manifest["filings"] for k, v in row.items()
         if isinstance(v, str)),
        default=(0, None),
    )
    assert longest[0] < 300, f"field {longest[1]!r} holds {longest[0]} chars of text"
