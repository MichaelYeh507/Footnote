"""Run the three arms over the frozen query set and record what they returned.

    python scripts/run_retrieval.py                 # all 65, both indexes
    python scripts/run_retrieval.py --dry-run       # refusals only, no arm runs
    python scripts/run_retrieval.py --limit 3       # plumbing check, partial

Needs `RAG_FILINGS_DIR` (the query set and the chunk store live beside the
filings), `DATABASE_URL`, and `OPENAI_API_KEY` in `backend/.env`.

**This script does not score anything.** It writes ranked lists; a separate
script turns them into numbers. The split is deliberate and it is the same
argument that put the query-set validator before the first query: scoring code
written while the rankings are on screen gets shaped by them one judgement call
at a time, and no reader could ever see it happen.

**All 65 queries run, including the 15 unanswerable.** Recall is undefined for
those -- they carry no gold and enter no recall denominator -- but Phases 4 and
5 measure abstention against what the retriever actually put in front of the
QA layer, and that is a ranked list. Not running them now would mean running
them later, after the recall numbers exist.

**Five refusals, every one before a single query is embedded or retrieved:**

  frozen      `query_freeze.refuse_unless_frozen` -- the live set must be
              exactly the one frozen at
              `a35b2634f47608...`, or every number computed downstream would
              not be a number over the reviewed set. Checked first, before the
              database is even opened.
  store       the chunk ids in Postgres must be exactly the chunk ids in the
              materialised store. Gold is derived from the store at scoring
              time while the rankings come from the database, so a divergence
              scores as a retrieval miss and nothing anywhere says why.
  embedded    no NULL embeddings. An HNSW index over a partly-NULL column does
              not contain those rows, so they can never be retrieved and the
              dense arm loses recall for a reason unrelated to embeddings.
  ef_search   `hnsw.ef_search` set and read back. Postgres accepts a SET on an
              unrecognised prefixed name as a placeholder that never takes
              effect.
  output      the output directory must be outside the repo.

**What is written**, to `corpus_paths.retrieval_dir()`, never the repo:

  `rankings-<stamp>.jsonl`     one record per query: the tsquery the sparse arm
                               built, and for each arm the (chunk_id, score)
                               pairs in rank order
  `provenance-<stamp>.json`    every parameter that produced them, the frozen
                               set digest, a digest over the query embeddings,
                               the store counts, and a sha256 of the rankings
                               file itself

The embeddings digest exists because the query vectors are the one input to
this run that is neither committed nor reproducible: the API is not guaranteed
to return identical floats twice. Recording a digest does not make the run
repeatable -- it makes a *second* run's difference from this one visible
instead of invisible.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import review_queries as review  # noqa: E402

import corpus_paths  # noqa: E402
import database  # noqa: E402
import services.chunk_store as chunk_store  # noqa: E402
from evaluation import query_freeze  # noqa: E402
from services import fusion, retrieval  # noqa: E402

ARMS = ("sparse", "dense", "hybrid")

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


def stamp(now: datetime.datetime | None = None) -> str:
    now = datetime.datetime.now() if now is None else now
    return now.strftime("%Y%m%d-%H%M%S")


def embeddings_digest(vectors: dict) -> str:
    """One sha256 over `query_id + "  " + <pgvector literal>` lines, id-sorted.

    Same shape as the query-set digest, and sorted for the same reason: the
    order the queries were embedded in is not a property of the embeddings.
    The literal is rendered exactly as `to_pgvector` renders it for the
    database, so the digest is over the bytes that were actually searched with,
    not over a second formatting of the same floats.
    """
    lines = "".join(
        f"{query_id}  {retrieval.to_pgvector(vectors[query_id])}\n"
        for query_id in sorted(vectors))
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def check_store_matches_database(cursor, records: list[dict]) -> list[str]:
    """Refusals if the loaded database is not the materialised store.

    The two halves of scoring read different sources -- gold is derived from
    the store's text, rankings come from the database -- so an id present in
    one and not the other produces a miss that looks exactly like a retrieval
    failure. Compares the whole id set rather than a count: two stores of the
    same size with different ids would pass a count check.
    """
    cursor.execute("select chunk_id from chunks")
    in_database = {row[0] for row in cursor.fetchall()}
    in_store = {record["chunk_id"] for record in records}
    problems = []
    missing = in_store - in_database
    extra = in_database - in_store
    if missing:
        problems.append(
            f"{len(missing)} chunks are in the store but not in the database "
            f"(e.g. {sorted(missing)[:3]}). Gold is derived from the store, so "
            f"those spans could never be retrieved and would score as misses.")
    if extra:
        problems.append(
            f"{len(extra)} chunks are in the database but not in the store "
            f"(e.g. {sorted(extra)[:3]}). An arm could return one, and scoring "
            f"would find no gold text for it.")
    cursor.execute("select count(*) from chunks where embedding is null")
    unembedded = cursor.fetchone()[0]
    if unembedded:
        problems.append(
            f"{unembedded} chunks have no embedding. An HNSW index over a "
            f"partly-NULL column does not contain those rows, so the dense arm "
            f"cannot reach them.")
    return problems


def run_query(cursor, client, query: dict) -> dict:
    """One query through all three arms. Returns the record written to disk."""
    text = query["query"]
    tsquery = retrieval.or_tsquery(cursor, text)
    sparse = retrieval.sparse_search(cursor, tsquery)
    vector = retrieval.embed_query(client, text)
    dense = retrieval.dense_search(cursor, vector)
    fused = retrieval.hybrid(retrieval.ranked_ids(sparse),
                             retrieval.ranked_ids(dense))
    return {
        "record": {
            "query_id": query["query_id"],
            "stratum": query.get("stratum"),
            "tsquery": tsquery,
            "arms": {
                "sparse": [[cid, float(score)] for cid, score in sparse],
                "dense": [[cid, float(score)] for cid, score in dense],
                "hybrid": [[cid, float(score)] for cid, score in fused],
            },
        },
        "vector": vector,
    }


def build_provenance(*, run_stamp, started, finished, freeze, queries,
                     ef_applied, iterative_scan, vectors, store_counts,
                     database_url, rankings_path, complete) -> dict:
    """Every parameter that produced the rankings, in one record.

    Written from values observed during the run rather than from the constants
    a reader could look up: `ef_search` is what the server reported, the store
    counts come from the database, and the rankings sha256 is taken over the
    file as written. A provenance record that restates its own source code
    documents intent; this one documents what happened.
    """
    return {
        "run": run_stamp,
        "started_at": started,
        "finished_at": finished,
        "complete": complete,
        "queries_run": len(queries),
        "query_set": {
            "path": str(review.queries_path()),
            "set_sha256": freeze.get("set_sha256"),
            "frozen_at": freeze.get("frozen_at"),
            "queries": freeze.get("composition", {}).get("queries"),
            "answerable": freeze.get("composition", {}).get("answerable"),
            "duplicate_span_advisories":
                freeze.get("composition", {}).get("duplicate_span_advisories"),
        },
        "sparse": {
            "configuration": retrieval.TS_CONFIG,
            "lexeme_combination": "OR",
            "lexeme_source": "plainto_tsquery",
            "rank_function": "ts_rank_cd",
            "rank_normalization": retrieval.RANK_NORMALIZATION,
            "depth": retrieval.DEPTH,
            "tie_break": "chunk_id ascending",
        },
        "dense": {
            "model": retrieval.EMBED_MODEL,
            "dimensions": retrieval.EMBED_DIMENSIONS,
            "request_dimensions": None,
            "query_prefix": None,
            "distance": "cosine",
            "operator": "<=>",
            "ef_search_requested": retrieval.EF_SEARCH,
            "ef_search_applied": ef_applied,
            "hnsw_iterative_scan": iterative_scan,
            "depth": retrieval.DEPTH,
            "tie_break": "chunk_id ascending",
            "embeddings_sha256": embeddings_digest(vectors),
        },
        "hybrid": {
            "method": "reciprocal rank fusion",
            "k": fusion.RRF_K,
            "depth": fusion.FUSION_DEPTH,
            "arms": ["sparse", "dense"],
            "tie_break": "chunk_id ascending",
        },
        "store": store_counts,
        "database": database.redacted(database_url),
        "code": {
            "services/retrieval.py": _file_sha256(
                BACKEND / "services" / "retrieval.py"),
            "services/fusion.py": _file_sha256(
                BACKEND / "services" / "fusion.py"),
        },
        "rankings": {
            "path": rankings_path.name,
            "sha256": _file_sha256(rankings_path),
        },
    }


def _file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refuse_repo_output(directory: pathlib.Path) -> None:
    """Refuse an output directory inside the repo.

    `.gitignore` covers the repo-relative default, but an explicit `--out`
    would walk straight past it, and the standing rule is that no evaluation
    data enters the public repo.
    """
    resolved = directory.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise RuntimeError(
            f"refusing to write ranked lists inside the repo ({resolved}). "
            f"They are output over corpus text and the input to every "
            f"retrieval number; they belong beside the filings.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="output directory (default: the data root's "
                             "retrieval/)")
    parser.add_argument("--limit", type=int, default=None,
                        help="run only the first N queries. Writes a record "
                             "marked incomplete, which the scorer refuses.")
    parser.add_argument("--dry-run", action="store_true",
                        help="run every refusal and stop; embeds nothing and "
                             "retrieves nothing")
    args = parser.parse_args(argv)

    # First, and before the database is opened: an arm run against a set that
    # no longer matches the freeze produces numbers over some other set.
    queries = review.read_queries()
    try:
        freeze = query_freeze.refuse_unless_frozen(queries)
    except (RuntimeError, FileNotFoundError) as exc:
        print("REFUSING to run any arm:")
        print(f"  {exc}")
        return 2
    print(f"query set  : {len(queries)} queries, frozen "
          f"{freeze.get('frozen_at')}")
    print(f"set sha256 : {freeze.get('set_sha256')}")

    out = corpus_paths.retrieval_dir() if args.out is None else args.out
    try:
        _refuse_repo_output(out)
    except RuntimeError as exc:
        print(f"REFUSING: {exc}")
        return 2

    records = chunk_store.read()
    url = database.url()
    print(f"database   : {database.redacted(url)}")

    import psycopg
    with psycopg.connect(url, connect_timeout=30, autocommit=True) as conn:
        with conn.cursor() as cursor:
            problems = check_store_matches_database(cursor, records)
            if problems:
                print("REFUSING to run any arm:")
                for problem in problems:
                    print(f"  {problem}")
                return 2
            print(f"store      : {len(records)} chunks, "
                  f"{len({r['accession'] for r in records})} accessions, "
                  f"matched against the database")

            ef_applied = retrieval.set_ef_search(cursor)
            cursor.execute(
                "select current_setting('hnsw.iterative_scan', true)")
            iterative_scan = cursor.fetchone()[0]
            print(f"ef_search  : {ef_applied} (iterative_scan="
                  f"{iterative_scan})")

            if args.dry_run:
                print("\ndry run: every refusal passed, no arm was run")
                return 0

            selected = queries if args.limit is None else queries[:args.limit]
            client = retrieval.embedding_client()
            started = datetime.datetime.now().isoformat(timespec="seconds")
            run_stamp = stamp()

            written, vectors = [], {}
            for number, query in enumerate(selected, start=1):
                result = run_query(cursor, client, query)
                written.append(result["record"])
                vectors[query["query_id"]] = result["vector"]
                counts = {arm: len(result["record"]["arms"][arm])
                          for arm in ARMS}
                print(f"  {number:>3}/{len(selected)} {query['query_id']} "
                      f"sparse {counts['sparse']:>2} dense {counts['dense']:>2} "
                      f"hybrid {counts['hybrid']:>3}")

            cursor.execute("select count(*), count(distinct accession), "
                           "sum(tokens) from chunks")
            chunks, accessions, tokens = cursor.fetchone()

    finished = datetime.datetime.now().isoformat(timespec="seconds")
    out.mkdir(parents=True, exist_ok=True)
    rankings_path = out / f"rankings-{run_stamp}.jsonl"
    rankings_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
                for record in written),
        encoding="utf-8")

    provenance = build_provenance(
        run_stamp=run_stamp, started=started, finished=finished,
        freeze=freeze, queries=selected, ef_applied=ef_applied,
        iterative_scan=iterative_scan, vectors=vectors,
        store_counts={"chunks": chunks, "accessions": accessions,
                      "tokens": tokens},
        database_url=url, rankings_path=rankings_path,
        complete=args.limit is None)
    provenance_path = out / f"provenance-{run_stamp}.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    print(f"\nrankings   : {rankings_path}")
    print(f"provenance : {provenance_path}")
    print(f"embeddings : {provenance['dense']['embeddings_sha256']}")
    if not provenance["complete"]:
        print("INCOMPLETE: --limit was given. The scorer refuses this file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
