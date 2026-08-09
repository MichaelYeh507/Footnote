"""Outcome classification and aggregation.

The outcome grid is pre-registered in HYBRID-RETRIEVAL-SEC-PLAN.md §5. Its point
is that most extraction evals collapse three different things into "wrong": a
null is cheap, and a confidently wrong number is expensive. Keeping them apart
is what makes the false-extraction rate computable at all.
"""

import pytest

from evaluation.scoring import Outcome, classify, summarize


def label(field="goodwill_impairment", kind="value", value=125.0, **extra):
    return {"field": field, "answer_kind": kind, "value": value,
            "status": "labeled", "ambiguous": False, **extra}


class TestOutcomeGrid:
    def test_value_matched_is_correct(self):
        assert classify(label(kind="value", value=125.0), 125.0) is Outcome.CORRECT

    def test_value_mismatched_is_wrong_value(self):
        assert classify(label(kind="value", value=125.0), 400.0) is Outcome.WRONG_VALUE

    def test_value_against_null_is_missed_not_wrong(self):
        """Abstaining on a present field is a different failure from inventing
        a number, and the plan tracks them separately."""
        assert classify(label(kind="value", value=125.0), None) is Outcome.MISSED

    def test_stated_none_against_zero_is_correct(self):
        assert classify(label(kind="stated_none", value=0.0), 0.0) is Outcome.CORRECT

    def test_stated_none_against_a_number_is_a_false_extraction(self):
        """The filing said there was no impairment; the model produced one."""
        assert classify(label(kind="stated_none", value=0.0), 125.0) is Outcome.FALSE_EXTRACTION

    def test_stated_none_against_null_is_missed(self):
        assert classify(label(kind="stated_none", value=0.0), None) is Outcome.MISSED

    def test_not_addressed_against_null_is_correct_abstention(self):
        assert classify(label(kind="not_addressed", value=None), None) is Outcome.CORRECT_ABSTENTION

    def test_not_addressed_against_a_number_is_a_false_extraction(self):
        assert classify(label(kind="not_addressed", value=None), 125.0) is Outcome.FALSE_EXTRACTION

    def test_not_addressed_against_zero_is_still_a_false_extraction(self):
        """Zero is an assertion that the filing stated zero. It did not state
        anything, so this is an invented value, not an abstention."""
        assert classify(label(kind="not_addressed", value=None), 0.0) is Outcome.FALSE_EXTRACTION


class TestScoringGuards:
    def test_unlabeled_instances_are_refused(self):
        """A pending record must never be scored. Blanks are indistinguishable
        from 'correctly absent' unless the code refuses to guess."""
        with pytest.raises(ValueError):
            classify(label(status="pending"), None)

    def test_unknown_answer_kind_is_refused(self):
        with pytest.raises(ValueError):
            classify(label(kind="probably_none"), None)


class TestSummarize:
    def _labels_and_predictions(self):
        # 4 fields x 5 filings, with a known outcome mix.
        rows = []
        preds = []
        for i in range(5):
            rows.append(label(field="ticker", kind="value", value="AAPL"))
            preds.append("AAPL" if i < 4 else "MSFT")          # 4/5 correct
        for i in range(5):
            rows.append(label(field="total_assets", kind="value", value=100.0))
            preds.append(100.0 if i < 3 else None)              # 3/5, 2 missed
        for i in range(5):
            rows.append(label(field="goodwill_impairment",
                              kind="not_addressed", value=None))
            preds.append(None if i < 4 else 50.0)               # 4/5, 1 false extraction
        for i in range(5):
            rows.append(label(field="dividends_declared_per_share",
                              kind="stated_none", value=0.0))
            preds.append(0.0 if i < 2 else 7.5)                 # 2/5, 3 false extractions
        return rows, preds

    def test_per_field_counts_and_accuracy(self):
        rows, preds = self._labels_and_predictions()
        report = summarize(rows, preds)
        by_field = {f["field"]: f for f in report["fields"]}
        assert by_field["ticker"]["n"] == 5
        assert by_field["ticker"]["correct"] == 4
        assert by_field["ticker"]["accuracy"] == pytest.approx(0.8)
        assert by_field["total_assets"]["outcomes"]["missed"] == 2

    def test_every_field_carries_an_interval(self):
        rows, preds = self._labels_and_predictions()
        for field in summarize(rows, preds)["fields"]:
            lo, hi = field["interval"]
            assert 0.0 <= lo <= field["accuracy"] <= hi <= 1.0

    def test_pooled_and_macro_are_both_reported(self):
        """§5 asks for both, and says the gap between them is the finding.
        Here every field has n=5 so they coincide; the harness must still
        report each rather than assuming they are interchangeable."""
        rows, preds = self._labels_and_predictions()
        report = summarize(rows, preds)
        assert report["pooled"]["correct"] == 13
        assert report["pooled"]["n"] == 20
        assert report["pooled"]["accuracy"] == pytest.approx(0.65)
        assert report["macro"]["accuracy"] == pytest.approx(0.65)

    def test_pooled_and_macro_diverge_when_field_counts_differ(self):
        """The case the gap is meant to expose: a field with few instances
        pulls the macro average without moving the pooled one much."""
        rows = [label(field="ticker", kind="value", value="A")] * 10
        preds = ["A"] * 10
        rows += [label(field="ceo_name", kind="value", value="X")] * 2
        preds += ["WRONG", "WRONG"]
        report = summarize(rows, preds)
        assert report["pooled"]["accuracy"] == pytest.approx(10 / 12)
        assert report["macro"]["accuracy"] == pytest.approx(0.5)

    def test_false_extraction_rate_uses_the_absent_denominator(self):
        """Denominator is stated_none + not_addressed instances, per §5 --
        not the whole corpus, which would understate the rate fourfold here."""
        rows, preds = self._labels_and_predictions()
        fx = summarize(rows, preds)["false_extraction"]
        assert fx["n"] == 10           # 5 not_addressed + 5 stated_none
        assert fx["count"] == 4        # 1 + 3
        assert fx["rate"] == pytest.approx(0.4)
        lo, hi = fx["interval"]
        assert lo < 0.4 < hi

    def test_fields_below_the_minimum_n_are_gated(self):
        """§3: gate any per-field claim below n=25. The harness must mark it,
        not silently publish a number with a +/-30 point interval."""
        rows = [label(field="ticker", kind="value", value="A")] * 5
        preds = ["A"] * 5
        field = summarize(rows, preds)["fields"][0]
        assert field["reportable"] is False
        assert field["n"] < 25

    def test_skipped_instances_are_excluded_from_denominators(self):
        rows = [label(field="ticker", kind="value", value="A") for _ in range(3)]
        rows[2]["status"] = "skipped"
        preds = ["A", "A", "A"]
        report = summarize(rows, preds)
        assert report["pooled"]["n"] == 2
        assert report["skipped"] == 1

    def test_ambiguous_instances_are_counted_and_reported_both_ways(self):
        """§5 says log ambiguous cases rather than resolving them silently. The
        headline number and the number excluding flagged instances are both
        reported, so the reader can see whether ambiguity is load-bearing."""
        rows = [label(field="ticker", kind="value", value="A") for _ in range(4)]
        rows[3]["ambiguous"] = True
        preds = ["A", "A", "A", "WRONG"]
        report = summarize(rows, preds)
        assert report["ambiguous"] == 1
        assert report["pooled"]["accuracy"] == pytest.approx(0.75)
        assert report["excluding_ambiguous"]["accuracy"] == pytest.approx(1.0)

    def test_mismatched_lengths_are_refused(self):
        with pytest.raises(ValueError):
            summarize([label()], [])
