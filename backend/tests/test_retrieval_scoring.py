"""Scoring the three arms: the denominators, the intervals, and the pairing.

Written **before any arm ran**, for the reason the query-set validator was
written before the first query: scoring code written while the rankings are on
screen gets shaped by them one judgement call at a time -- which chunk "really"
counts, which query was "unfair" -- and no reader could ever see it happen.
Everything here is exercised against hand-built rankings whose answers were
worked out on paper.

The failures this file is written against:

  **The 15 entering a denominator.** They carry no gold, recall is undefined
  for them, and the only safe way to exclude them is explicitly. Scoring them
  as misses would drag every arm's pooled figure down by the same 15/65 and
  look like a result.

  **Three overlapping Wilson intervals read as "no difference".** The arms see
  the same queries, so the comparison is paired. McNemar's discordant pairs are
  what the pre-registration requires alongside, and the case that has to be
  handled rather than crashed is `b + c == 0`: two arms that agree everywhere
  have no rate to report, and `wilson_interval` raises on an empty denominator
  by design.

  **A silently short ranking.** A hit test over a truncated list returns False,
  which is indistinguishable from a genuine miss.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import retrieval_scoring as scoring  # noqa: E402
from evaluation import wilson  # noqa: E402


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


def _ranking(query_id, sparse=(), dense=(), hybrid=()):
    return {
        "query_id": query_id,
        "stratum": "exact_entity",
        "tsquery": "'a'",
        "arms": {
            "sparse": [[c, 1.0] for c in sparse],
            "dense": [[c, 0.1] for c in dense],
            "hybrid": [[c, 0.01] for c in hybrid],
        },
    }


class TestSplittingTheSet:

    def test_the_unanswerable_are_excluded_and_counted(self):
        queries = [_query("q1"), _query("q2", "conceptual"),
                   _unanswerable("q3"), _unanswerable("q4")]
        split = scoring.split_by_answerability(queries)
        assert [q["query_id"] for q in split["answerable"]] == ["q1", "q2"]
        assert split["excluded"] == ["q3", "q4"]

    def test_an_unanswerable_query_carrying_gold_is_refused(self):
        """It would silently enter the recall denominator, which is the one
        thing the stratum exists not to do."""
        broken = dict(_unanswerable("q3"),
                      gold=[{"accession": "acc-1", "span": "x"}])
        with pytest.raises(ValueError, match="q3"):
            scoring.split_by_answerability([_query("q1"), broken])

    def test_an_answerable_query_with_no_gold_is_refused(self):
        """`hit_at_k` raises on empty gold by design; catching it here names
        the query instead of failing halfway through a run."""
        broken = dict(_query("q1"), gold=[])
        with pytest.raises(ValueError, match="q1"):
            scoring.split_by_answerability([broken])

    def test_stratum_counts_are_reported(self):
        queries = [_query("q1"), _query("q2"), _query("q3", "conceptual"),
                   _unanswerable("q4")]
        split = scoring.split_by_answerability(queries)
        assert split["strata"] == {"exact_entity": 2, "conceptual": 1}


class TestHitsPerArm:

    # Six chunks, only c1 holding the gold span in the gold accession. The
    # filler exists because the unknown-id guard refuses any ranked id the
    # store does not hold -- a ranking of invented ids would be refused before
    # it could be scored.
    records = [_chunk("c1", "the span sits here"),
               _chunk("c2", "something else entirely"),
               _chunk("c3", "the span again", accession="acc-2")] + [
        _chunk(f"x{i}", f"filler {i}") for i in range(1, 6)]

    def test_a_gold_chunk_at_rank_one_hits_at_both_k(self):
        outcome = scoring.score_query(
            self.records, _query("q1"),
            _ranking("q1", sparse=["c1", "c2"]))
        assert outcome["arms"]["sparse"] == {1: True, 5: True}

    def test_a_gold_chunk_at_rank_three_hits_at_five_only(self):
        outcome = scoring.score_query(
            self.records, _query("q1"),
            _ranking("q1", sparse=["c2", "x1", "c1"]))
        assert outcome["arms"]["sparse"] == {1: False, 5: True}

    def test_a_gold_chunk_at_rank_six_misses_at_both(self):
        ranked = ["x1", "x2", "x3", "x4", "x5", "c1"]
        outcome = scoring.score_query(self.records, _query("q1"),
                                      _ranking("q1", sparse=ranked))
        assert outcome["arms"]["sparse"] == {1: False, 5: False}

    def test_an_empty_ranking_is_a_miss_not_an_error(self):
        """A sparse query can legitimately match nothing. That is a miss."""
        outcome = scoring.score_query(self.records, _query("q1"),
                                      _ranking("q1", sparse=[]))
        assert outcome["arms"]["sparse"] == {1: False, 5: False}

    def test_gold_is_scoped_to_the_accession(self):
        """c3 holds the same text in another filing. Retrieving it is a miss --
        the pre-registered rule, and the reason AMENDMENT 5 exists."""
        outcome = scoring.score_query(self.records, _query("q1"),
                                      _ranking("q1", sparse=["c3"]))
        assert outcome["arms"]["sparse"][5] is False
        assert outcome["gold"] == ["c1"]

    def test_every_arm_is_scored(self):
        outcome = scoring.score_query(
            self.records, _query("q1"),
            _ranking("q1", sparse=["c1"], dense=["c2"], hybrid=["c2", "c1"]))
        assert outcome["arms"]["sparse"][1] is True
        assert outcome["arms"]["dense"][1] is False
        assert outcome["arms"]["hybrid"][1] is False
        assert outcome["arms"]["hybrid"][5] is True

    def test_a_ranking_naming_a_chunk_the_store_does_not_have_is_refused(self):
        """The database and the materialised store are two sources. Gold comes
        from the store, rankings from the database, and an id in one and not
        the other scores as a miss that looks exactly like a retrieval
        failure."""
        with pytest.raises(ValueError, match="ghost"):
            scoring.score_query(self.records, _query("q1"),
                                _ranking("q1", sparse=["ghost"]))

    def test_a_query_whose_gold_matches_nothing_is_refused(self):
        query = _query("q1", gold=(("acc-1", "no chunk holds this"),))
        with pytest.raises(ValueError, match="q1"):
            scoring.score_query(self.records, query, _ranking("q1"))

    def test_a_query_whose_gold_exceeds_the_cap_is_refused(self):
        """AMENDMENT 4: at most five chunks in the union. Above that, recall@5
        is satisfied essentially by accident."""
        records = [_chunk(f"c{i}", "the span") for i in range(6)]
        with pytest.raises(ValueError, match="q1"):
            scoring.score_query(records, _query("q1"), _ranking("q1"))


class TestTheRecallRows:

    def test_a_row_carries_its_denominator_and_a_wilson_interval(self):
        row = scoring.recall_row(20, 25)
        assert row["hits"] == 20 and row["n"] == 25
        assert row["rate"] == pytest.approx(0.8)
        assert row["interval"] == wilson.wilson_interval(20, 25)

    def test_a_row_below_the_reportable_floor_is_flagged(self):
        """Section 3 gates any claim below n = 25, and the strata are 25
        exactly. A row that fell under it must say so rather than read like
        the others."""
        assert scoring.recall_row(8, 10)["reportable"] is False
        assert scoring.recall_row(20, 25)["reportable"] is True

    def test_an_empty_denominator_raises_rather_than_rendering_as_zero(self):
        with pytest.raises(ValueError):
            scoring.recall_row(0, 0)


class TestTheMcNemarComparison:

    def test_discordant_pairs_are_counted_in_both_directions(self):
        a = {"q1": True, "q2": True, "q3": False, "q4": False}
        b = {"q1": True, "q2": False, "q3": True, "q4": False}
        result = scoring.mcnemar(a, b)
        assert result["b"] == 1, "queries a hits and b misses"
        assert result["c"] == 1, "queries b hits and a misses"
        assert result["concordant"] == 2

    def test_the_rate_is_b_over_b_plus_c_with_a_wilson_interval(self):
        a = {f"q{i}": i < 8 for i in range(10)}
        b = {f"q{i}": i >= 8 for i in range(10)}
        result = scoring.mcnemar(a, b)
        assert (result["b"], result["c"]) == (8, 2)
        assert result["rate"] == pytest.approx(0.8)
        assert result["interval"] == wilson.wilson_interval(8, 10)

    def test_two_arms_that_agree_everywhere_have_no_rate(self):
        """`wilson_interval` raises on an empty denominator by design, so the
        no-discordance case has to be a shape rather than a crash. It is also
        a real outcome: two arms can return the same top-1 for every query."""
        a = b = {"q1": True, "q2": False}
        result = scoring.mcnemar(a, b)
        assert (result["b"], result["c"]) == (0, 0)
        assert result["rate"] is None
        assert result["interval"] is None

    def test_it_refuses_two_arms_scored_over_different_queries(self):
        """The comparison is paired. Comparing arms over different query sets
        is not a paired test, and nothing in the output would show it."""
        with pytest.raises(ValueError, match="same queries"):
            scoring.mcnemar({"q1": True}, {"q2": True})

    def test_the_discordant_denominator_is_flagged_when_it_is_small(self):
        a = {"q1": True, "q2": False, "q3": False}
        b = {"q1": False, "q2": False, "q3": False}
        assert scoring.mcnemar(a, b)["reportable"] is False


class TestTheWholeSummary:

    records = [_chunk("g1", "the first span"),
               _chunk("g2", "the second span"),
               _chunk("n1", "noise one"),
               _chunk("n2", "noise two")]

    queries = [
        _query("q1", "exact_entity", (("acc-1", "the first span"),)),
        _query("q2", "conceptual", (("acc-1", "the second span"),)),
        _unanswerable("q3"),
    ]

    rankings = {
        # sparse gets q1 at rank 1 and misses q2 entirely.
        # dense gets q2 at rank 1 and q1 at rank 3.
        # hybrid gets both at rank 1.
        "q1": _ranking("q1", sparse=["g1", "n1"], dense=["n1", "n2", "g1"],
                       hybrid=["g1", "n1"]),
        "q2": _ranking("q2", sparse=["n1", "n2"], dense=["g2"],
                       hybrid=["g2", "n1"]),
    }

    def summary(self):
        return scoring.summarize(self.queries, self.records, self.rankings)

    def test_the_denominators_are_the_answerable_ones(self):
        summary = self.summary()
        assert summary["queries"]["total"] == 3
        assert summary["queries"]["answerable"] == 2
        assert summary["queries"]["excluded_unanswerable"] == 1

    def test_recall_at_one_per_arm_pooled(self):
        arms = self.summary()["arms"]
        assert arms["sparse"][1]["pooled"]["hits"] == 1
        assert arms["dense"][1]["pooled"]["hits"] == 1
        assert arms["hybrid"][1]["pooled"]["hits"] == 2

    def test_recall_at_five_per_arm_pooled(self):
        arms = self.summary()["arms"]
        assert arms["sparse"][5]["pooled"]["hits"] == 1
        assert arms["dense"][5]["pooled"]["hits"] == 2
        assert arms["hybrid"][5]["pooled"]["hits"] == 2

    def test_each_stratum_is_reported_separately(self):
        arms = self.summary()["arms"]
        assert arms["sparse"][5]["exact_entity"]["hits"] == 1
        assert arms["sparse"][5]["conceptual"]["hits"] == 0
        assert arms["dense"][5]["conceptual"]["hits"] == 1

    def test_the_unanswerable_query_is_in_no_denominator(self):
        arms = self.summary()["arms"]
        for arm in scoring.ARMS:
            for k in scoring.K_VALUES:
                assert arms[arm][k]["pooled"]["n"] == 2

    def test_every_arm_pair_is_compared_at_every_k(self):
        comparisons = self.summary()["comparisons"]
        keys = {(c["arm_a"], c["arm_b"], c["k"], c["stratum"])
                for c in comparisons}
        assert ("hybrid", "sparse", 1, "pooled") in keys
        assert ("hybrid", "dense", 5, "pooled") in keys
        assert ("dense", "sparse", 1, "exact_entity") in keys

    def test_a_comparison_carries_its_discordant_pairs(self):
        comparisons = self.summary()["comparisons"]
        pair = next(c for c in comparisons
                    if (c["arm_a"], c["arm_b"], c["k"], c["stratum"])
                    == ("hybrid", "sparse", 1, "pooled"))
        assert (pair["b"], pair["c"]) == (1, 0)

    def test_the_per_query_detail_names_the_gold_and_the_outcome(self):
        detail = self.summary()["per_query"]
        assert detail["q1"]["gold"] == ["g1"]
        assert detail["q1"]["arms"]["sparse"][1] is True
        assert detail["q2"]["arms"]["sparse"][5] is False

    def test_a_missing_ranking_is_refused(self):
        """A rankings file that lost a query would otherwise shrink the
        denominator, and a smaller denominator with the same hits is a higher
        recall."""
        rankings = {"q1": self.rankings["q1"]}
        with pytest.raises(ValueError, match="q2"):
            scoring.summarize(self.queries, self.records, rankings)

    def test_a_ranking_for_an_unknown_query_is_refused(self):
        rankings = dict(self.rankings, q9=_ranking("q9", sparse=["g1"]))
        with pytest.raises(ValueError, match="q9"):
            scoring.summarize(self.queries, self.records, rankings)

    def test_the_duplicate_span_advisory_count_is_reported(self):
        """AMENDMENT 5 requires it alongside the results. Counted from the
        store here rather than copied from the freeze, so a store that moved
        shows up as a disagreement."""
        records = self.records + [_chunk("g1b", "the first span",
                                         accession="acc-2")]
        summary = scoring.summarize(self.queries, records, self.rankings)
        assert summary["duplicate_span_advisories"] == 1
        assert summary["queries"]["answerable"] == 2
