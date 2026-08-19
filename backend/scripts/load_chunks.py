"""Load the materialised chunk store into Postgres, once, with the guards on.

    python scripts/load_chunks.py

Reads `<data>/chunks/chunks.jsonl` and COPYs every record into `chunks`. Both
indexes are built from these rows, and the rows come from the store rather than
from a fresh parse of the filings, so the sparse and dense arms see
byte-identical passages.

**The whole load is one transaction.** Every check below runs inside it, before
the commit, and any failure rolls back. That is deliberate: a partial load is
the failure that hides. Both indexes build happily over whatever rows exist,
queries against the missing text simply never hit, and the arm looks worse for
a reason unrelated to retrieval. It is the same defect the chunker shipped once
already -- HON reached the first store at 0.7% of its text with every test
green, because the tests counted what was present instead of comparing against
an independent number.

Five refusals:

  occupied   the table already holds rows and --force was not given. The
             embeddings describe the text that is there; replacing it quietly
             would leave them describing text that no longer exists.
  rows       the row count must equal the store's record count.
  filings    the distinct accession count must equal the store's. Rows can be
             present while a whole filing is not.
  tokens     the summed token count must equal the store's -- an independent
             measurement, so rows and filings can both be right while the text
             was truncated in transit.
  tsv        no row may have a NULL tsvector. A NULL means that chunk is absent
             from the sparse index while present in the table, which no row
             count reveals and which reads as a retrieval miss.

The embedding column is deliberately left NULL here. The load is two-phase --
rows first, vectors second -- and `scripts/embed_chunks.py` is what fills them
and refuses to finish while any row is still NULL.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import database  # noqa: E402
from services import chunk_store  # noqa: E402

TABLE = "chunks"

# The columns the loader writes, in COPY order. `tsv` is generated always and
# `embedding` is filled in phase two; sending either here would be an error.
#
# The store's `index` becomes `chunk_index`: bare `index` reads as the SQL
# object rather than as a position. It is the only rename in the whole path,
# which is why it has a test of its own.
COLUMNS = (
    "chunk_id", "accession", "ticker", "period", "item", "title",
    "chunk_index", "first_page", "last_page", "tokens", "text",
)

_STORE_FIELD = {"chunk_index": "index"}


def row_from_record(record: dict) -> tuple:
    """One store record as a COPY row, in COLUMNS order."""
    return tuple(record[_STORE_FIELD.get(column, column)] for column in COLUMNS)


def expectations(records: list[dict]) -> dict:
    """What the database must contain afterwards, computed from the store.

    Computed *before* the load and compared *after*, so the two are independent
    measurements rather than the same number read twice.
    """
    return {
        "rows": len(records),
        "filings": len({r["accession"] for r in records}),
        "tokens": sum(r["tokens"] for r in records),
    }


def verify(expected: dict, actual: dict, nulls: int) -> list[str]:
    """Every mismatch, not just the first -- reporting one at a time would turn
    a single rollback into three."""
    problems = []
    if actual["rows"] != expected["rows"]:
        problems.append(
            f"rows: database has {actual['rows']}, store has {expected['rows']}")
    if actual["filings"] != expected["filings"]:
        problems.append(
            f"filings: database has {actual['filings']} distinct accessions, "
            f"store has {expected['filings']}")
    if actual["tokens"] != expected["tokens"]:
        problems.append(
            f"tokens: database sums to {actual['tokens']}, "
            f"store sums to {expected['tokens']}")
    if nulls:
        problems.append(
            f"tsv: {nulls} rows have a NULL tsvector, so they are in the table "
            f"but not in the sparse index")
    return problems


def copy_records(cursor, records: list[dict], table: str = TABLE) -> None:
    """COPY every record in one stream.

    COPY rather than executemany: 11,621 rows holding 22.9 MB of text is where
    the round-trip per row starts to matter, and COPY is one round trip.
    """
    columns = ", ".join(COLUMNS)
    with cursor.copy(f"copy {table} ({columns}) from stdin") as copy:
        for record in records:
            copy.write_row(row_from_record(record))


def measure(cursor, table: str = TABLE) -> tuple[dict, int]:
    cursor.execute(
        f"select count(*), count(distinct accession), coalesce(sum(tokens), 0) "
        f"from {table}")
    rows, filings, tokens = cursor.fetchone()
    cursor.execute(f"select count(*) from {table} where tsv is null")
    return {"rows": rows, "filings": filings, "tokens": int(tokens)}, \
        cursor.fetchone()[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=pathlib.Path, default=None)
    parser.add_argument("--force", action="store_true",
                        help="replace rows that are already loaded")
    args = parser.parse_args(argv)

    records = chunk_store.read(args.store)
    expected = expectations(records)
    print(f"store: {expected['rows']} chunks, {expected['filings']} filings, "
          f"{expected['tokens']} tokens")

    with database.connect() as connection:      # autocommit=False
        print(f"database: {database.redacted(database.url())}")
        with connection.cursor() as cursor:
            cursor.execute(f"select count(*) from {TABLE}")
            existing = cursor.fetchone()[0]
            if existing and not args.force:
                print(f"REFUSING to load: {TABLE} already holds {existing} "
                      f"rows. Both indexes were built from them, so replacing "
                      f"them quietly would leave the embeddings describing "
                      f"text that no longer exists. Pass --force to replace.")
                return 2
            if existing:
                print(f"replacing {existing} existing rows")
                cursor.execute(f"delete from {TABLE}")

            print("copying ...")
            copy_records(cursor, records)

            actual, nulls = measure(cursor)
            problems = verify(expected, actual, nulls)
            if problems:
                connection.rollback()
                print("REFUSING to commit — rolled back:")
                for problem in problems:
                    print(f"  {problem}")
                return 2

        connection.commit()

    print(f"loaded {actual['rows']} chunks over {actual['filings']} filings, "
          f"{actual['tokens']} tokens, 0 null tsvectors")
    print("embeddings are NULL; run scripts/embed_chunks.py next")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
