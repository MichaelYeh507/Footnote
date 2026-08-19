"""Reciprocal Rank Fusion, against hand-computed values.

Plan §6 puts this second in the test priority order, behind only the eval
harness, and for the same reason: RRF is four lines of arithmetic that decides
the hybrid arm's entire ranking. A sign error or an off-by-one in the rank base
produces a plausible ordering that is quietly wrong, and no downstream number
would reveal it -- the hybrid arm would simply score worse than it should, which
reads as a finding rather than as a bug.

Every expected value below is computed by hand in the test that uses it, with
the arithmetic written out. A test that calls the implementation to build its
own expectation would pass against any implementation.

The constants are pre-registered (EVALUATION-SPEC.md, 2026-08-19): k = 60,
1-based ranks, fusion depth 50, ties broken by chunk_id ascending.

Written before services/fusion.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fusion  # noqa: E402


class TestThePreRegisteredConstants:
    """These are published. A change here is a change to a published document."""

    def test_k_is_sixty(self):
        assert fusion.RRF_K == 60

    def test_fusion_depth_is_fifty(self):
        assert fusion.FUSION_DEPTH == 50


class TestTheArithmetic:

    def test_one_arm_one_document(self):
        """1/(60+1) = 1/61 = 0.016393442622950821..."""
        scored = fusion.reciprocal_rank_fusion([["a"]])
        assert scored[0][0] == "a"
        assert scored[0][1] == pytest.approx(1 / 61)

    def test_ranks_are_one_based_not_zero_based(self):
        """The whole hand-check: a zero-based implementation gives 1/60 for the
        top document, which is 0.0166667 rather than 0.0163934. Both look
        reasonable; only one is RRF."""
        scored = fusion.reciprocal_rank_fusion([["a"]])
        assert scored[0][1] == pytest.approx(0.016393442622950821)
        assert scored[0][1] != pytest.approx(1 / 60)

    def test_two_arms_agreeing_sum_their_contributions(self):
        """`a` is rank 1 in both arms: 1/61 + 1/61 = 2/61 = 0.032786885...
        `b` is rank 2 in both:        1/62 + 1/62 = 2/62 = 0.032258064..."""
        scored = fusion.reciprocal_rank_fusion([["a", "b"], ["a", "b"]])
        assert dict(scored)["a"] == pytest.approx(2 / 61)
        assert dict(scored)["b"] == pytest.approx(2 / 62)
        assert [c for c, _ in scored] == ["a", "b"]

    def test_a_document_absent_from_an_arm_contributes_nothing_from_it(self):
        """Pre-registered explicitly, because the tempting alternative -- a
        penalty term, or a notional rank of depth+1 -- changes every ranking.

        `a`: arm one rank 1, arm two absent      -> 1/61          = 0.0163934
        `b`: arm one rank 2, arm two rank 1      -> 1/62 + 1/61   = 0.0325235
        So `b` outranks `a`, which is the point of fusing at all.
        """
        scored = fusion.reciprocal_rank_fusion([["a", "b"], ["b"]])
        ranking = dict(scored)
        assert ranking["a"] == pytest.approx(1 / 61)
        assert ranking["b"] == pytest.approx(1 / 62 + 1 / 61)
        assert [c for c, _ in scored] == ["b", "a"]

    def test_the_worked_three_arm_example(self):
        """One example computed by hand end to end, per plan §6.

        sparse: [x, y, z]      dense: [y, x, w]      hybrid input is both.

        x: 1/61 + 1/62 = 0.016393442622950821 + 0.016129032258064516
                       = 0.032522474881015337
        y: 1/62 + 1/61 = the same sum          = 0.032522474881015337
        z: 1/63        = 0.015873015873015872
        w: 1/63        = 0.015873015873015872

        x and y tie exactly, as do z and w -- which is what makes this example
        worth hand-computing: it exercises the tie-break rule as well as the
        arithmetic.
        """
        scored = fusion.reciprocal_rank_fusion([["x", "y", "z"], ["y", "x", "w"]])
        ranking = dict(scored)
        assert ranking["x"] == pytest.approx(0.032522474881015337)
        assert ranking["y"] == pytest.approx(0.032522474881015337)
        assert ranking["z"] == pytest.approx(0.015873015873015872)
        assert ranking["w"] == pytest.approx(0.015873015873015872)
        # Ties break by chunk_id ascending: x before y, w before z.
        assert [c for c, _ in scored] == ["x", "y", "w", "z"]


class TestTheTieBreak:
    """Pre-registered as chunk_id ascending: arbitrary, but deterministic and
    independent of every arm's score, so recall@1 is reproducible."""

    def test_ties_break_by_chunk_id_ascending(self):
        scored = fusion.reciprocal_rank_fusion([["b", "a"], ["a", "b"]])
        # Both hold 1/61 + 1/62. Only the id decides.
        assert [c for c, _ in scored] == ["a", "b"]

    def test_the_tie_break_does_not_override_the_score(self):
        """`z` sorts last alphabetically and must still win on score, or the
        tie-break has quietly become the ranking."""
        scored = fusion.reciprocal_rank_fusion([["z", "a"], ["z", "a"]])
        assert [c for c, _ in scored] == ["z", "a"]

    def test_input_order_does_not_change_the_result(self):
        """Same arms, arms passed in the other order. RRF is a sum, so the
        result must be identical -- including the tie-break."""
        one = fusion.reciprocal_rank_fusion([["x", "y", "z"], ["y", "x", "w"]])
        two = fusion.reciprocal_rank_fusion([["y", "x", "w"], ["x", "y", "z"]])
        assert [c for c, _ in one] == [c for c, _ in two]


class TestFusionDepth:

    def test_only_the_top_depth_of_each_arm_contributes(self):
        """A document ranked past the depth cannot be rescued by the other arm.

        With depth 2, `c` at rank 3 in arm one is not in the fusion at all, so
        its only contribution comes from arm two.
        """
        scored = fusion.reciprocal_rank_fusion(
            [["a", "b", "c"], ["c"]], depth=2,
        )
        ranking = dict(scored)
        assert ranking["c"] == pytest.approx(1 / 61), (
            "c should carry arm two's rank-1 contribution only"
        )
        assert ranking["a"] == pytest.approx(1 / 61)
        assert ranking["b"] == pytest.approx(1 / 62)

    def test_a_document_beyond_depth_in_every_arm_is_absent_entirely(self):
        scored = fusion.reciprocal_rank_fusion([["a", "b", "c"]], depth=2)
        assert "c" not in dict(scored)

    def test_the_default_depth_is_the_pre_registered_fifty(self):
        arm = [f"chunk{i:03d}" for i in range(80)]
        scored = fusion.reciprocal_rank_fusion([arm])
        assert len(scored) == 50


class TestTheDegenerateCases:
    """Each of these returned something plausible-but-wrong in an early draft."""

    def test_no_arms_returns_empty(self):
        assert fusion.reciprocal_rank_fusion([]) == []

    def test_all_arms_empty_returns_empty(self):
        assert fusion.reciprocal_rank_fusion([[], []]) == []

    def test_one_empty_arm_does_not_suppress_the_other(self):
        """A sparse query matching nothing is an ordinary outcome, not a reason
        for the hybrid arm to return nothing."""
        scored = fusion.reciprocal_rank_fusion([[], ["a", "b"]])
        assert [c for c, _ in scored] == ["a", "b"]

    def test_a_duplicate_within_one_arm_is_counted_once(self):
        """Postgres will not return the same chunk_id twice, but a defensive
        union of two queries could. Counting it twice would let one arm
        outvote the other."""
        scored = fusion.reciprocal_rank_fusion([["a", "a", "b"]])
        assert dict(scored)["a"] == pytest.approx(1 / 61)
        assert len(scored) == 2


class TestTheOutputShape:

    def test_returns_pairs_sorted_by_descending_score(self):
        scored = fusion.reciprocal_rank_fusion([["a", "b", "c"]])
        scores = [s for _, s in scored]
        assert scores == sorted(scores, reverse=True)

    def test_k_is_a_parameter_but_defaults_to_the_pre_registered_value(self):
        """Overridable so the test above can hand-compute with a small k, and
        never overridden in the eval -- a k chosen after seeing recall@k is a
        dial. 1/(10+1) = 1/11."""
        scored = fusion.reciprocal_rank_fusion([["a"]], k=10)
        assert scored[0][1] == pytest.approx(1 / 11)
