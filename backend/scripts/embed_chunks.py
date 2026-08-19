"""Fill the dense index: one embedding per chunk, under the pre-registered model.

    python scripts/embed_chunks.py

Reads every row of `chunks` whose embedding is still NULL, embeds its `text`
verbatim, and writes the vector back. Run it again after a failure and it picks
up where it stopped -- selection is `where embedding is null` and each batch
commits on its own, so a crash halfway through is a resumption rather than a
restart. This is the one step in Phase 3 that costs money and cannot be
replayed for free.

PRE-REGISTERED 2026-08-19 in EVALUATION-SPEC.md, before either index existed
and before any query was written:

  * `text-embedding-3-small`, at its native 1,536 dimensions -- no Matryoshka
    truncation, so there is no dimension parameter that could later be claimed
    to have been chosen for the result, and 1,536 sits under pgvector's
    2,000-dimension ceiling for an HNSW index on the `vector` type.
  * The chunk's `text` field, verbatim. No title prefix, no ticker or item
    header, no instruction prefix -- the sparse arm indexes the same bytes, and
    a prefix on one arm would make the ablation measure the prefix.
  * Cosine distance, which is why the index is `vector_cosine_ops`.

The refusals exist because the failure here is quiet. A response with fewer
vectors than inputs would misalign every subsequent chunk_id with someone
else's vector; a response of the wrong width would leave rows NULL. An HNSW
index over a partly-NULL column simply does not contain those chunks, so they
would never be retrieved and the dense arm would score worse for a reason that
has nothing to do with embeddings.
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import openai  # noqa: E402
from dotenv import dotenv_values  # noqa: E402

import database  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Pre-registered 2026-08-19. See the module docstring before touching either.
MODEL = "text-embedding-3-small"
DIMENSIONS = 1536

# Deliberately None: the request sends no `dimensions` parameter, so the model
# returns its native width. Setting it would be a truncation, and a truncation
# is a parameter.
REQUEST_DIMENSIONS = None

# The API caps both the number of inputs and the tokens per request. The token
# budget is the binding one here -- the median chunk is 460 tokens, so 200
# items would be roughly 92,000 tokens.
MAX_ITEMS = 200
MAX_TOKENS = 100_000


def batches(records, max_items=MAX_ITEMS, max_tokens=MAX_TOKENS):
    """(chunk_id, text, tokens) records grouped into API requests.

    A record whose own token count exceeds the budget is emitted alone rather
    than skipped. That cannot happen with the current store -- the largest
    chunk is 809 tokens -- but silently dropping a chunk is exactly the class
    of failure this script exists to prevent.
    """
    batch, total = [], 0
    for record in records:
        tokens = record[2]
        if batch and (len(batch) >= max_items or total + tokens > max_tokens):
            yield batch
            batch, total = [], 0
        batch.append(record)
        total += tokens
    if batch:
        yield batch


def to_pgvector(values) -> str:
    """pgvector's text input format: a bracketed, comma-separated list."""
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def check_response(vectors, expected: int) -> None:
    """Refuse a response that does not match what was asked for.

    Checks every vector, not just the first: a truncated or padded response
    from the middle of a batch is the case that would misalign ids and vectors
    without changing the count.
    """
    if len(vectors) != expected:
        raise ValueError(
            f"embedding response holds {len(vectors)} vectors for {expected} "
            f"inputs. Ids and vectors are matched by position, so a short "
            f"response would attach every later vector to the wrong chunk.")
    for index, vector in enumerate(vectors):
        if len(vector) != DIMENSIONS:
            raise ValueError(
                f"vector {index} has {len(vector)} dimensions, expected "
                f"{DIMENSIONS}. The column is declared vector({DIMENSIONS}) "
                f"and the width is pre-registered.")


def _client() -> openai.OpenAI:
    """A client built from backend/.env rather than os.environ.

    conftest seeds a dummy OPENAI_API_KEY, so reading the environment here
    would make a test run silently target a key that cannot work -- the same
    reasoning tests/test_schema_drift.py gives for reading .env directly.
    """
    key = (dotenv_values(BACKEND / ".env").get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("no OPENAI_API_KEY in backend/.env")
    return openai.OpenAI(api_key=key)


def pending(cursor) -> list[tuple]:
    cursor.execute(
        "select chunk_id, text, tokens from chunks where embedding is null "
        "order by chunk_id")
    return cursor.fetchall()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="embed at most this many chunks (for a dry run)")
    args = parser.parse_args(argv)

    client = _client()
    with database.connect() as connection:
        print(f"database: {database.redacted(database.url())}")
        with connection.cursor() as cursor:
            records = pending(cursor)
            cursor.execute("select count(*) from chunks")
            total = cursor.fetchone()[0]
        if args.limit:
            records = records[:args.limit]
        if not records:
            print(f"nothing to do: all {total} chunks already have embeddings")
            return 0

        tokens = sum(r[2] for r in records)
        print(f"embedding {len(records)} of {total} chunks "
              f"({tokens} tokens) with {MODEL} at {DIMENSIONS}d")

        done = 0
        for batch in batches(records):
            response = client.embeddings.create(
                model=MODEL, input=[r[1] for r in batch],
            )
            vectors = [item.embedding for item in response.data]
            check_response(vectors, expected=len(batch))
            with connection.cursor() as cursor:
                cursor.executemany(
                    "update chunks set embedding = %s::vector where chunk_id = %s",
                    [(to_pgvector(v), r[0]) for v, r in zip(vectors, batch)],
                )
            connection.commit()   # per batch, so a crash resumes rather than restarts
            done += len(batch)
            print(f"  {done}/{len(records)}", end="\r", flush=True)

        print()
        with connection.cursor() as cursor:
            cursor.execute("select count(*) from chunks where embedding is null")
            remaining = cursor.fetchone()[0]
            cursor.execute("select min(vector_dims(embedding)), "
                           "max(vector_dims(embedding)) from chunks "
                           "where embedding is not null")
            low, high = cursor.fetchone()

    if remaining and not args.limit:
        print(f"REFUSING to report success: {remaining} chunks still have no "
              f"embedding. An HNSW index over a partly-NULL column does not "
              f"contain those chunks, so they can never be retrieved.")
        return 2
    if low != DIMENSIONS or high != DIMENSIONS:
        print(f"REFUSING to report success: stored widths run {low}..{high}, "
              f"expected exactly {DIMENSIONS}.")
        return 2

    print(f"embedded {done} chunks, all {DIMENSIONS}d, {remaining} still null")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
