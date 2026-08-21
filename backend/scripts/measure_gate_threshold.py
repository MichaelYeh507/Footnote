"""Measure Phase 3b's gate threshold from the store. Gold never enters it.

    python scripts/measure_gate_threshold.py              # newest run's L values
    python scripts/measure_gate_threshold.py --rankings <path>
    python scripts/measure_gate_threshold.py --dry-run    # refusals only

Needs `RAG_FILINGS_DIR`, `DATABASE_URL` and no API key: the null is drawn from
the store's own vocabulary, so nothing here is embedded.

PRE-REGISTERED 2026-08-20 in `EVALUATION-SPEC.md`, appendix *PHASE 3b, a fourth
arm*, and published before this file was written. **That pre-registration is
post-hoc.** The arm exists because Phase 3's published results showed where
fusion fails, and it is evaluated on the same 65 queries. The threshold this
script measures is the one part of 3b that is genuinely uncontaminated, and
that is worth stating precisely rather than warmly:

  **What goes in:** the `tsvector` vocabulary of the 11,621-chunk store, each
  lexeme's document frequency, and the distinct-lexeme counts of the frozen
  queries. Nothing else.

  **What never goes in:** the gold spans, the gold chunk sets, any hit or miss,
  any recall figure, and every per-query outcome of run 20260820-153615. This
  script could have run on 2026-08-19, before a single query was written, and
  would have produced the same `tau`.

**The method, fixed before this file existed.** For each distinct `L` present
in the query set, draw `NULL_BAGS_PER_SIZE` bags of `L` distinct lexemes,
weighted by document frequency, from one `random.Random` seeded at `NULL_SEED`;
score each bag through the sparse arm's own `sparse_search`; take the
nearest-rank 95th percentile of the resulting top scores. That is `tau(L)`.

**Why the bags are not re-stemmed, restated here because this is the script
that would do it.** `ts_stat` returns lexemes out of the index, already
stemmed. They are quoted, OR-ed and cast `::tsquery` directly. Passing them
through `plainto_tsquery` would re-run the `english` dictionary, re-parse
compound tokens, and additionally AND what should be OR-ed -- a different null,
with nothing in the output to say so.

**The threshold is not moved after it is applied.** If the gate later fires on
none of the 50 answerable queries, the arm is identical to `hybrid` and that is
the result. If it fires on all 50, the arm is identical to `dense` and that is
the result. This script has no flag for the percentile, by design.

**Three refusals, before any bag is drawn:**

  frozen      the live query set must still match `query-set-freeze.json`
  untouched   the rankings file must hash to what its provenance recorded
  output      the output directory must be outside the repo
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import random
import statistics
import sys
import time

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
import database  # noqa: E402
from evaluation import gate, query_freeze  # noqa: E402
from scripts import review_queries as review  # noqa: E402

REPO = BACKEND.parent


def stamp(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return now.strftime("%Y%m%d-%H%M%S")


def _file_sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refuse_repo_output(directory: pathlib.Path) -> None:
    resolved = directory.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise RuntimeError(
            f"refusing to write the threshold inside the repo ({resolved}). "
            f"It is measured over corpus text and belongs beside the filings.")


def newest_rankings(directory: pathlib.Path) -> pathlib.Path:
    found = sorted(directory.glob("rankings-*.jsonl"))
    if not found:
        raise FileNotFoundError(
            f"no rankings-*.jsonl in {directory}. Run "
            f"scripts/run_retrieval.py first -- the L values come from the "
            f"tsqueries the sparse arm actually built.")
    return found[-1]


def read_rankings(path: pathlib.Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def lexeme_sizes(rankings: list[dict]) -> dict[int, list[str]]:
    """Distinct `L` -> the query ids that have it.

    Read from the recorded tsqueries rather than rebuilt, so the null is sized
    against what the arm actually parsed and not against a second parse that
    could disagree with it.
    """
    sizes: dict[int, list[str]] = {}
    for record in rankings:
        size = gate.lexeme_count(record.get("tsquery"))
        sizes.setdefault(size, []).append(record["query_id"])
    return dict(sorted(sizes.items()))


def measure(cursor, sizes: dict[int, list[str]], *,
            bags: int = gate.NULL_BAGS_PER_SIZE,
            seed: int = gate.NULL_SEED,
            progress=None) -> dict:
    """The null distribution and `tau(L)` for every `L`.

    One RNG for the whole measurement, seeded once and consumed in ascending
    `L` order, so the draw is reproducible as a sequence rather than only per
    size.
    """
    vocabulary = gate.store_vocabulary(cursor)
    words = [word for word, _count in vocabulary]
    weights = [count for _word, count in vocabulary]
    rng = random.Random(seed)

    out = {}
    for size in sorted(sizes):
        if size == 0:
            # A query with no lexemes scores 0.0 and is gated by any
            # non-negative threshold; there is no null to draw for it.
            out[size] = {"queries": len(sizes[size]), "bags": 0,
                         "tau": 0.0, "note": "no lexemes; gated by definition"}
            continue
        scores = []
        for index in range(bags):
            bag = gate.weighted_sample_without_replacement(
                rng, words, weights, size)
            scores.append(gate.null_top_score(cursor, bag))
            if progress and (index + 1) % 100 == 0:
                progress(size, index + 1, bags)
        out[size] = {
            "queries": len(sizes[size]),
            "bags": bags,
            "tau": gate.nearest_rank_percentile(scores, gate.NULL_PERCENTILE),
            "min": min(scores),
            "median": statistics.median(scores),
            "mean": statistics.fmean(scores),
            "max": max(scores),
            "zero_scoring_bags": sum(1 for s in scores if s == 0.0),
        }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    queries = review.read_queries()
    try:
        freeze = query_freeze.refuse_unless_frozen(queries)
    except (RuntimeError, FileNotFoundError) as exc:
        print("REFUSING to measure anything:")
        print(f"  {exc}")
        return 2
    print(f"query set  : {len(queries)} queries, frozen "
          f"{freeze.get('frozen_at')}")
    print(f"set sha256 : {freeze.get('set_sha256')}")

    directory = corpus_paths.retrieval_dir()
    rankings_path = args.rankings or newest_rankings(directory)
    provenance_path = (rankings_path.parent
                       / rankings_path.name.replace("rankings-", "provenance-")
                       .replace(".jsonl", ".json"))
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    recorded = provenance["rankings"]["sha256"]
    actual = _file_sha256(rankings_path)
    if recorded != actual:
        print("REFUSING: the rankings file no longer matches its provenance.")
        print(f"  recorded {recorded}")
        print(f"  actual   {actual}")
        return 2
    print(f"rankings   : {rankings_path.name} (sha256 verified)")

    out_dir = args.out or directory
    try:
        _refuse_repo_output(out_dir)
    except RuntimeError as exc:
        print(f"REFUSING: {exc}")
        return 2

    rankings = read_rankings(rankings_path)
    sizes = lexeme_sizes(rankings)
    total = sum(gate.NULL_BAGS_PER_SIZE for size in sizes if size > 0)
    print(f"L values   : {sorted(sizes)}")
    print(f"queries    : "
          + ", ".join(f"L={s}:{len(q)}" for s, q in sizes.items()))
    print(f"null draws : {total} bags "
          f"({gate.NULL_BAGS_PER_SIZE} per L), seed {gate.NULL_SEED}, "
          f"percentile {gate.NULL_PERCENTILE} nearest-rank")

    if args.dry_run:
        print("\n--dry-run: every refusal passed, nothing measured.")
        return 0

    url = database.url()
    print(f"database   : {database.redacted(url)}")
    started = datetime.datetime.now()
    clock = time.time()

    def progress(size, done, of):
        rate = (time.time() - clock)
        print(f"  L={size:<3} {done}/{of}   {rate:6.0f}s elapsed", flush=True)

    import psycopg
    with psycopg.connect(url, connect_timeout=30, autocommit=True) as conn:
        with conn.cursor() as cursor:
            vocabulary_size = len(gate.store_vocabulary(cursor))
            print(f"vocabulary : {vocabulary_size} distinct lexemes\n")
            null = measure(cursor, sizes, progress=progress)

    finished = datetime.datetime.now()
    run = stamp(finished)
    record = {
        "run": run,
        "measured_at": finished.isoformat(timespec="seconds"),
        "started_at": started.isoformat(timespec="seconds"),
        "pre_registered": "EVALUATION-SPEC.md, appendix PHASE 3b, 2026-08-20",
        "post_hoc": (
            "This threshold is uncontaminated -- no gold, no hit, no recall "
            "enters it -- but the ARM it serves was designed after Phase 3's "
            "results were known, on the same 65 queries. Any number it "
            "produces is a hypothesis consistent with the data that suggested "
            "it, never an independent confirmation."),
        "method": {
            "statistic": "top ts_rank_cd score of the sparse arm, "
                         "normalization 0, depth 50",
            "null": "random bags of L distinct lexemes drawn from the store's "
                    "tsvector vocabulary, weighted by document frequency, "
                    "without replacement",
            "not_re_stemmed": "bags are quoted, OR-ed and cast ::tsquery; "
                              "they never pass through plainto_tsquery or "
                              "to_tsquery",
            "percentile": gate.NULL_PERCENTILE,
            "percentile_definition": "nearest rank, so tau is an observed value",
            "bags_per_size": gate.NULL_BAGS_PER_SIZE,
            "seed": gate.NULL_SEED,
            "rng": "random.Random, one instance, consumed in ascending L",
            "gate_rule": "s1 <= tau(L) gates the sparse arm out of fusion",
        },
        "vocabulary_size": vocabulary_size,
        "query_set": {
            "set_sha256": freeze.get("set_sha256"),
            "frozen_at": freeze.get("frozen_at"),
        },
        "rankings": {
            "path": rankings_path.name,
            "sha256": actual,
        },
        "sizes": {str(size): ids for size, ids in sizes.items()},
        "null": {str(size): row for size, row in null.items()},
    }

    out_path = out_dir / f"gate-threshold-{run}.json"
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print()
    print("GATE THRESHOLD -- MEASURED FROM THE STORE, GOLD NEVER ENTERED IT")
    print("=" * 72)
    print(f"  {'L':>3}  {'queries':>7}  {'bags':>5}  {'min':>7}  "
          f"{'median':>7}  {'tau(p95)':>9}  {'max':>7}")
    for size, row in null.items():
        if row["bags"] == 0:
            print(f"  {size:>3}  {row['queries']:>7}  {'-':>5}  "
                  f"{'-':>7}  {'-':>7}  {row['tau']:>9.4f}  {'-':>7}   "
                  f"({row['note']})")
            continue
        print(f"  {size:>3}  {row['queries']:>7}  {row['bags']:>5}  "
              f"{row['min']:>7.4f}  {row['median']:>7.4f}  "
              f"{row['tau']:>9.4f}  {row['max']:>7.4f}")
    print()
    print(f"  elapsed {(finished - started).total_seconds():.0f}s")
    print(f"  written {out_path}")
    print()
    print("  tau is NOT moved after it is applied. If the gate fires on none "
          "of the 50\n  answerable queries the arm is identical to hybrid, and "
          "that is the result.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
