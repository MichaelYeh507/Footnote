"""Loading the store into Postgres, and refusing a load that is not whole.

The failure this guards is the one the chunker already taught: a store that is
*mostly* there. HON reached the first chunk store at 0.7% of its text and every
test passed, because every test counted things that were present rather than
comparing against an independent number. So the checks here are all
comparisons against the store, run inside the transaction, with a rollback
rather than a warning.

A partial load is invisible downstream. Both indexes build happily over
whatever rows exist; queries against the missing text simply never hit, and the
arm looks worse for a reason unrelated to retrieval.

Written before scripts/load_chunks.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import load_chunks as loader  # noqa: E402


def _record(chunk_id="c1", accession="acc-1", tokens=10, **overrides):
    record = {
        "chunk_id": chunk_id, "accession": accession, "ticker": "AAA",
        "period": "2025-12-31", "item": "1", "title": "Business",
        "index": 0, "first_page": 1, "last_page": 2, "tokens": tokens,
        "text": "a passage of filing text",
    }
    record.update(overrides)
    return record


class TestTheRowMapping:

    def test_maps_every_store_field_to_a_column(self):
        row = loader.row_from_record(_record())
        assert len(row) == len(loader.COLUMNS)

    def test_index_becomes_chunk_index(self):
        """The store calls it `index`; the column is `chunk_index`, because
        bare `index` reads as the SQL object rather than a position. This is
        the one rename in the whole path, so it is the one place a silent
        mismatch could hide."""
        assert loader.COLUMNS[loader.COLUMNS.index("chunk_index")] == "chunk_index"
        row = dict(zip(loader.COLUMNS, loader.row_from_record(_record(index=7))))
        assert row["chunk_index"] == 7

    def test_does_not_send_a_value_for_the_generated_column(self):
        """`tsv` is generated always; sending a value for it is an error, and
        maintaining it by hand would let it drift from `text`."""
        assert "tsv" not in loader.COLUMNS

    def test_does_not_send_a_value_for_the_embedding(self):
        """The load is two-phase: rows first, vectors second. A NULL embedding
        is the expected intermediate state, and the loader refuses to call
        itself finished while any row is still NULL."""
        assert "embedding" not in loader.COLUMNS

    def test_preserves_an_empty_item(self):
        """1,393 of 11,621 chunks carry an empty item deliberately."""
        row = dict(zip(loader.COLUMNS, loader.row_from_record(
            _record(item="", title=""))))
        assert row["item"] == ""

    def test_preserves_non_ascii_text(self):
        row = dict(zip(loader.COLUMNS, loader.row_from_record(
            _record(text="Grainger’s risk — reward"))))
        assert row["text"] == "Grainger’s risk — reward"


class TestTheExpectations:
    """What the loader compares the database against, computed from the store
    before the load so the two are independent measurements."""

    RECORDS = [_record("c1", "acc-1", tokens=10),
               _record("c2", "acc-1", tokens=20),
               _record("c3", "acc-2", tokens=30)]

    def test_counts_rows(self):
        assert loader.expectations(self.RECORDS)["rows"] == 3

    def test_counts_distinct_filings(self):
        assert loader.expectations(self.RECORDS)["filings"] == 2

    def test_sums_tokens(self):
        assert loader.expectations(self.RECORDS)["tokens"] == 60


class TestTheVerification:
    """Each mismatch must be reported, not merely detected."""

    EXPECTED = {"rows": 3, "filings": 2, "tokens": 60}

    def test_a_matching_load_reports_no_problems(self):
        assert loader.verify(self.EXPECTED, dict(self.EXPECTED), nulls=0) == []

    def test_a_short_row_count_is_caught(self):
        actual = dict(self.EXPECTED, rows=2)
        problems = loader.verify(self.EXPECTED, actual, nulls=0)
        assert len(problems) == 1 and "rows" in problems[0]

    def test_a_missing_filing_is_caught(self):
        """The HON failure mode, restated at the database boundary: rows can
        be present while a whole filing is not."""
        actual = dict(self.EXPECTED, filings=1)
        problems = loader.verify(self.EXPECTED, actual, nulls=0)
        assert len(problems) == 1 and "filing" in problems[0].lower()

    def test_a_token_mismatch_is_caught(self):
        """An independent measurement: rows and filings can both be right
        while the text itself was truncated in transit."""
        actual = dict(self.EXPECTED, tokens=59)
        problems = loader.verify(self.EXPECTED, actual, nulls=0)
        assert len(problems) == 1 and "token" in problems[0].lower()

    def test_a_null_tsvector_is_caught(self):
        """The generated column must populate for every row. A NULL tsv means
        that chunk is absent from the sparse index while present in the table
        -- invisible to a row count, and it would read as a retrieval miss."""
        problems = loader.verify(self.EXPECTED, dict(self.EXPECTED), nulls=4)
        assert len(problems) == 1 and "tsv" in problems[0].lower()

    def test_several_problems_are_all_reported(self):
        """Reporting only the first would turn one rollback into three."""
        actual = {"rows": 2, "filings": 1, "tokens": 59}
        assert len(loader.verify(self.EXPECTED, actual, nulls=1)) == 4


@pytest.mark.live
class TestAgainstTheRealDatabase:
    """Round-trips a handful of rows through a real table, then removes them.

    Uses a temporary table with the same DDL as `chunks` rather than the real
    one, so a test run can never disturb a loaded store. Skips cleanly without
    DATABASE_URL.
    """

    @pytest.fixture
    def cursor(self):
        database = pytest.importorskip("database")
        try:
            url = database.url()
        except RuntimeError as exc:
            pytest.skip(str(exc).splitlines()[0])
        import psycopg
        try:
            connection = psycopg.connect(url, connect_timeout=10)
        except Exception as exc:
            pytest.skip(f"database unreachable: {type(exc).__name__}")
        with connection:
            with connection.cursor() as cur:
                cur.execute("""
                    create temporary table chunks_roundtrip (
                        chunk_id text primary key,
                        accession text not null, ticker text not null,
                        period text not null, item text not null default '',
                        title text not null default '',
                        chunk_index integer not null, first_page integer not null,
                        last_page integer not null, tokens integer not null,
                        text text not null,
                        embedding extensions.vector(1536),
                        tsv tsvector generated always as
                            (to_tsvector('english', "text")) stored
                    ) on commit drop
                """)
                yield cur
            connection.rollback()

    def test_copy_round_trips_every_field(self, cursor):
        records = [_record("r1", "acc-1", index=0),
                   _record("r2", "acc-1", index=1, item="", title=""),
                   _record("r3", "acc-2", index=0, text="Grainger’s — dash")]
        loader.copy_records(cursor, records, table="chunks_roundtrip")

        cursor.execute("select count(*), count(distinct accession), sum(tokens) "
                       "from chunks_roundtrip")
        rows, filings, tokens = cursor.fetchone()
        assert (rows, filings, tokens) == (3, 2, 30)

        cursor.execute("select text from chunks_roundtrip where chunk_id='r3'")
        assert cursor.fetchone()[0] == "Grainger’s — dash"

        cursor.execute("select item, title from chunks_roundtrip "
                       "where chunk_id='r2'")
        assert cursor.fetchone() == ("", "")

    def test_the_generated_tsvector_populates(self, cursor):
        """The sparse index exists only because of this column."""
        loader.copy_records(cursor, [_record("r1", text="goodwill impairment")],
                            table="chunks_roundtrip")
        cursor.execute("select tsv is not null, tsv::text from chunks_roundtrip")
        populated, rendered = cursor.fetchone()
        assert populated
        # 'english' stems both words; 'simple' would not.
        assert "goodwil" in rendered and "impair" in rendered

    def test_the_embedding_column_accepts_the_pre_registered_width(self, cursor):
        loader.copy_records(cursor, [_record("r1")], table="chunks_roundtrip")
        cursor.execute("update chunks_roundtrip set embedding = %s::vector",
                       ("[" + ",".join(["0.1"] * 1536) + "]",))
        cursor.execute("select vector_dims(embedding) from chunks_roundtrip")
        assert cursor.fetchone()[0] == 1536

    def test_a_wrong_width_embedding_is_rejected(self, cursor):
        """The guard behind the drift test's typmod comparison: a 768-wide
        vector does not silently truncate, it errors."""
        import psycopg
        loader.copy_records(cursor, [_record("r1")], table="chunks_roundtrip")
        with pytest.raises(psycopg.Error):
            cursor.execute("update chunks_roundtrip set embedding = %s::vector",
                           ("[" + ",".join(["0.1"] * 768) + "]",))
