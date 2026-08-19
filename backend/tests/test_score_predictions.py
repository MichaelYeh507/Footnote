"""The scoring runner: grid validation, key-joined alignment, report rendering.

The runner is the last piece of code between the frozen labels and the
published numbers, and its failure modes are silent ones: a positional zip
that pairs AAPL's label with AMCR's prediction scores cleanly and reports a
wrong number; a missing prediction record shifts nothing visibly; a report
that prints a sub-gate field publishes an interval too wide to mean anything.
Every test here exists to make one of those failures loud.

Written before scripts/score_predictions.py existed (red first).
"""

import hashlib
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from evaluation.extraction_run import EVAL_FIELDS  # noqa: E402

import score_predictions as runner  # noqa: E402

# --------------------------------------------------------------- fixtures
#
# Two in-window filings x 9 fields = an 18-pair grid, plus one over-window
# filing that must never enter it. Values are chosen so every outcome on the
# grid appears at least once, and so the two filings disagree on every numeric
# field -- a misalignment cannot accidentally score as correct.

ACC_A, ACC_B, ACC_OVER = "0001-25-000001", "0002-25-000002", "0009-25-000009"


def make_manifest():
    return {"filings": [
        {"accession": ACC_A, "ticker": "AAA", "period": "2025-12-31",
         "fits_context_window": True},
        {"accession": ACC_B, "ticker": "BBB", "period": "2025-06-30",
         "fits_context_window": True},
        {"accession": ACC_OVER, "ticker": "ZZZ", "period": "2025-12-31",
         "fits_context_window": False},
    ]}


_LABEL_VALUES = {
    ACC_A: {
        "company_name": ("value", "Alpha Industries Inc"),
        "ticker": ("value", "AAA"),
        "fiscal_year_end": ("value", "2025-12-31"),
        "employees": ("value", 41000),
        "total_assets": ("value", 16524.0),
        "revenue_most_recent_fy": ("value", 13640.0),
        "ceo_name": ("value", "Jane Q. Smith"),
        "dividends_declared_per_share": ("value", 3.0),
        "goodwill_impairment": ("stated_none", None),
    },
    ACC_B: {
        "company_name": ("value", "Beta Corp"),
        "ticker": ("value", "BBB"),
        "fiscal_year_end": ("value", "2025-06-30"),
        "employees": ("value", 7300),
        "total_assets": ("value", 99881.0),
        "revenue_most_recent_fy": ("value", 45210.0),
        "ceo_name": ("value", "Robert Jones"),
        "dividends_declared_per_share": ("not_addressed", None),
        "goodwill_impairment": ("not_addressed", None),
    },
}

# Predictions: AAA is scored entirely correct; BBB carries one of every
# failure -- a wrong value, a wrong name, a false extraction against
# not_addressed, a correct abstention, and one missed field.
_PREDICTED = {
    ACC_A: {
        "company_name": "Alpha Industries, Inc.",
        "ticker": "aaa",
        "fiscal_year_end": "December 31, 2025",
        "employees": "approximately 41,000",
        "total_assets": 16524.0,
        "revenue_most_recent_fy": 13640.0,
        "ceo_name": "Jane Smith",
        "dividends_declared_per_share": 3.0,
        "goodwill_impairment": 0.0,
    },
    ACC_B: {
        "company_name": "Beta Corporation",
        "ticker": "BBB",
        "fiscal_year_end": "2025-06-30",
        "employees": None,               # missed
        "total_assets": 12345.0,         # wrong value
        "revenue_most_recent_fy": 45210.0,
        "ceo_name": "William Jones",     # name mismatch
        "dividends_declared_per_share": 1.10,  # false extraction
        "goodwill_impairment": None,     # correct abstention
    },
}

_TICKER = {ACC_A: "AAA", ACC_B: "BBB"}
_PERIOD = {ACC_A: "2025-12-31", ACC_B: "2025-06-30"}


def label_row(accession, field, ambiguous=False, **overrides):
    kind, value = _LABEL_VALUES[accession][field]
    row = {"accession": accession, "ticker": _TICKER[accession],
           "period": _PERIOD[accession], "field": field, "status": "labeled",
           "answer_kind": kind, "value": value,
           "locator": {"section": "s", "anchor": "a", "searched": []},
           "ambiguous": ambiguous, "note": "", "schema_version": 1}
    row.update(overrides)
    return row


def pred_row(accession, field, **overrides):
    row = {"accession": accession, "ticker": _TICKER[accession],
           "period": _PERIOD[accession], "field": field,
           "value": _PREDICTED[accession][field]}
    row.update(overrides)
    return row


def make_labels():
    return [label_row(acc, f) for acc in (ACC_A, ACC_B) for f in EVAL_FIELDS]


def make_predictions():
    return [pred_row(acc, f) for acc in (ACC_A, ACC_B) for f in EVAL_FIELDS]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


@pytest.fixture
def corpus(tmp_path):
    """A complete, clean synthetic corpus on disk; tests break it from here."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(make_manifest()), encoding="utf-8")
    labels = write_jsonl(tmp_path / "labels.jsonl", make_labels())
    predictions = write_jsonl(tmp_path / "predictions.jsonl", make_predictions())
    return {"manifest": manifest, "labels": labels, "predictions": predictions,
            "tmp": tmp_path}


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_main(corpus, *extra):
    return runner.main([
        "--labels", str(corpus["labels"]),
        "--predictions", str(corpus["predictions"]),
        "--manifest", str(corpus["manifest"]),
        "--labels-sha256", sha256_of(corpus["labels"]),
        *extra,
    ])


# --------------------------------------------------------- grid validation

class TestGridValidation:
    def grid(self):
        return runner.expected_grid(make_manifest())

    def test_expected_grid_is_in_window_filings_times_fields(self):
        grid = self.grid()
        assert len(grid) == 2 * len(EVAL_FIELDS)
        assert (ACC_A, "total_assets") in grid

    def test_over_window_filings_are_not_in_the_grid(self):
        """The five over-window filings were never extracted or labeled; a
        grid that expects them would report 45 phantom gaps."""
        assert not any(acc == ACC_OVER for acc, _ in self.grid())

    def test_clean_grid_has_no_gaps(self):
        assert runner.grid_gaps(self.grid(), make_labels(),
                                make_predictions()) == []

    def test_missing_prediction_is_a_named_gap(self):
        preds = [p for p in make_predictions()
                 if not (p["accession"] == ACC_B and p["field"] == "employees")]
        gaps = runner.grid_gaps(self.grid(), make_labels(), preds)
        assert any("employees" in g and ACC_B in g for g in gaps)

    def test_extra_prediction_is_a_named_gap(self):
        preds = make_predictions() + [{
            "accession": ACC_OVER, "ticker": "ZZZ", "period": "2025-12-31",
            "field": "ticker", "value": "ZZZ"}]
        gaps = runner.grid_gaps(self.grid(), make_labels(), preds)
        assert any(ACC_OVER in g for g in gaps)

    def test_duplicate_prediction_is_a_named_gap(self):
        preds = make_predictions() + [pred_row(ACC_A, "ticker")]
        gaps = runner.grid_gaps(self.grid(), make_labels(), preds)
        assert any("duplicate" in g.lower() and ACC_A in g for g in gaps)

    def test_duplicate_label_is_a_named_gap(self):
        labels = make_labels() + [label_row(ACC_A, "ticker")]
        gaps = runner.grid_gaps(self.grid(), labels, make_predictions())
        assert any("duplicate" in g.lower() and ACC_A in g for g in gaps)

    def test_missing_label_is_a_named_gap(self):
        """The labels file is checked against the manifest grid too -- the
        runner must not quietly score whatever subset happens to exist."""
        labels = [r for r in make_labels()
                  if not (r["accession"] == ACC_A and r["field"] == "ceo_name")]
        gaps = runner.grid_gaps(self.grid(), labels, make_predictions())
        assert any("ceo_name" in g and ACC_A in g for g in gaps)

    def test_unlabeled_status_is_a_named_gap(self):
        labels = make_labels()
        labels[3] = dict(labels[3], status="pending")
        gaps = runner.grid_gaps(self.grid(), labels, make_predictions())
        assert any("pending" in g for g in gaps)

    def test_prediction_without_a_value_key_is_a_named_gap(self):
        """A record missing 'value' must be a gap, not a silent null -- .get()
        would score it as an abstention the model never made."""
        preds = make_predictions()
        del preds[5]["value"]
        gaps = runner.grid_gaps(self.grid(), make_labels(), preds)
        assert any("value" in g.lower() and preds[5]["field"] in g for g in gaps)


# -------------------------------------------------------------- alignment

class TestAlignment:
    def test_pairs_are_joined_by_key_not_position(self):
        """THE defect this runner exists to prevent: summarize() zips
        positionally, so pairing is entirely the runner's job. A predictions
        file in any order must score identically."""
        ordered, values = runner.align(make_labels(), make_predictions())
        shuffled_preds = list(reversed(make_predictions()))
        ordered2, values2 = runner.align(make_labels(), shuffled_preds)
        assert ordered == ordered2
        assert values == values2

    def test_each_label_gets_its_own_accessions_prediction(self):
        ordered, values = runner.align(make_labels(), make_predictions())
        by_key = {(l["accession"], l["field"]): v
                  for l, v in zip(ordered, values)}
        assert by_key[(ACC_A, "total_assets")] == 16524.0
        assert by_key[(ACC_B, "total_assets")] == 12345.0

    def test_alignment_refuses_an_incomplete_join(self):
        preds = make_predictions()[:-1]
        with pytest.raises(KeyError):
            runner.align(make_labels(), preds)


# ------------------------------------------------------------ detail rows

class TestDetailRows:
    def detail(self):
        labels, values = runner.align(make_labels(), make_predictions())
        return runner.detail_rows(labels, values)

    def test_false_extractions_are_collected_with_the_predicted_value(self):
        rows = self.detail()["false_extractions"]
        assert len(rows) == 1
        row = rows[0]
        assert (row["accession"], row["field"]) == (
            ACC_B, "dividends_declared_per_share")
        assert row["predicted"] == 1.10
        assert row["answer_kind"] == "not_addressed"

    def test_ceo_name_mismatches_are_wrong_values_only(self):
        """A null ceo_name is an abstention, not a mismatch between two names;
        the plan's separate list is for judging the name rule's failures."""
        rows = self.detail()["ceo_name_mismatches"]
        assert [r["accession"] for r in rows] == [ACC_B]
        assert rows[0]["label_value"] == "Robert Jones"
        assert rows[0]["predicted"] == "William Jones"

    def test_wrong_values_and_missed_are_collected(self):
        detail = self.detail()
        wrong = {(r["accession"], r["field"]) for r in detail["wrong_values"]}
        assert (ACC_B, "total_assets") in wrong
        missed = {(r["accession"], r["field"]) for r in detail["missed"]}
        assert missed == {(ACC_B, "employees")}

    def test_ambiguous_flag_reaches_the_rows(self):
        labels = make_labels()
        for row in labels:
            if row["accession"] == ACC_B and row["field"] == "dividends_declared_per_share":
                row["ambiguous"] = True
        aligned, values = runner.align(labels, make_predictions())
        rows = runner.detail_rows(aligned, values)["false_extractions"]
        assert rows[0]["ambiguous"] is True


# ------------------------------------------------------------ the report

class TestReport:
    def report(self):
        labels, values = runner.align(make_labels(), make_predictions())
        return runner.render_report(
            runner.summarize(labels, values),
            runner.detail_rows(labels, values),
            {"labels_sha256": "cafe" * 16, "labels_frozen": True,
             "expected_pairs": 18, "filings": 2, "run_meta": None},
        )

    def test_report_is_pure_ascii(self):
        """The console is cp1252; one smart quote and the report dies."""
        self.report().encode("ascii")

    def test_fields_below_the_gate_never_show_an_accuracy(self):
        """Every synthetic field has n=2, far under MIN_REPORTABLE_N=25, so
        the per-field table must show the gate, not a number whose interval
        spans everything."""
        report = self.report()
        assert "GATED" in report
        line = next(l for l in report.splitlines() if "total_assets" in l)
        assert "0.5" not in line
        assert "GATED" in line

    def test_pooled_rate_carries_numerator_denominator_and_interval(self):
        report = self.report()
        line = next(l for l in report.splitlines() if "pooled" in l.lower())
        assert "14/18" in line
        assert "[" in line and "]" in line

    def test_population_weighted_overall_is_reported(self):
        assert "population-weighted" in self.report().lower()

    def test_all_five_outcomes_are_reported_separately(self):
        report = self.report()
        for name in ("correct", "wrong_value", "missed", "false_extraction",
                     "correct_abstention"):
            assert name in report

    def test_false_extraction_rate_shows_the_settled_denominator(self):
        report = self.report()
        assert "1/3" in report          # 1 FE over stated_none + not_addressed
        assert "stated_none + not_addressed" in report

    def test_false_extraction_rate_carries_the_contamination_disclosure(self):
        assert "DISCLOSED CONTAMINATION" in self.report()

    def test_ambiguous_is_reported_included_and_excluded(self):
        report = self.report().lower()
        assert "excluding ambiguous" in report

    def test_ceo_name_mismatches_are_listed_verbatim(self):
        report = self.report()
        assert "Robert Jones" in report
        assert "William Jones" in report

    def test_false_extraction_cases_are_listed(self):
        report = self.report()
        assert "dividends_declared_per_share" in report
        assert "1.1" in report


# ------------------------------------------------------------------ main

class TestMain:
    def test_clean_corpus_scores_and_exits_zero(self, corpus, capsys):
        assert run_main(corpus) == 0
        out = capsys.readouterr().out
        assert "grid check" in out.lower()
        assert "pooled" in out.lower()

    def test_grid_gap_reports_and_refuses_to_score(self, corpus, capsys):
        preds = [p for p in make_predictions()
                 if not (p["accession"] == ACC_B and p["field"] == "employees")]
        write_jsonl(corpus["predictions"], preds)
        assert run_main(corpus) == 1
        out = capsys.readouterr().out
        assert ACC_B in out and "employees" in out
        assert "pooled" not in out.lower()   # no numbers on a broken grid

    def test_wrong_labels_hash_refuses_without_scoring(self, corpus, capsys):
        code = runner.main([
            "--labels", str(corpus["labels"]),
            "--predictions", str(corpus["predictions"]),
            "--manifest", str(corpus["manifest"]),
            "--labels-sha256", "0" * 64,
        ])
        assert code == 2
        out = capsys.readouterr().out
        assert "pooled" not in out.lower()

    def test_default_expected_hash_is_the_frozen_one(self):
        """The constant in the script is the audit link to the freeze; scoring
        different bytes must require saying so explicitly on the command
        line."""
        assert runner.FROZEN_LABELS_SHA256 == (
            "ad155dddb4a11c772f37973bcda0b3f2464da57798901aec5366c3ca2d671c50")

    def test_json_output_is_written(self, corpus):
        out = corpus["tmp"] / "scores.json"
        assert run_main(corpus, "--json", str(out)) == 0
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["summary"]["pooled"]["n"] == 18
        assert data["detail"]["false_extractions"]
        assert data["provenance"]["labels_sha256"] == sha256_of(corpus["labels"])
