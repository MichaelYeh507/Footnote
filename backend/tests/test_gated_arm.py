"""Scoring a fourth arm without disturbing the first three.

PRE-REGISTERED 2026-08-20 (`EVALUATION-SPEC.md`, appendix *PHASE 3b*), and
**post-hoc**: the arm answers a failure visible in Phase 3's published numbers,
on the same 65 queries.

The constraint that shapes every test here is the hard one from that appendix:
**the sparse, dense and hybrid numbers already published are never recomputed,
adjusted or restated.** So the scorer must behave in exactly two ways --
identically to before when handed the original rankings, and with one extra row
when handed the gated file alongside. Anything in between is a regression that
would quietly change a published number.

The failures this file is written against:

  **The fourth arm becoming mandatory.** Adding "gated" to `ARMS` would make
  the scorer refuse the original rankings file, which is what `RESULTS.md`
  tells a reader to re-score. The published numbers would become
  irreproducible by the very document that publishes them.

  **A partial merge scoring as a result.** A gated file covering some queries
  and not others gives a fourth arm a smaller denominator than the other
  three, and the same hits over a smaller denominator is a higher recall.

  **The merge rewriting the three arms.** Merging must add a key and touch
  nothing else; a merge that rebuilt the record could reorder or drop an arm's
  list with no error anywhere.

  **Comparisons quietly staying at three arms.** If `COMPARISONS` is not
  extended, the fourth arm gets recall rows and no paired test -- and a
  direction would then be read off point estimates, which is the mistake this
  project already made once.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import gate  # noqa: E402
from evaluation import retrieval_scoring as scoring  # noqa: E402


def _query(query_id, stratum="exact_entity", gold=(("acc-1", "the span"),)):
    return {
        "query_id": query_id,
        "stratum": stratum,
        "query": "a question",
        "gold": [{"accession": a, "span": s} for a, s in gold],
    }


def _unanswerable(query_id):
    return {"query_id": query_id, "stratum": "unanswerable",
            "query": "a question no filing answers", "gold": []}


def _chunk(chunk_id, text, accession="acc-1"):
    return {"chunk_id": chunk_id, "accession": accession, "ticker": "AAA",
            "period": "2025-12-31", "item": "1", "title": "Business",
            "index": 0, "first_page": 1, "last_page": 1,
            "tokens": len(text.split()), "text": text}


def _ranking(query_id, sparse=(), dense=(), hybrid=(), gated=None):
    arms = {
        "sparse": [[c, 1.0] for c in sparse],
        "dense": [[c, 0.1] for c in dense],
        "hybrid": [[c, 0.01] for c in hybrid],
    }
    if gated is not None:
        arms["gated"] = [[c, 0.01] for c in gated]
    return {"query_id": query_id, "stratum": "exact_entity",
            "tsquery": "'a'", "arms": arms}


CHUNKS = [_chunk("c1", "the span is here"), _chunk("c2", "something else"),
          _chunk("c3", "a third passage")]
QUERIES = [_query("q1"), _query("q2"), _unanswerable("q3")]


class TestTheThreeArmsAreUntouched:

    def test_the_pre_registered_arms_are_still_exactly_three(self):
        """`ARMS` is the pre-registered set and 3b does not join it."""
        assert scoring.ARMS == ("sparse", "dense", "hybrid")

    def test_the_pre_registered_comparisons_are_still_exactly_three(self):
        assert scoring.COMPARISONS == (
            ("hybrid", "sparse"), ("hybrid", "dense"), ("dense", "sparse"))

    def test_rankings_without_a_gated_arm_still_score(self):
        """The reproduction path `RESULTS.md` publishes must keep working."""
        rankings = {"q1": _ranking("q1", ["c1"], ["c2"], ["c1"]),
                    "q2": _ranking("q2", ["c2"], ["c1"], ["c2"])}
        summary = scoring.summarize(QUERIES, CHUNKS, rankings)
        assert set(summary["arms"]) == {"sparse", "dense", "hybrid"}

    def test_adding_the_fourth_arm_changes_no_other_arm_s_number(self):
        """The load-bearing test of this file.

        Score the same rankings twice -- once without a gated arm, once with --
        and require every sparse, dense and hybrid figure to be identical.
        """
        plain = {"q1": _ranking("q1", ["c1"], ["c2"], ["c1"]),
                 "q2": _ranking("q2", ["c2"], ["c1"], ["c2"])}
        withgate = {"q1": _ranking("q1", ["c1"], ["c2"], ["c1"], gated=["c1"]),
                    "q2": _ranking("q2", ["c2"], ["c1"], ["c2"], gated=["c1"])}

        before = scoring.summarize(QUERIES, CHUNKS, plain)
        after = scoring.summarize(QUERIES, CHUNKS, withgate)

        for arm in scoring.ARMS:
            assert before["arms"][arm] == after["arms"][arm], arm

        def pairs(summary):
            return {(c["arm_a"], c["arm_b"], c["k"], c["stratum"]): c
                    for c in summary["comparisons"]
                    if "gated" not in (c["arm_a"], c["arm_b"])}
        assert pairs(before) == pairs(after)


class TestDetectingTheFourthArm:

    def test_it_is_found_when_every_ranking_carries_it(self):
        rankings = {"q1": _ranking("q1", gated=["c1"]),
                    "q2": _ranking("q2", gated=["c2"])}
        assert scoring.arms_present(rankings) == (
            "sparse", "dense", "hybrid", "gated")

    def test_it_is_absent_when_no_ranking_carries_it(self):
        rankings = {"q1": _ranking("q1"), "q2": _ranking("q2")}
        assert scoring.arms_present(rankings) == scoring.ARMS

    def test_a_partial_gated_file_is_refused_not_scored(self):
        """Some queries gated and some not gives the fourth arm a smaller
        denominator, and the same hits over a smaller denominator is a higher
        recall."""
        rankings = {"q1": _ranking("q1", gated=["c1"]), "q2": _ranking("q2")}
        with pytest.raises(ValueError, match="q2"):
            scoring.arms_present(rankings)


class TestTheFourthArmIsReported:

    RANKINGS = {"q1": _ranking("q1", ["c2"], ["c1"], ["c2"], gated=["c1"]),
                "q2": _ranking("q2", ["c2"], ["c2"], ["c2"], gated=["c2"])}

    def test_it_gets_its_own_recall_rows(self):
        summary = scoring.summarize(QUERIES, CHUNKS, self.RANKINGS)
        assert "gated" in summary["arms"]
        assert summary["arms"]["gated"][1]["pooled"]["hits"] == 1
        assert summary["arms"]["gated"][1]["pooled"]["n"] == 2

    def test_it_is_paired_against_all_three(self):
        """Every arm compared against every other, or a direction gets read off
        point estimates."""
        summary = scoring.summarize(QUERIES, CHUNKS, self.RANKINGS)
        seen = {(c["arm_a"], c["arm_b"]) for c in summary["comparisons"]}
        assert ("gated", "sparse") in seen
        assert ("gated", "dense") in seen
        assert ("gated", "hybrid") in seen

    def test_the_fifteen_are_still_excluded(self):
        summary = scoring.summarize(QUERIES, CHUNKS, self.RANKINGS)
        assert summary["queries"]["excluded_unanswerable"] == 1
        assert summary["arms"]["gated"][5]["pooled"]["n"] == 2


class TestMergingTheGatedFile:

    def test_it_adds_one_key_and_touches_nothing_else(self):
        base = {"q1": _ranking("q1", ["c1"], ["c2"], ["c1"])}
        original = json.loads(json.dumps(base))
        merged = scoring.merge_gated(base, {"q1": [["c3", 0.5]]})
        assert merged["q1"]["arms"]["gated"] == [["c3", 0.5]]
        for arm in scoring.ARMS:
            assert (merged["q1"]["arms"][arm]
                    == original["q1"]["arms"][arm]), arm

    def test_it_does_not_mutate_the_rankings_it_was_given(self):
        """The original file is the authority for three published arms; a merge
        that mutated it in place would edit them in memory."""
        base = {"q1": _ranking("q1", ["c1"], ["c2"], ["c1"])}
        scoring.merge_gated(base, {"q1": [["c3", 0.5]]})
        assert "gated" not in base["q1"]["arms"]

    def test_a_gated_entry_for_an_unknown_query_is_refused(self):
        base = {"q1": _ranking("q1")}
        with pytest.raises(ValueError, match="q9"):
            scoring.merge_gated(base, {"q1": [], "q9": []})

    def test_a_missing_gated_entry_is_refused(self):
        base = {"q1": _ranking("q1"), "q2": _ranking("q2")}
        with pytest.raises(ValueError, match="q2"):
            scoring.merge_gated(base, {"q1": []})


class TestTheReportDoesNotClaimBlindnessItDoesNotHave:
    """The report footer says every parameter was published before either index
    existed. That is true of the three arms and **false** of the fourth.

    Caught by reading the rendered output rather than by a test: the sentence
    printed unconditionally, under a table containing a post-hoc arm, from the
    very tool a reader would use to check the claim.
    """

    def _summary(self, gated: bool):
        rankings = {"q1": _ranking("q1", ["c1"], ["c2"], ["c1"],
                                   gated=["c1"] if gated else None),
                    "q2": _ranking("q2", ["c2"], ["c1"], ["c2"],
                                   gated=["c2"] if gated else None)}
        return scoring.summarize(QUERIES, CHUNKS, rankings)

    def _render(self, summary):
        import importlib
        module = importlib.import_module("scripts.score_retrieval")
        return module.report(summary, {}, None, {})

    def test_the_blindness_claim_is_printed_for_the_three_arms(self):
        text = self._render(self._summary(gated=False))
        assert "Every parameter above was published before either index" in text

    def test_the_blindness_claim_is_withdrawn_once_the_fourth_arm_is_there(self):
        text = self._render(self._summary(gated=True))
        assert "Every parameter above was published before either index" \
            not in text

    def test_the_post_hoc_disclosure_replaces_it(self):
        text = self._render(self._summary(gated=True))
        assert "were NOT" in text
        assert "2026-08-20" in text

    def test_the_gated_row_is_marked_in_the_table_itself(self):
        """A reader who copies one table out of the report still sees it."""
        text = self._render(self._summary(gated=True))
        assert "POST-HOC" in text.split("RECALL@1")[1].split("RECALL@5")[0]


class TestReadingTheGateStatisticFromAStoredRun:
    """`s1` comes out of the recorded rankings, so the fourth arm needs no
    database and no API -- and cannot re-run, and therefore cannot disturb, the
    three arms it is compared against."""

    def test_s1_is_the_top_sparse_score(self):
        record = _ranking("q1")
        record["arms"]["sparse"] = [["c1", 3.4], ["c2", 1.1], ["c3", 0.2]]
        assert gate.sparse_top_score(record) == 3.4

    def test_s1_is_not_taken_from_the_dense_arm(self):
        """Dense stores a cosine *distance*, where smaller is better. Reading
        it as evidence would invert the gate."""
        record = _ranking("q1")
        record["arms"]["sparse"] = [["c1", 3.4]]
        record["arms"]["dense"] = [["c9", 0.001]]
        assert gate.sparse_top_score(record) == 3.4

    def test_an_empty_sparse_list_is_zero_evidence(self):
        """A query whose lexemes matched nothing. Not an error: the arm
        returning nothing is exactly the case the gate exists for."""
        record = _ranking("q1")
        record["arms"]["sparse"] = []
        assert gate.sparse_top_score(record) == 0.0


class TestApplyingTheThresholdToAStoredRun:

    TAUS = {4: 2.0, 8: 5.0}

    def _record(self, tsquery, sparse, dense):
        return {"query_id": "q1", "stratum": "conceptual", "tsquery": tsquery,
                "arms": {"sparse": sparse, "dense": dense, "hybrid": []}}

    def test_it_looks_up_tau_by_the_query_s_own_lexeme_count(self):
        """L=4 here, so tau is 2.0 and not the 5.0 belonging to L=8."""
        record = self._record("'a' | 'b' | 'c' | 'd'",
                              [["s1", 3.0]], [["d1", 0.1]])
        decision = gate.gate_decision(record, self.TAUS)
        assert decision["lexemes"] == 4
        assert decision["tau"] == 2.0
        assert decision["s1"] == 3.0
        assert decision["gated"] is False

    def test_a_query_below_its_own_tau_is_gated(self):
        record = self._record("'a' | 'b' | 'c' | 'd'",
                              [["s1", 1.0]], [["d1", 0.1]])
        assert gate.gate_decision(record, self.TAUS)["gated"] is True

    def test_a_missing_tau_is_refused_rather_than_defaulted(self):
        """Defaulting to 0.0 would gate nothing at that size and look like a
        measured decision; defaulting high would gate everything. Neither is
        detectable in a published recall figure."""
        record = self._record("'a' | 'b'", [["s1", 1.0]], [["d1", 0.1]])
        with pytest.raises(KeyError, match="2"):
            gate.gate_decision(record, self.TAUS)

    def test_the_gated_ranking_is_dense_only(self):
        record = self._record("'a' | 'b' | 'c' | 'd'",
                              [["s1", 1.0]], [["d1", 0.1], ["d2", 0.2]])
        decision = gate.gate_decision(record, self.TAUS)
        assert [c for c, _ in decision["ranking"]] == ["d1", "d2"]

    def test_the_ungated_ranking_holds_both_arms(self):
        record = self._record("'a' | 'b' | 'c' | 'd'",
                              [["s1", 9.0]], [["d1", 0.1]])
        decision = gate.gate_decision(record, self.TAUS)
        ids = {c for c, _ in decision["ranking"]}
        assert ids == {"s1", "d1"}


class TestTheGatedArmMatchesItsDefinition:
    """End to end on hand-built lists: the arm the scorer sees is the arm the
    pre-registration describes."""

    SPARSE = [f"s{i:03d}" for i in range(50)]
    DENSE = [f"d{i:03d}" for i in range(50)]

    def test_an_ungated_query_is_the_hybrid_arm_exactly(self):
        built = gate.gated_ranking(self.SPARSE, self.DENSE, s1=9.0, tau=1.0)
        from services import fusion
        assert built == fusion.reciprocal_rank_fusion(
            [self.SPARSE, self.DENSE])

    def test_a_gated_query_is_the_dense_arm_exactly(self):
        built = gate.gated_ranking(self.SPARSE, self.DENSE, s1=0.1, tau=1.0)
        assert [c for c, _ in built] == self.DENSE
