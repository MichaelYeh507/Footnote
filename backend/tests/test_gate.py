"""The Phase 3b gate: its statistic, its null model, and its threshold.

PRE-REGISTERED 2026-08-20 in `EVALUATION-SPEC.md`, appendix *PHASE 3b, a fourth
arm*, and published before this file was written. **That pre-registration is
post-hoc** -- the arm is designed after Phase 3's numbers were known -- which
raises rather than lowers the bar on these tests: the one part of 3b that is
genuinely uncontaminated is that the threshold comes from the store and never
from recall, and that property is only worth anything if it is enforced here.

Four failures this file is written against, every one of them silent:

  **Re-stemming the null bags.** The bags are lexemes taken out of the index's
  own `tsvector` via `ts_stat`, so they are already stemmed. Passing them
  through `plainto_tsquery` or `to_tsquery` runs the `english` dictionary a
  second time, which re-parses compound tokens and produces a *different* null
  with nothing anywhere to say so. The known-positive control is a hyphenated
  lexeme that survives a direct cast and is split by a re-parse.

  **The boundary going the wrong way.** The rule is `s1 <= tau` fires the gate
  and `s1 > tau` does not. An off-by-one at the boundary changes which queries
  are gated and no published number would reveal it.

  **A percentile that interpolates.** The pre-registration fixes the
  *nearest-rank* definition, so `tau` is a value that was actually observed. An
  interpolating percentile (numpy's default) returns a number that appeared in
  no null draw.

  **Sampling with replacement.** `random.choices` samples with replacement, so
  a bag of "8 lexemes" can hold the same stem twice -- sizing the null against
  a term the arm only matches once, and against a query definition that counts
  distinct lexemes.
"""

import pathlib
import random
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import gate  # noqa: E402
from services import fusion, retrieval  # noqa: E402


class TestTheLexemeCount:
    """`L` is the number of DISTINCT lexemes in the query's OR-tsquery."""

    def test_it_reuses_the_arm_s_own_lexeme_pattern(self):
        """Not a re-implementation.

        A second regex here would agree with `services/retrieval.py` until the
        day it did not, and the day it did not would be a silently different
        `L` and therefore a silently different null.
        """
        assert gate.LEXEME_PATTERN is retrieval._TSQUERY_LEXEME

    def test_it_counts_distinct_not_total(self):
        """`plainto_tsquery` emits a repeated stem when the question repeats a
        word. q001's real tsquery carries `'mani'` twice."""
        tsquery = "'mani' | 'primari' | 'supplier' | 'mani' | 'product'"
        assert gate.lexemes_of(tsquery) == [
            "mani", "primari", "supplier", "product"]
        assert gate.lexeme_count(tsquery) == 4

    def test_it_preserves_order_of_first_appearance(self):
        assert gate.lexemes_of("'b' | 'a' | 'b' | 'c'") == ["b", "a", "c"]

    def test_it_unescapes_a_doubled_quote(self):
        """A lexeme holding an apostrophe is stored doubled inside the quotes."""
        assert gate.lexemes_of("'o''brien' | 'corp'") == ["o'brien", "corp"]

    def test_no_lexemes_is_zero_not_an_error(self):
        """An all-stopword query is an ordinary outcome; the arm returns
        nothing and the gate sees s1 = 0."""
        assert gate.lexemes_of(None) == []
        assert gate.lexeme_count(None) == 0
        assert gate.lexemes_of("") == []


class TestTheGateBoundary:
    """`s1 <= tau` fires. `s1 > tau` does not. The equality case is the one
    that an off-by-one moves, and it is pinned in both directions."""

    def test_below_fires(self):
        assert gate.gate_fires(s1=1.0, tau=2.0) is True

    def test_above_does_not_fire(self):
        assert gate.gate_fires(s1=3.0, tau=2.0) is False

    def test_exactly_at_the_threshold_fires(self):
        assert gate.gate_fires(s1=2.0, tau=2.0) is True

    def test_a_hair_above_does_not_fire(self):
        assert gate.gate_fires(s1=2.0000001, tau=2.0) is False

    def test_zero_evidence_fires_against_any_nonnegative_threshold(self):
        """`ts_rank_cd` is non-negative, so a query the arm could not score at
        all must always be gated."""
        assert gate.gate_fires(s1=0.0, tau=0.0) is True


class TestTheNearestRankPercentile:
    """The pre-registration fixes nearest-rank, so tau is an observed value."""

    def test_it_returns_a_value_that_was_actually_observed(self):
        values = [0.1, 0.2, 0.3, 0.4]
        assert gate.nearest_rank_percentile(values, 95) in values

    def test_it_does_not_interpolate_between_neighbours(self):
        """The interpolating definition of p95 over [0, 100] is 95.0, which
        appears in no draw. Nearest-rank returns 100."""
        values = [0.0, 100.0]
        assert gate.nearest_rank_percentile(values, 95) == 100.0

    def test_the_index_is_ceiling_not_floor(self):
        """p95 of 1000 sorted values is the 950th, i.e. index 949."""
        values = [float(i) for i in range(1000)]
        assert gate.nearest_rank_percentile(values, 95) == 949.0

    def test_it_sorts_before_indexing(self):
        values = [5.0, 1.0, 3.0, 2.0, 4.0]
        assert gate.nearest_rank_percentile(values, 100) == 5.0
        assert gate.nearest_rank_percentile(values, 20) == 1.0

    def test_an_empty_sample_is_refused_rather_than_defaulted(self):
        """A tau of 0.0 from an empty draw would gate nothing and look like a
        measurement."""
        with pytest.raises(ValueError, match="no values"):
            gate.nearest_rank_percentile([], 95)


class TestThePreRegisteredConstants:
    """The parameters of 3b, pinned the way `RRF_K` is.

    Added after a perturbation survived: changing `NULL_PERCENTILE` from 95 to
    90 broke nothing, and it is the one free parameter of this whole arm. A
    percentile nothing can detect is a percentile that could have been chosen
    after seeing the result, which is the exact failure the pre-registration is
    written to make impossible.
    """

    def test_the_percentile_is_ninety_five(self):
        """Not a fresh choice: it is the project's own alpha, the same 95%
        every Wilson interval in RESULTS.md is computed at."""
        assert gate.NULL_PERCENTILE == 95

    def test_the_default_percentile_is_the_pre_registered_one(self):
        """The constant and the default must not drift apart, or the published
        number would come from one and the pinned value from the other."""
        values = [float(i) for i in range(1000)]
        assert (gate.nearest_rank_percentile(values)
                == gate.nearest_rank_percentile(values, gate.NULL_PERCENTILE))

    def test_the_draw_is_a_thousand_bags_per_size(self):
        assert gate.NULL_BAGS_PER_SIZE == 1000

    def test_the_seed_is_the_pre_registered_one(self):
        assert gate.NULL_SEED == 20260820


class TestTheNullBagSampling:

    VOCAB = ["alpha", "beta", "gamma", "delta", "epsilon"]
    WEIGHTS = [100, 50, 25, 10, 1]

    def test_a_bag_holds_exactly_k_distinct_lexemes(self):
        rng = random.Random(20260820)
        for _ in range(50):
            bag = gate.weighted_sample_without_replacement(
                rng, self.VOCAB, self.WEIGHTS, 3)
            assert len(bag) == 3
            assert len(set(bag)) == 3

    def test_the_same_seed_gives_the_same_bags(self):
        a = gate.weighted_sample_without_replacement(
            random.Random(20260820), self.VOCAB, self.WEIGHTS, 3)
        b = gate.weighted_sample_without_replacement(
            random.Random(20260820), self.VOCAB, self.WEIGHTS, 3)
        assert a == b

    def test_a_different_seed_gives_different_bags(self):
        """Otherwise the seed is decorative and 1,000 draws are one draw."""
        seen = {
            tuple(gate.weighted_sample_without_replacement(
                random.Random(seed), self.VOCAB, self.WEIGHTS, 3))
            for seed in range(40)
        }
        assert len(seen) > 1

    def test_weight_is_load_bearing(self):
        """The heaviest lexeme must appear far more often than the lightest.

        Without this the sampler could ignore weights entirely and every test
        above would still pass -- and the null would be built from rare terms,
        which is the direction that makes it artificially easy.
        """
        rng = random.Random(20260820)
        counts = {word: 0 for word in self.VOCAB}
        for _ in range(400):
            for word in gate.weighted_sample_without_replacement(
                    rng, self.VOCAB, self.WEIGHTS, 1):
                counts[word] += 1
        assert counts["alpha"] > counts["epsilon"] * 5

    def test_asking_for_more_than_the_vocabulary_is_refused(self):
        rng = random.Random(20260820)
        with pytest.raises(ValueError, match="distinct"):
            gate.weighted_sample_without_replacement(
                rng, self.VOCAB, self.WEIGHTS, len(self.VOCAB) + 1)

    def test_a_zero_weight_lexeme_is_never_drawn(self):
        rng = random.Random(20260820)
        drawn = set()
        for _ in range(200):
            drawn.update(gate.weighted_sample_without_replacement(
                rng, ["a", "b"], [1, 0], 1))
        assert drawn == {"a"}


class TestTheBagBecomesATsquery:

    def test_it_quotes_and_ors_every_lexeme(self):
        assert gate.bag_to_tsquery_text(["alpha", "beta"]) == "'alpha' | 'beta'"

    def test_it_doubles_an_embedded_quote(self):
        """`'o'brien'` is a syntax error; `'o''brien'` is the lexeme."""
        assert gate.bag_to_tsquery_text(["o'brien"]) == "'o''brien'"

    def test_it_round_trips_through_the_lexeme_reader(self):
        bag = ["alpha", "o'brien", "zero-trust", "5.7"]
        assert gate.lexemes_of(gate.bag_to_tsquery_text(bag)) == bag

    def test_an_empty_bag_is_refused(self):
        """An empty tsquery text would cast to NULL and the arm would return
        nothing -- a null draw scoring 0.0 for a reason unrelated to the null."""
        with pytest.raises(ValueError, match="empty"):
            gate.bag_to_tsquery_text([])


class TestTheGatedArmIsBuiltFromTheOtherTwo:
    """`gated` reuses the published RRF rather than introducing new fusion
    math: not gated is `rrf([sparse, dense])`, gated is `rrf([dense])`."""

    SPARSE = [f"s{i:03d}" for i in range(50)]
    DENSE = [f"d{i:03d}" for i in range(50)]

    def test_when_the_gate_does_not_fire_it_is_identical_to_hybrid(self):
        gated = gate.gated_ranking(self.SPARSE, self.DENSE, s1=9.0, tau=1.0)
        hybrid = fusion.reciprocal_rank_fusion([self.SPARSE, self.DENSE])
        assert gated == hybrid

    def test_when_the_gate_fires_the_order_is_the_dense_order_unchanged(self):
        gated = gate.gated_ranking(self.SPARSE, self.DENSE, s1=0.5, tau=1.0)
        assert [chunk_id for chunk_id, _ in gated] == self.DENSE

    def test_when_the_gate_fires_no_sparse_chunk_appears_at_all(self):
        """"Contributes no votes" means absent, not down-weighted. A sparse-only
        chunk surviving anywhere in the list is the failure this arm exists to
        remove."""
        gated = gate.gated_ranking(self.SPARSE, self.DENSE, s1=0.5, tau=1.0)
        ids = {chunk_id for chunk_id, _ in gated}
        assert not (ids & set(self.SPARSE))

    def test_the_gated_scores_stay_in_rrf_units(self):
        """Not dense distances. Mixing two score semantics into one column
        would make the stored file unreadable by hand."""
        gated = gate.gated_ranking(self.SPARSE, self.DENSE, s1=0.5, tau=1.0)
        assert gated[0][1] == pytest.approx(1.0 / (fusion.RRF_K + 1))

    def test_it_does_not_move_k_or_the_depth(self):
        """3b changes the arm, not the pre-registered fusion parameters."""
        assert fusion.RRF_K == 60
        assert fusion.FUSION_DEPTH == 50

    def test_the_fusion_depth_still_binds_when_the_gate_fires(self):
        """A 60-long dense list must still be cut at 50 -- otherwise the gated
        arm quietly searches deeper than the arm it is compared against."""
        deep = [f"d{i:03d}" for i in range(60)]
        gated = gate.gated_ranking(self.SPARSE, deep, s1=0.5, tau=1.0)
        assert len(gated) == fusion.FUSION_DEPTH


# --------------------------------------------------------------------------
# Live. Skips cleanly without DATABASE_URL. No OpenAI call is made here.
# --------------------------------------------------------------------------

live = pytest.mark.live


@pytest.fixture(scope="module")
def cursor():
    database = pytest.importorskip("database")
    try:
        url = database.url()
    except RuntimeError as exc:
        pytest.skip(str(exc).splitlines()[0])
    import psycopg
    try:
        connection = psycopg.connect(url, connect_timeout=10, autocommit=True)
    except Exception as exc:
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    with connection:
        with connection.cursor() as cur:
            cur.execute("select to_regclass('public.chunks')")
            if cur.fetchone()[0] is None:
                pytest.skip("chunks table does not exist (migration 003)")
            yield cur


@live
class TestTheNullBagsAreNotReStemmed:
    """The failure the pre-registration names explicitly, with a control."""

    def test_a_compound_lexeme_survives_a_direct_cast(self, cursor):
        """KNOWN-POSITIVE CONTROL for "do not re-stem".

        `zero-trust` is one lexeme in this store's vocabulary. Cast directly it
        stays one term; put through `plainto_tsquery` it is re-parsed into
        three. If the measurement ever routes bags through the parser, this
        test is what says so.
        """
        text = gate.bag_to_tsquery_text(["zero-trust"])
        cursor.execute("select %s::tsquery::text", (text,))
        direct = cursor.fetchone()[0]
        assert gate.lexemes_of(direct) == ["zero-trust"]

        cursor.execute("select plainto_tsquery('english', %s)::text",
                       ("zero-trust",))
        reparsed = cursor.fetchone()[0]
        assert len(gate.lexemes_of(reparsed)) > 1

    def test_the_vocabulary_reader_returns_lexemes_and_frequencies(self, cursor):
        vocab = gate.store_vocabulary(cursor)
        assert len(vocab) > 10_000
        words = [word for word, _n in vocab]
        assert len(set(words)) == len(words)
        assert all(count >= 1 for _w, count in vocab)

    def test_the_vocabulary_holds_stems_not_surface_forms(self, cursor):
        """`ts_stat` reads the index, so it returns what the arm matches
        against. A vocabulary of surface forms would build a null the arm can
        never score."""
        vocab = dict(gate.store_vocabulary(cursor))
        assert "compani" in vocab
        assert "company" not in vocab

    def test_a_null_bag_scores_through_the_same_sparse_path(self, cursor):
        """End to end: draw, cast, search, read a top score."""
        vocab = gate.store_vocabulary(cursor)
        rng = random.Random(20260820)
        bag = gate.weighted_sample_without_replacement(
            rng, [w for w, _ in vocab], [n for _, n in vocab], 8)
        score = gate.null_top_score(cursor, bag)
        assert score > 0.0

    def test_a_bag_of_one_rare_lexeme_still_returns_a_score(self, cursor):
        """The floor case: one term, few matches, but a real cover-density
        score rather than an exception or a silent zero."""
        score = gate.null_top_score(cursor, ["compani"])
        assert score > 0.0

    def test_null_top_score_itself_does_not_re_stem(self, cursor):
        """The control above proves a direct cast survives; this one proves the
        function that actually runs the null uses one.

        Written because the first version of this file tested the *cast* and
        not `null_top_score`, so a perturbation routing the measurement through
        `plainto_tsquery` passed everything -- the test selected on the thing it
        was checking. Here the two paths are computed side by side and required
        to differ, then the function is required to match the direct one.

        The bag is multi-lexeme on purpose. A single compound lexeme was tried
        first and could not discriminate: `zero-trust` scores 0.1 down either
        path, because a chunk holding the compound also holds its parts. Two
        lexemes separate the paths on a second axis as well -- the direct cast
        ORs while `plainto_tsquery` ANDs -- so this control now bites on a
        re-parse and on an AND/OR slip alike.
        """
        bag = ["zero-trust", "compani"]
        text = gate.bag_to_tsquery_text(bag)

        cursor.execute("select %s::tsquery::text", (text,))
        direct = retrieval.sparse_search(cursor, cursor.fetchone()[0])
        direct_top = direct[0][1] if direct else 0.0

        cursor.execute("select plainto_tsquery('english', %s)::text",
                       (" ".join(bag),))
        reparsed = retrieval.sparse_search(cursor, cursor.fetchone()[0])
        reparsed_top = reparsed[0][1] if reparsed else 0.0

        assert direct_top != reparsed_top, (
            "the two paths agree, so this test cannot detect a re-parse; pick "
            "lexemes the english dictionary splits or that OR and AND differ on")
        assert gate.null_top_score(cursor, bag) == direct_top

    def test_null_top_score_is_the_maximum_not_some_other_row(self, cursor):
        """`sparse_search` returns score-descending, so the top is row 0. A
        perturbation reading the last row instead still returns a positive
        number, which "score > 0" cannot tell apart."""
        vocab = gate.store_vocabulary(cursor)
        rng = random.Random(20260820)
        bag = gate.weighted_sample_without_replacement(
            rng, [w for w, _ in vocab], [n for _, n in vocab], 8)
        text = gate.bag_to_tsquery_text(bag)
        cursor.execute("select %s::tsquery::text", (text,))
        rows = retrieval.sparse_search(cursor, cursor.fetchone()[0])
        scores = [score for _chunk_id, score in rows]
        assert min(scores) < max(scores), (
            "this bag has no score spread, so the assertion below cannot bite")
        assert gate.null_top_score(cursor, bag) == max(scores)
