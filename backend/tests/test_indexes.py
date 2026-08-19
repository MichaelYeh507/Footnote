"""Both retrieval indexes, against the live database.

"The index exists" is not the claim worth testing. An HNSW index built on the
wrong opclass is not used by a `<=>` query at all -- the query still returns
correct rows, by sequential scan, so at 11,621 rows the defect shows up as
latency rather than as an error, which is to say it does not show up. The same
goes for the GIN index and `@@`. So these tests check that each index is
*chosen by the planner* and that it returns what it should.

The dense check is a self-retrieval: take a vector already stored, query with
it, and require its own chunk back at rank 1 at distance ~0. It needs no query
set and no new embedding, so it can run before a single eval query exists --
and it is perturbed here by querying with a different vector and requiring the
opposite, because "the nearest neighbour of a point is itself" is exactly the
kind of assertion that passes against a broken index.

All live-marked: skips cleanly without DATABASE_URL.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

pytestmark = pytest.mark.live


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


class TestTheLoadedRows:

    def test_the_store_is_fully_loaded(self, cursor):
        """The numbers the pre-registration quotes, checked against the
        database rather than against the file they came from."""
        cursor.execute("select count(*), count(distinct accession), sum(tokens) "
                       "from chunks")
        assert cursor.fetchone() == (11621, 44, 4890354)

    def test_no_chunk_is_missing_its_tsvector(self, cursor):
        cursor.execute("select count(*) from chunks where tsv is null")
        assert cursor.fetchone()[0] == 0

    def test_the_item_distribution_matches_the_store(self, cursor):
        """An independent confirmation the load was faithful: these counts were
        measured from the JSONL before it was ever loaded."""
        cursor.execute("select item, count(*) from chunks group by 1")
        counts = dict(cursor.fetchall())
        assert counts["8"] == 3668
        assert counts["1A"] == 1847
        assert counts[""] == 1393      # front matter and post-signature tail
        assert counts["7"] == 1293
        assert counts["1"] == 1228


class TestTheSparseIndex:

    def test_the_planner_uses_the_gin_index(self, cursor):
        """Explained against the shape the sparse arm actually issues.

        The query shape matters, and getting it wrong here was instructive: a
        bare `where tsv @@ q limit 10` plans as a sequential scan, because the
        planner can satisfy ten rows by scanning and never needs the index.
        That is a correct decision, and testing it would have asserted
        something the arm never does. The arm always ranks
        (`order by ts_rank_cd desc`), which requires finding every match, and
        that is when the index earns its place.
        """
        cursor.execute(
            "explain (costs off) "
            "select chunk_id, ts_rank_cd(tsv, q, 0) r from chunks, "
            "to_tsquery('english', 'goodwill | impairment') q "
            "where tsv @@ q order by r desc limit 50")
        plan = " ".join(row[0] for row in cursor.fetchall())
        assert "chunks_tsv_idx" in plan, f"sequential scan instead: {plan}"

    def test_stemming_is_active(self, cursor):
        """The pre-registered configuration is `english`, not `simple`. Under
        `simple`, 'impairment' would not reach 'impaired'."""
        cursor.execute("select to_tsquery('english', 'impairment')::text")
        assert "impair" in cursor.fetchone()[0]

    def test_a_known_term_returns_ranked_rows(self, cursor):
        cursor.execute(
            "select chunk_id, ts_rank_cd(tsv, q, 0) r from chunks, "
            "to_tsquery('english', 'goodwill | impairment') q "
            "where tsv @@ q order by r desc limit 5")
        rows = cursor.fetchall()
        assert len(rows) == 5
        assert [r[1] for r in rows] == sorted((r[1] for r in rows), reverse=True)

    def test_a_nonsense_term_returns_nothing(self, cursor):
        """The empty result is an ordinary outcome, and the arm must produce it
        rather than erroring -- RRF depends on being handed an empty list."""
        cursor.execute(
            "select count(*) from chunks "
            "where tsv @@ to_tsquery('english', 'zzqqxxjjvv')")
        assert cursor.fetchone()[0] == 0


class TestTheDenseIndex:

    def test_every_chunk_has_an_embedding(self, cursor):
        """An HNSW index over a partly-NULL column silently does not contain
        those chunks. They would never be retrieved, and the dense arm would
        score worse for a reason unrelated to embeddings."""
        cursor.execute("select count(*) from chunks where embedding is null")
        assert cursor.fetchone()[0] == 0

    def test_every_embedding_is_the_pre_registered_width(self, cursor):
        cursor.execute("select min(vector_dims(embedding)), "
                       "max(vector_dims(embedding)) from chunks")
        assert cursor.fetchone() == (1536, 1536)

    def test_the_planner_uses_the_hnsw_index(self, cursor):
        cursor.execute("select embedding::text from chunks limit 1")
        probe = cursor.fetchone()[0]
        cursor.execute(
            "explain (costs off) select chunk_id from chunks "
            "order by embedding <=> %s::vector limit 5", (probe,))
        plan = " ".join(row[0] for row in cursor.fetchall())
        assert "chunks_embedding_idx" in plan, f"not using HNSW: {plan}"

    def test_a_chunk_is_at_zero_distance_from_itself(self, cursor):
        """Self-retrieval, over several chunks spread through the table.

        Asserted as "in the zero-distance set", not "is rank 1", and the
        distinction is a measurement rather than a hedge. 448 of 11,621 chunks
        (3.9%) are byte-identical to a chunk in another filing -- shared
        boilerplate, in groups of up to 10 -- so identical text gives identical
        embeddings and a genuine tie at distance exactly 0. Requiring rank 1
        would fail on a correct index, which is what it did when first written.
        """
        cursor.execute("select chunk_id, embedding::text from chunks "
                       "order by chunk_id limit 5")
        for chunk_id, vector in cursor.fetchall():
            cursor.execute(
                "select chunk_id, embedding <=> %s::vector d from chunks "
                "order by d limit 12", (vector,))
            rows = cursor.fetchall()
            tied = [cid for cid, distance in rows if distance < 1e-6]
            assert chunk_id in tied, (
                f"{chunk_id} is not among its own nearest neighbours: "
                f"{rows[:3]}")

    def test_an_unrelated_vector_returns_a_different_chunk(self, cursor):
        """The perturbation for the test above.

        "A chunk is at distance zero from itself" would also hold against an
        index that ignored the query vector entirely, so it is only evidence
        once a *different* query vector gives a different answer. Uses a real
        embedding from elsewhere in the table rather than a synthesised one:
        pgvector has no scalar-multiply operator, and a hand-built vector would
        not be a point the index was built over.
        """
        cursor.execute("select chunk_id, embedding::text from chunks "
                       "order by chunk_id limit 1")
        first_id, first_vector = cursor.fetchone()
        cursor.execute(
            "select embedding::text from chunks where embedding <=> %s::vector "
            "> 0.5 order by chunk_id limit 1", (first_vector,))
        far = cursor.fetchone()
        if far is None:
            pytest.skip("no sufficiently distant chunk to probe with")
        cursor.execute(
            "select chunk_id from chunks order by embedding <=> %s::vector "
            "limit 1", (far[0],))
        assert cursor.fetchone()[0] != first_id

    def test_cosine_distance_orders_sensibly(self, cursor):
        """A chunk is nearer to itself than to an arbitrary other chunk."""
        cursor.execute("select chunk_id, embedding::text from chunks "
                       "order by chunk_id limit 1")
        chunk_id, vector = cursor.fetchone()
        cursor.execute(
            "select embedding <=> %s::vector from chunks where chunk_id != %s "
            "order by chunk_id limit 1", (vector, chunk_id))
        other = cursor.fetchone()[0]
        assert other > 1e-6


class TestDuplicateText:
    """Measured 2026-08-19, after both indexes were built.

    Found by a self-retrieval test that failed against a correct index: two
    chunks were tied at distance exactly 0 because their text is byte-identical.
    Filings share a great deal of standard language, so this is a property of
    the corpus rather than a defect in the chunker.

    It is pinned here because one of these numbers is load-bearing. The
    pre-registered gold rule scopes a span to its accession and caps the gold
    set at 5, and that cap is only meaningful if a filing cannot contain the
    same passage twice.
    """

    def test_duplicate_text_exists_and_is_a_small_share(self, cursor):
        cursor.execute("""
            select coalesce(sum(n), 0) from
            (select count(*) n from chunks group by text having count(*) > 1) d
        """)
        duplicated = cursor.fetchone()[0]
        assert duplicated == 448, f"duplicate-text chunks moved to {duplicated}"

    def test_no_filing_contains_the_same_passage_twice(self, cursor):
        """The one that matters for the gold-set cap.

        If a filing could repeat a passage, a span unique-looking to whoever
        wrote the query could still match several chunks of that filing, and
        the cap would be doing more work than its 5 suggests. It cannot.
        """
        cursor.execute("""
            select count(*) from
            (select accession, text from chunks
             group by accession, text having count(*) > 1) d
        """)
        assert cursor.fetchone()[0] == 0

    def test_the_largest_identical_group_spans_ten_filings(self, cursor):
        """Boilerplate shared across issuers. A dense query whose gold chunk
        sits in a group like this sees up to ten indistinguishable candidates
        at distance 0, which is a real hazard for the query set rather than for
        the index -- recorded here so it is measured before queries exist."""
        cursor.execute(
            "select max(n) from (select count(*) n from chunks group by text) d")
        assert cursor.fetchone()[0] == 10


class TestRowLevelSecurity:

    def test_rls_is_enabled_on_the_chunk_table(self, cursor):
        """22.9 MB of filing text, and the anon key must not read it. Enabled
        in 003 against the project-wide default, which stays off elsewhere."""
        cursor.execute("select relrowsecurity from pg_class "
                       "where relname = 'chunks'")
        assert cursor.fetchone()[0] is True

    def test_the_other_tables_keep_the_project_default(self, cursor):
        """The deviation is deliberate and scoped to one table; a later blanket
        change would show up here."""
        cursor.execute("select relname, relrowsecurity from pg_class "
                       "where relname in ('extractions','reports','risks',"
                       "'management')")
        assert all(not enabled for _name, enabled in cursor.fetchall())
