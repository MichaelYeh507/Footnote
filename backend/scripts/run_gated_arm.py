"""Build Phase 3b's `gated` arm from a completed run. No database, no API.

    python scripts/run_gated_arm.py                 # newest run + newest tau
    python scripts/run_gated_arm.py --dry-run       # refusals only
    python scripts/run_gated_arm.py --rankings <p> --threshold <p>

Needs `RAG_FILINGS_DIR` and nothing else.

PRE-REGISTERED 2026-08-20 in `EVALUATION-SPEC.md`, appendix *PHASE 3b, a fourth
arm*, published before this file was written -- and **POST-HOC**. The first
three arms were pre-registered blind, when no retrieval number existed. This
one answers a failure visible in their published results and is evaluated on
the same 65 queries, so what it produces is a hypothesis consistent with the
data that suggested it, never an independent confirmation.

**Why this script touches neither Postgres nor OpenAI, and why that matters.**
Everything the fourth arm needs is already on disk: the sparse arm's ranked
list with its `ts_rank_cd` scores, the dense arm's ranked list, and `tau(L)`
from `measure_gate_threshold.py`. So `gated` is a re-fusion of a recorded run
rather than a new retrieval. The three published arms are **read and never
re-run**, which is the strongest available guarantee that adding a fourth row
cannot move them: their numbers come from a file this script only opens for
reading, and whose sha256 it verifies first.

**The rule, from the appendix.** `s1` is the sparse arm's own top `ts_rank_cd`
score. If `s1 > tau(L)` the arms fuse under the published RRF, bit-identical to
`hybrid`. If `s1 <= tau(L)` the sparse arm contributes no votes and the ranking
is `dense`'s, unchanged. `k = 60`, depth 50 and the `chunk_id` tie-break are
the pre-registered values in both branches; 3b changes the arm, not them.

**All 65 queries are processed**, including the 15 unanswerable. Recall is
undefined for those and they enter no denominator, but Phases 4 and 5 measure
abstention against what a retriever actually put in front of the QA layer, and
the fourth arm needs the same treatment as the first three.

**The gate count per stratum is printed and recorded** whatever it is. The
appendix requires it published: it is the single most diagnostic number about
whether the rule did what it was designed to do, and the threshold is **not**
moved afterwards. If the gate fires on none of the 50 answerable queries the
arm is identical to `hybrid`, and that is the result.

**Five refusals, before a single ranking is fused:**

  frozen      the live query set must still match `query-set-freeze.json`
  untouched   the rankings file must hash to what its provenance recorded
  same run    the threshold file must name that same rankings sha256, or tau
              was measured against a different run's lexeme counts
  same set    both files must name the frozen set digest
  output      the output directory must be outside the repo
"""

import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
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
            f"refusing to write ranked lists inside the repo ({resolved}). "
            f"They are output over corpus text and the input to a retrieval "
            f"number; they belong beside the filings.")


def _newest(directory: pathlib.Path, pattern: str, what: str) -> pathlib.Path:
    found = sorted(directory.glob(pattern))
    if not found:
        raise FileNotFoundError(f"no {pattern} in {directory}. {what}")
    return found[-1]


def read_rankings(path: pathlib.Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def taus_from(threshold: dict) -> dict:
    """`L` -> `tau(L)`, with integer keys. JSON has string keys only."""
    return {int(size): row["tau"] for size, row in threshold["null"].items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=pathlib.Path)
    parser.add_argument("--threshold", type=pathlib.Path)
    parser.add_argument("--out", type=pathlib.Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    queries = review.read_queries()
    try:
        freeze = query_freeze.refuse_unless_frozen(queries)
    except (RuntimeError, FileNotFoundError) as exc:
        print("REFUSING to build the fourth arm:")
        print(f"  {exc}")
        return 2
    set_digest = freeze.get("set_sha256")
    print(f"query set  : {len(queries)} queries, frozen "
          f"{freeze.get('frozen_at')}")
    print(f"set sha256 : {set_digest}")

    directory = corpus_paths.retrieval_dir()
    rankings_path = args.rankings or _newest(
        directory, "rankings-*.jsonl", "Run scripts/run_retrieval.py first.")
    threshold_path = args.threshold or _newest(
        directory, "gate-threshold-*.json",
        "Run scripts/measure_gate_threshold.py first -- tau is published "
        "before the arm runs.")

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
    if not provenance.get("complete", False):
        print("REFUSING: that run is marked incomplete. A partial denominator "
              "with the same hits is a higher recall.")
        return 2
    print(f"rankings   : {rankings_path.name} (sha256 verified)")

    threshold = json.loads(threshold_path.read_text(encoding="utf-8"))
    if threshold["rankings"]["sha256"] != actual:
        print("REFUSING: the threshold was measured against a different run.")
        print(f"  threshold names {threshold['rankings']['sha256']}")
        print(f"  rankings are    {actual}")
        print("  tau is drawn per lexeme count, so a threshold measured "
              "against another run's\n  queries is a threshold for other "
              "queries.")
        return 2
    for name, digest in (("rankings", provenance["query_set"]["set_sha256"]),
                         ("threshold", threshold["query_set"]["set_sha256"])):
        if digest != set_digest:
            print(f"REFUSING: the {name} file names set digest {digest}, but "
                  f"the live set is {set_digest}.")
            return 2
    print(f"threshold  : {threshold_path.name} "
          f"(percentile {threshold['method']['percentile']}, "
          f"seed {threshold['method']['seed']})")

    out_dir = args.out or directory
    try:
        _refuse_repo_output(out_dir)
    except RuntimeError as exc:
        print(f"REFUSING: {exc}")
        return 2

    taus = taus_from(threshold)
    print(f"tau(L)     : "
          + ", ".join(f"L={size}:{taus[size]:.4f}" for size in sorted(taus)))

    if args.dry_run:
        print("\n--dry-run: every refusal passed, nothing fused.")
        return 0

    rankings = read_rankings(rankings_path)
    decisions = [gate.gate_decision(record, taus) for record in rankings]

    answerable = [d for d in decisions if d["stratum"] != "unanswerable"]
    fired = collections.Counter(
        d["stratum"] for d in answerable if d["gated"])
    totals = collections.Counter(d["stratum"] for d in answerable)

    run = stamp()
    out_path = out_dir / f"gated-rankings-{run}.jsonl"
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        for decision in decisions:
            handle.write(json.dumps({
                "query_id": decision["query_id"],
                "stratum": decision["stratum"],
                "lexemes": decision["lexemes"],
                "s1": decision["s1"],
                "tau": decision["tau"],
                "gated": decision["gated"],
                "ranking": [[cid, score]
                            for cid, score in decision["ranking"]],
            }, ensure_ascii=False) + "\n")

    record = {
        "run": run,
        "built_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "arm": "gated",
        "pre_registered": "EVALUATION-SPEC.md, appendix PHASE 3b, 2026-08-20",
        "post_hoc": (
            "Designed after the three arms' results were published, on the "
            "same 65 queries. A hypothesis consistent with the data that "
            "suggested it, never an independent confirmation. The threshold "
            "is uncontaminated -- measured from the store, gold never "
            "entering it -- but the shape of the rule is not."),
        "rule": "s1 <= tau(L) removes the sparse arm from fusion; otherwise "
                "the published RRF over both arms, bit-identical to hybrid",
        "fusion": {"k": 60, "depth": 50, "tie_break": "chunk_id ascending"},
        "threshold": {
            "path": threshold_path.name,
            "sha256": _file_sha256(threshold_path),
            "percentile": threshold["method"]["percentile"],
            "seed": threshold["method"]["seed"],
            "bags_per_size": threshold["method"]["bags_per_size"],
            "tau": {str(size): taus[size] for size in sorted(taus)},
        },
        "source_rankings": {"path": rankings_path.name, "sha256": actual},
        "query_set": {"set_sha256": set_digest,
                      "frozen_at": freeze.get("frozen_at")},
        "queries_processed": len(decisions),
        "gate_fired": {
            "answerable_total": len(answerable),
            "answerable_gated": sum(1 for d in answerable if d["gated"]),
            "by_stratum": {s: fired.get(s, 0) for s in sorted(totals)},
            "stratum_totals": dict(sorted(totals.items())),
            "unanswerable_gated": sum(
                1 for d in decisions
                if d["stratum"] == "unanswerable" and d["gated"]),
        },
        "rankings": {"path": out_path.name,
                     "sha256": _file_sha256(out_path)},
    }
    provenance_out = out_dir / f"gated-provenance-{run}.json"
    provenance_out.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    print()
    print("PHASE 3b -- THE GATED ARM. POST-HOC; SEE THE DISCLOSURE ABOVE.")
    print("=" * 72)
    print(f"  queries processed   {len(decisions)} "
          f"(15 unanswerable carry no gold and enter no denominator)")
    print(f"  gate fired          "
          f"{sum(1 for d in answerable if d['gated'])}/{len(answerable)} "
          f"answerable")
    for stratum in sorted(totals):
        print(f"    {stratum:<16}  {fired.get(stratum, 0)}/{totals[stratum]}")
    print()
    print(f"  written {out_path.name}")
    print(f"          {provenance_out.name}")
    print()
    print("  Score it with:  python scripts/score_retrieval.py --gated "
          f"{out_path.name}")
    print("  tau is not moved now that it has been applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
