"""The three arms, against fakes and against the live indexes.

Every parameter exercised here was pre-registered in `EVALUATION-SPEC.md` on
2026-08-19, before either index existed and before any query was written. So
these tests are not checking that the code does something reasonable; they are
checking that it does the *declared* thing, and that each declared parameter is
load-bearing rather than decorative. A parameter nothing can detect is a
parameter that could have been anything.

Three failures this file is written against, all silent:

  **AND instead of OR.** `plainto_tsquery` ANDs its terms. Against a twelve-word
  conceptual query the modal outcome of an AND is zero rows, and a sparse arm
  scoring near zero would read as a property of lexical retrieval rather than as
  a parser artifact. The known-positive control is a query where AND returns
  nothing and OR returns thousands.

  **`ef_search` never applied.** `hnsw.ef_search` is a pgvector GUC, and until
  pgvector's library is loaded into the backend Postgres does not recognise the
  name at all -- `show hnsw.ef_search` on a fresh connection raises
  `UndefinedObject`. A `SET` that silently failed to take would leave the arm
  running at the server default with nothing in any output to say so. The
  control is that `ef_search = 10` caps a depth-50 request at ten rows.

  **The tie-break not applied.** Measured on the live store: the top 50 for
  `goodwill | impairment | charge` holds twelve distinct `ts_rank_cd` scores,
  and rank 1 is itself a two-way tie. So the tie-break is not a formality here;
  it decides recall@1.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import fusion, retrieval  # noqa: E402


class FakeCursor:
    """Records every statement and replays canned rows, in order.

    `execute` takes the next result off the queue, so a test can assert both
    what was asked and what the code did with the answer. A statement issued
    with no result queued raises rather than returning `[]`: an unqueued
    statement is one the test did not expect, and returning empty would let it
    pass as an arm that legitimately matched nothing.
    """

    def __init__(self, results=()):
        self.results = list(results)
        self.statements = []
        self._rows = None

    def execute(self, sql, params=None):
        self.statements.append((" ".join(str(sql).split()), params))
        if not self.results:
            raise AssertionError(
                f"unexpected statement with no queued result: {sql!r}")
        self._rows = self.results.pop(0)

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    @property
    def sql(self):
        return [statement for statement, _ in self.statements]


class FakeEmbeddings:
    def __init__(self, vectors):
        self.vectors = vectors
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        data = [type("Item", (), {"embedding": v})() for v in self.vectors]
        return type("Response", (), {"data": data})()


class FakeClient:
    def __init__(self, vectors):
        self.embeddings = FakeEmbeddings(vectors)


class TestThePreRegisteredParameters:
    """The constants themselves. Cheap, and the first thing to fail if a
    parameter is ever adjusted after a number exists."""

    def test_the_tsvector_configuration_is_english(self):
        assert retrieval.TS_CONFIG == "english"

    def test_rank_normalization_is_zero(self):
        """No length normalization: the chunker already fixed length at a
        512-token target with a measured median of 460."""
        assert retrieval.RANK_NORMALIZATION == 0

    def test_ef_search_is_one_hundred(self):
        assert retrieval.EF_SEARCH == 100

    def test_ef_search_clears_the_depth(self):
        """`ef_search` below the requested depth degrades results quietly.
        Stated in the pre-registration as the reason for 100, pinned here."""
        assert retrieval.EF_SEARCH >= 2 * retrieval.DEPTH

    def test_depth_is_the_pre_registered_fifty(self):
        assert retrieval.DEPTH == 50

    def test_depth_is_the_same_fifty_fusion_uses(self):
        """Two constants named 50 that could drift apart would let an arm
        retrieve deeper than the fusion it feeds, and the extra would be
        discarded without a word."""
        assert retrieval.DEPTH == fusion.FUSION_DEPTH

    def test_the_embedding_model_and_width(self):
        assert retrieval.EMBED_MODEL == "text-embedding-3-small"
        assert retrieval.EMBED_DIMENSIONS == 1536

    def test_the_query_is_embedded_by_the_same_model_as_the_chunks(self):
        """The drift guard, not a restatement.

        `scripts/embed_chunks.py` holds its own copies of these constants and
        has already been run: 11,621 vectors exist under them. A query embedded
        by a different model or at a different width would be compared against
        vectors from another space, and cosine distance would still return
        fifty rows in confident order.
        """
        sys.path.insert(0, str(BACKEND / "scripts"))
        import embed_chunks

        assert retrieval.EMBED_MODEL == embed_chunks.MODEL
        assert retrieval.EMBED_DIMENSIONS == embed_chunks.DIMENSIONS
        assert embed_chunks.REQUEST_DIMENSIONS is None


class TestTheOrTsquery:

    def test_it_asks_postgres_for_the_lexemes_of_plainto_tsquery(self):
        """The pre-registration is specific about the source of the lexemes:
        `plainto_tsquery('english', ...)`, joined with `|`. Taking them from
        `to_tsvector` instead would be a different function of the query."""
        cursor = FakeCursor([[("'a' | 'b'",)]])
        retrieval.or_tsquery(cursor, "anything")
        statement = cursor.sql[0]
        assert "plainto_tsquery('english'" in statement
        assert " | " in statement

    def test_it_returns_what_postgres_built(self):
        cursor = FakeCursor([[("'goodwil' | 'impair'",)]])
        assert retrieval.or_tsquery(cursor, "goodwill impairment") == \
            "'goodwil' | 'impair'"

    def test_a_query_of_only_stopwords_returns_none(self):
        """`plainto_tsquery('english', 'the and of')` is the empty tsquery, and
        the aggregate over no lexemes is NULL. None rather than '' so a caller
        cannot pass it to a search and match nothing for an unexplained
        reason."""
        cursor = FakeCursor([[(None,)]])
        assert retrieval.or_tsquery(cursor, "the and of") is None

    def test_it_refuses_blank_query_text(self):
        with pytest.raises(ValueError, match="empty"):
            retrieval.or_tsquery(FakeCursor(), "   ")


class TestTheSparseSearch:

    def test_it_ranks_with_ts_rank_cd_at_the_declared_normalization(self):
        cursor = FakeCursor([[]])
        retrieval.sparse_search(cursor, "'a' | 'b'")
        assert "ts_rank_cd(tsv, %(tsquery)s::tsquery, 0)" in cursor.sql[0]

    def test_it_breaks_ties_by_chunk_id_ascending_in_sql(self):
        """Measured on the live store: twelve distinct scores among the top 50,
        and rank 1 is a two-way tie. Ordering by score alone would leave
        recall@1 at the mercy of physical row order."""
        cursor = FakeCursor([[]])
        retrieval.sparse_search(cursor, "'a' | 'b'")
        assert "order by score desc, chunk_id asc" in cursor.sql[0]

    def test_it_limits_to_the_pre_registered_depth(self):
        cursor = FakeCursor([[]])
        retrieval.sparse_search(cursor, "'a' | 'b'")
        assert cursor.statements[0][1]["depth"] == 50

    def test_it_returns_chunk_id_score_pairs_in_the_order_postgres_gave(self):
        cursor = FakeCursor([[("c2", 2.9), ("c7", 2.9), ("c1", 1.0)]])
        assert retrieval.sparse_search(cursor, "'a'") == \
            [("c2", 2.9), ("c7", 2.9), ("c1", 1.0)]

    def test_a_none_tsquery_returns_nothing_without_touching_the_table(self):
        """An all-stopword query is an ordinary outcome and RRF is built to be
        handed an empty list. Issuing `tsv @@ NULL` would scan the table to
        return the same nothing."""
        cursor = FakeCursor()
        assert retrieval.sparse_search(cursor, None) == []
        assert cursor.statements == []

    def test_it_refuses_a_negative_or_zero_depth(self):
        with pytest.raises(ValueError, match="depth"):
            retrieval.sparse_search(FakeCursor(), "'a'", depth=0)


class TestEfSearch:

    def test_it_sets_and_reads_back(self):
        cursor = FakeCursor([[], [("100",)]])
        assert retrieval.set_ef_search(cursor) == 100
        assert cursor.sql[0] == "set hnsw.ef_search = 100"
        assert cursor.sql[1] == "show hnsw.ef_search"

    def test_it_raises_when_the_server_reports_a_different_value(self):
        """The failure this exists for: `hnsw.ef_search` is a pgvector GUC, and
        a name Postgres does not recognise as a real setting can be accepted as
        a placeholder and never applied."""
        cursor = FakeCursor([[], [("40",)]])
        with pytest.raises(RuntimeError, match="40"):
            retrieval.set_ef_search(cursor)

    def test_it_refuses_a_non_integer_value(self):
        """SET takes no parameters, so the value is formatted into the
        statement. Anything but a plain positive int is refused before it gets
        there."""
        for bad in ("100", 100.0, -1, 0, True):
            with pytest.raises((TypeError, ValueError)):
                retrieval.set_ef_search(FakeCursor(), bad)


class TestTheDenseSearch:

    def test_it_sets_ef_search_before_every_search(self):
        """Not hoisted to connection setup on purpose: a parameter applied once
        somewhere else is a parameter one caller eventually runs without."""
        cursor = FakeCursor([[], [("100",)], []])
        retrieval.dense_search(cursor, [0.1] * retrieval.EMBED_DIMENSIONS)
        assert cursor.sql[0] == "set hnsw.ef_search = 100"
        assert "embedding <=>" in cursor.sql[2]

    def test_it_orders_by_cosine_distance_then_chunk_id(self):
        cursor = FakeCursor([[], [("100",)], []])
        retrieval.dense_search(cursor, [0.1] * retrieval.EMBED_DIMENSIONS)
        search = cursor.sql[2]
        assert "<=>" in search, "cosine is the pre-registered operator"
        assert "order by distance, chunk_id" in search

    def test_it_limits_to_the_pre_registered_depth(self):
        cursor = FakeCursor([[], [("100",)], []])
        retrieval.dense_search(cursor, [0.1] * retrieval.EMBED_DIMENSIONS)
        assert cursor.statements[2][1]["depth"] == 50

    def test_it_sends_the_vector_in_pgvector_literal_form(self):
        cursor = FakeCursor([[], [("100",)], []])
        retrieval.dense_search(cursor, [0.5, -0.25] + [0.0] * 1534)
        sent = cursor.statements[2][1]["vector"]
        assert sent.startswith("[0.5,-0.25,0.0,")
        assert sent.endswith("]")

    def test_it_refuses_a_vector_of_the_wrong_width(self):
        """The column is `vector(1536)`. A short vector is a cast error at the
        server; refusing here names the model mismatch instead."""
        with pytest.raises(ValueError, match="1536"):
            retrieval.dense_search(FakeCursor(), [0.1] * 512)

    def test_it_returns_chunk_id_distance_pairs(self):
        cursor = FakeCursor([[], [("100",)], [("c1", 0.0), ("c2", 0.3)]])
        assert retrieval.dense_search(
            cursor, [0.1] * retrieval.EMBED_DIMENSIONS) == \
            [("c1", 0.0), ("c2", 0.3)]


class TestEmbeddingTheQuery:

    def test_the_query_text_is_sent_verbatim_with_no_prefix(self):
        """`text-embedding-3-*` is symmetric -- no query or document prefixes --
        and the chunks were embedded as bare text. A prefix on one side would
        make the comparison measure the prefix."""
        client = FakeClient([[0.1] * retrieval.EMBED_DIMENSIONS])
        retrieval.embed_query(client, "How many suppliers does Grainger use?")
        assert client.embeddings.calls[0]["input"] == \
            ["How many suppliers does Grainger use?"]

    def test_it_asks_for_the_pre_registered_model(self):
        client = FakeClient([[0.1] * retrieval.EMBED_DIMENSIONS])
        retrieval.embed_query(client, "q")
        assert client.embeddings.calls[0]["model"] == "text-embedding-3-small"

    def test_it_sends_no_dimensions_parameter(self):
        """Sending one would truncate, and a truncation is a parameter."""
        client = FakeClient([[0.1] * retrieval.EMBED_DIMENSIONS])
        retrieval.embed_query(client, "q")
        assert "dimensions" not in client.embeddings.calls[0]

    def test_it_returns_the_vector(self):
        vector = [0.25] * retrieval.EMBED_DIMENSIONS
        assert retrieval.embed_query(FakeClient([vector]), "q") == vector

    def test_it_refuses_a_response_of_the_wrong_width(self):
        with pytest.raises(ValueError, match="1536"):
            retrieval.embed_query(FakeClient([[0.1] * 512]), "q")

    def test_it_refuses_a_response_holding_more_than_one_vector(self):
        """One query in, one vector out. Two would mean the caller is being
        handed someone else's embedding by position."""
        client = FakeClient([[0.1] * 1536, [0.2] * 1536])
        with pytest.raises(ValueError, match="1 "):
            retrieval.embed_query(client, "q")

    def test_it_refuses_blank_query_text(self):
        with pytest.raises(ValueError, match="empty"):
            retrieval.embed_query(FakeClient([[0.1] * 1536]), "  \n ")


class TestTheEmbeddingClient:

    def test_it_reads_the_env_file_and_not_the_environment(self, monkeypatch,
                                                           tmp_path):
        """conftest seeds a dummy OPENAI_API_KEY into the environment. A client
        that read it would fail authentication at run time, and the message
        would point at credentials rather than at this."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-the-environment")
        env = tmp_path / ".env"
        env.write_text("OPENAI_API_KEY=sk-from-the-file\n", encoding="utf-8")
        assert retrieval.embedding_client(env).api_key == "sk-from-the-file"

    def test_it_raises_rather_than_building_a_keyless_client(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            retrieval.embedding_client(env)


class TestTheHybridArm:

    def test_it_fuses_exactly_the_two_arms_at_the_pre_registered_constants(self):
        sparse = ["a", "b", "c"]
        dense = ["c", "d"]
        assert retrieval.hybrid(sparse, dense) == \
            fusion.reciprocal_rank_fusion([sparse, dense],
                                          k=fusion.RRF_K,
                                          depth=fusion.FUSION_DEPTH)

    def test_nothing_past_the_pre_registered_depth_contributes(self):
        """The three-element example above cannot see a depth change: at that
        size every depth from 3 to 1000 gives the same answer. Caught by
        perturbation -- raising the fusion depth to 1000 broke nothing.

        A document ranked 51st by one arm cannot be rescued by the other. That
        is the pre-registered rule, and it is what makes depth a parameter
        rather than an implementation detail.
        """
        sparse = [f"s{i:03d}" for i in range(60)]
        dense = [f"d{i:03d}" for i in range(60)]
        fused = dict(retrieval.hybrid(sparse, dense))
        assert sparse[49] in fused and dense[49] in fused
        assert sparse[50] not in fused, "an arm's 51st entry reached fusion"
        assert dense[50] not in fused
        assert len(fused) == 2 * fusion.FUSION_DEPTH

    def test_a_document_both_arms_rank_beats_one_only_the_first_ranks(self):
        """The property the hybrid arm exists to have, asserted rather than
        assumed: agreement between arms outranks a single arm's top hit."""
        ranked = retrieval.hybrid(["top", "shared"], ["shared", "other"])
        assert ranked[0][0] == "shared"

    def test_either_arm_may_be_empty(self):
        assert retrieval.hybrid([], ["a"])[0][0] == "a"
        assert retrieval.hybrid(["a"], [])[0][0] == "a"
        assert retrieval.hybrid([], []) == []


class TestRankedIds:

    def test_it_drops_the_scores_and_keeps_the_order(self):
        assert retrieval.ranked_ids([("c2", 2.9), ("c1", 1.0)]) == ["c2", "c1"]

    def test_it_refuses_a_ranking_holding_a_repeated_chunk_id(self):
        """RRF counts a repeated id once, at its best rank, so a duplicate
        would not corrupt fusion -- but it would corrupt recall@k, which counts
        positions. A duplicate here means the SQL returned one row twice."""
        with pytest.raises(ValueError, match="twice"):
            retrieval.ranked_ids([("c1", 1.0), ("c1", 0.5)])


# --------------------------------------------------------------------------
# Live tests. Skip cleanly without DATABASE_URL; no OpenAI call is made here --
# the dense probes reuse vectors already in the table, so this file costs
# nothing to run.
# --------------------------------------------------------------------------

live = pytest.mark.live


def _vector(literal: str) -> list[float]:
    """pgvector's text output back to a list of floats.

    Local to the tests on purpose: nothing in the retrieval path reads a stored
    vector back into Python -- the arms send a vector and receive chunk ids --
    so a parser in `services/retrieval.py` would be code that exists only for
    its tests.
    """
    return [float(part) for part in literal.strip("[]").split(",")]


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
class TestTheSparseArmAgainstPostgres:

    def test_the_lexemes_are_or_ed_not_and_ed(self, cursor):
        query = "What was the goodwill impairment charge?"
        built = retrieval.or_tsquery(cursor, query)
        assert " | " in built
        assert " & " not in built

    def test_or_finds_what_and_cannot(self, cursor):
        """The known-positive control for the largest single choice on the
        pre-registration page. A conceptual query carries words the passage
        does not, and under AND one such word returns nothing at all."""
        query = "goodwill impairment zzqqxxjjvv"
        cursor.execute("select count(*) from chunks "
                       "where tsv @@ plainto_tsquery('english', %s)", (query,))
        assert cursor.fetchone()[0] == 0, "AND was expected to match nothing"

        results = retrieval.sparse_search(cursor,
                                          retrieval.or_tsquery(cursor, query))
        assert len(results) == retrieval.DEPTH

    def test_a_lexeme_containing_an_ampersand_survives(self, cursor):
        """Why the lexemes are extracted rather than the operator replaced.
        The parser emits url and file tokens verbatim, and `x.com/a?b=1&c=2` is
        one lexeme containing an `&` -- which a text substitution on `&` would
        split into two nonsense terms, with no error anywhere."""
        built = retrieval.or_tsquery(
            cursor, "see http://x.com/a?b=1&c=2 for the impairment")
        assert "'x.com/a?b=1&c=2'" in built

    def test_an_all_stopword_query_yields_no_tsquery_and_no_rows(self, cursor):
        assert retrieval.or_tsquery(cursor, "the and of") is None
        assert retrieval.sparse_search(cursor, None) == []

    def test_the_planner_uses_the_gin_index(self, cursor):
        """A sequential scan would return the same rows, slowly, and nothing in
        any output would say the index was not used."""
        cursor.execute("explain (costs off) " + retrieval.SPARSE_SQL,
                       {"tsquery": "'goodwil' | 'impair'", "depth": 50})
        plan = " ".join(row[0] for row in cursor.fetchall())
        assert "chunks_tsv_idx" in plan, f"sequential scan instead: {plan}"

    def test_results_come_back_ranked_and_tie_broken(self, cursor):
        query = "goodwill impairment charge"
        results = retrieval.sparse_search(cursor,
                                          retrieval.or_tsquery(cursor, query))
        assert len(results) == retrieval.DEPTH
        pairs = [(-score, chunk_id) for chunk_id, score in results]
        assert pairs == sorted(pairs), "not ordered by score desc, id asc"

    def test_ties_are_real_here_so_the_tie_break_is_load_bearing(self, cursor):
        """The control for the test above. Asserting a tie-break against data
        holding no ties asserts nothing."""
        results = retrieval.sparse_search(
            cursor, retrieval.or_tsquery(cursor, "goodwill impairment charge"))
        scores = [score for _, score in results]
        assert len(set(scores)) < len(scores), "no ties in the top 50"

    def test_the_normalization_flag_is_load_bearing(self, cursor):
        """The control for `RANK_NORMALIZATION == 0`. Flag 1 divides by
        document length; if the third argument were being ignored, or sat in
        the wrong position, both rankings would be identical."""
        tsquery = retrieval.or_tsquery(cursor, "goodwill impairment charge")
        ours = retrieval.ranked_ids(retrieval.sparse_search(cursor, tsquery))
        cursor.execute(
            retrieval.SPARSE_SQL.replace(
                f"::tsquery, {retrieval.RANK_NORMALIZATION})",
                "::tsquery, 1)"),
            {"tsquery": tsquery, "depth": retrieval.DEPTH})
        length_normalized = [row[0] for row in cursor.fetchall()]
        assert ours != length_normalized

    def test_the_depth_is_what_bounds_the_result(self, cursor):
        deeper = retrieval.sparse_search(
            cursor, retrieval.or_tsquery(cursor, "goodwill impairment charge"),
            depth=51)
        assert len(deeper) == 51


@pytest.fixture(scope="module")
def probe(cursor):
    """A vector already in the table, used as a query. No OpenAI call, and it
    is a point the index was actually built over."""
    cursor.execute("select chunk_id, embedding::text from chunks "
                   "order by chunk_id offset 5000 limit 1")
    chunk_id, literal = cursor.fetchone()
    return chunk_id, _vector(literal)


@live
class TestTheDenseArmAgainstPostgres:

    def test_ef_search_is_applied_and_reads_back(self, cursor):
        assert retrieval.set_ef_search(cursor) == retrieval.EF_SEARCH

    def test_ef_search_below_the_depth_silently_truncates(self, cursor, probe):
        """The known-positive control for `ef_search` being in force at all.

        The pre-registration's stated reason for 100 is that a value below the
        requested depth 'degrades results quietly'. Here it is, quiet: a
        depth-50 request served ten rows, no error, no warning. Restores the
        pre-registered value afterwards -- this fixture's connection is shared
        with every test below.
        """
        _, vector = probe
        try:
            retrieval.set_ef_search(cursor, 10)
            starved = retrieval.dense_search(cursor, vector, depth=50,
                                             ef_search=10)
            assert len(starved) == 10
        finally:
            retrieval.set_ef_search(cursor)
        assert len(retrieval.dense_search(cursor, vector, depth=50)) == 50

    def test_a_chunk_retrieves_itself_at_distance_zero(self, cursor, probe):
        chunk_id, vector = probe
        results = retrieval.dense_search(cursor, vector)
        tied = [cid for cid, distance in results if distance < 1e-6]
        assert chunk_id in tied

    def test_a_different_vector_returns_a_different_first_chunk(self, cursor,
                                                               probe):
        """The perturbation for the test above: 'a point is nearest itself'
        also holds for an index that ignores the query vector."""
        chunk_id, vector = probe
        cursor.execute(
            "select embedding::text from chunks "
            "where embedding <=> %s::vector > 0.5 "
            "order by chunk_id limit 1", (retrieval.to_pgvector(vector),))
        row = cursor.fetchone()
        if row is None:
            pytest.skip("no sufficiently distant chunk to probe with")
        assert retrieval.dense_search(cursor, _vector(row[0]))[0][0] != chunk_id

    def test_the_planner_uses_the_hnsw_index(self, cursor, probe):
        """An HNSW index built on one opclass is simply not used by a query
        written with another operator, and the failure is a correct answer
        arrived at slowly."""
        _, vector = probe
        cursor.execute("explain (costs off) " + retrieval.DENSE_SQL,
                       {"vector": retrieval.to_pgvector(vector), "depth": 50})
        plan = " ".join(row[0] for row in cursor.fetchall())
        assert "chunks_embedding_idx" in plan, f"not using HNSW: {plan}"

    def test_distances_come_back_ascending_and_tie_broken(self, cursor, probe):
        _, vector = probe
        results = retrieval.dense_search(cursor, vector)
        assert len(results) == retrieval.DEPTH
        assert results == sorted(results, key=lambda r: (r[1], r[0]))


@live
class TestTheThreeArmsTogether:

    def test_all_three_produce_a_ranking_of_distinct_chunks(self, cursor):
        cursor.execute("select embedding::text from chunks "
                       "order by chunk_id offset 5000 limit 1")
        vector = _vector(cursor.fetchone()[0])
        sparse = retrieval.ranked_ids(
            retrieval.sparse_search(
                cursor, retrieval.or_tsquery(cursor, "goodwill impairment")))
        dense = retrieval.ranked_ids(retrieval.dense_search(cursor, vector))
        hybrid = retrieval.ranked_ids(retrieval.hybrid(sparse, dense))

        for name, ranking in (("sparse", sparse), ("dense", dense),
                              ("hybrid", hybrid)):
            assert len(set(ranking)) == len(ranking), f"{name} repeats a chunk"
        assert len(hybrid) <= len(sparse) + len(dense)
        assert set(hybrid) == set(sparse) | set(dense)
