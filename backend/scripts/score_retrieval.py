"""Score the three arms' ranked lists in the pre-registered format.

    python scripts/score_retrieval.py                    # newest run
    python scripts/score_retrieval.py --rankings <path>  # a named run
    python scripts/score_retrieval.py --json <path>      # also write the record

Needs `RAG_FILINGS_DIR`: the query set, the chunk store and the ranked lists all
live beside the filings. Touches no database and no API -- everything it needs
was written by `scripts/run_retrieval.py`.

**Written and tested before any arm ran.** Scoring code written afterwards is
shaped by the rankings one judgement call at a time, and no reader could see it
happen. Same argument as the query-set validator, which was built before the
first query.

**Six refusals, each before any number is computed:**

  frozen       the live query set must still match `query-set-freeze.json`,
               or these numbers are over some other set
  same set     the run's provenance must name the same set digest -- a run
               against an older set, scored against today's, would silently
               mix the two
  complete     a run made with `--limit` is refused: a partial denominator
               with the same hits is a higher recall
  untouched    the rankings file must hash to what its provenance recorded,
               so an edited ranked list is a loud failure rather than a
               quiet one
  covered      exactly the 50 answerable queries, no more and none missing
  gold         every gold span still resolves in the store, to at most five
               chunks (AMENDMENT 4)

**The 15 unanswerable queries are excluded explicitly** and the count is
printed. They carry no gold, recall is undefined for them, and `hit_at_k`
raises rather than scoring an empty gold set a miss.

**AMENDMENT 5's DUPLICATE-SPAN count is reported alongside the results**, as
that amendment requires. It is recounted from the store here and cross-checked
against the number recorded in the freeze, so a store that moved between the
freeze and the scoring is visible rather than absorbed.
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
import services.chunk_store as chunk_store  # noqa: E402
from evaluation import query_freeze  # noqa: E402
from evaluation import retrieval_scoring as scoring  # noqa: E402


def newest_run(directory: pathlib.Path):
    """The most recent rankings file and its provenance, or a refusal.

    Pairs them by the stamp in the filename rather than by "the newest of
    each": two runs minutes apart would otherwise let one run's numbers be
    reported under the other's parameters.
    """
    if not directory.exists():
        raise FileNotFoundError(
            f"no retrieval output at {directory}. Run "
            f"`python scripts/run_retrieval.py` first.")
    rankings = sorted(directory.glob("rankings-*.jsonl"))
    if not rankings:
        raise FileNotFoundError(f"no rankings-*.jsonl in {directory}")
    return rankings[-1], provenance_for(rankings[-1])


def provenance_for(rankings: pathlib.Path) -> pathlib.Path:
    stamp = rankings.name[len("rankings-"):-len(".jsonl")]
    path = rankings.parent / f"provenance-{stamp}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{rankings.name} has no provenance at {path.name}. The numbers "
            f"are meaningless without the parameters that produced them.")
    return path


def load_rankings(path: pathlib.Path) -> dict:
    records = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(),
                                  start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        qid = record["query_id"]
        if qid in records:
            raise ValueError(
                f"{path.name} line {number}: {qid} appears twice. One query "
                f"cannot have two rankings; the file is not a single run.")
        records[qid] = record
    return records


def check_run(provenance: dict, rankings_path: pathlib.Path,
              freeze: dict) -> list[str]:
    """Refusals about the run itself, before anything is scored."""
    problems = []
    if not provenance.get("complete"):
        problems.append(
            "the run is marked incomplete (--limit was used). A partial "
            "denominator with the same hits is a higher recall.")

    run_digest = (provenance.get("query_set") or {}).get("set_sha256")
    frozen_digest = freeze.get("set_sha256")
    if run_digest != frozen_digest:
        problems.append(
            f"the run was made against set {str(run_digest)[:12]}..., the "
            f"freeze now records {str(frozen_digest)[:12]}.... Scoring one "
            f"against the other would mix two query sets.")

    recorded = (provenance.get("rankings") or {}).get("sha256")
    actual = hashlib.sha256(rankings_path.read_bytes()).hexdigest()
    if recorded != actual:
        problems.append(
            f"{rankings_path.name} does not match the sha256 its provenance "
            f"recorded ({str(recorded)[:12]}... vs {actual[:12]}...). The "
            f"ranked lists have been edited since the run.")
    return problems


def actual_sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_gated(path: pathlib.Path, source_sha: str):
    """(provenance, {query_id: ranked list}) for Phase 3b's fourth arm.

    Refuses a gated file built from different rankings than the ones being
    scored. `tau` is drawn per lexeme count against one run's tsqueries, so a
    gated file from another run is a fourth arm for other queries -- and the
    mismatch would show up as nothing worse than a slightly different number.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"no gated rankings at {path}. Run scripts/run_gated_arm.py, "
            f"which needs the threshold measured and published first.")
    provenance_path = (path.parent
                       / path.name.replace("gated-rankings-",
                                           "gated-provenance-")
                       .replace(".jsonl", ".json"))
    if not provenance_path.exists():
        raise FileNotFoundError(
            f"no provenance beside {path.name}. The fourth arm's threshold, "
            f"seed and gate counts are what make its number readable.")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))

    recorded = (provenance.get("rankings") or {}).get("sha256")
    if recorded != actual_sha(path):
        raise ValueError(
            f"{path.name} does not match the sha256 its provenance recorded. "
            f"The fourth arm's ranked lists have been edited since it ran.")
    built_from = (provenance.get("source_rankings") or {}).get("sha256")
    if built_from != source_sha:
        raise ValueError(
            f"{path.name} was built from rankings {str(built_from)[:12]}... "
            f"but the run being scored is {source_sha[:12]}.... The gate "
            f"threshold is drawn per lexeme count against one run's queries.")

    lists = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entry = json.loads(line)
                lists[entry["query_id"]] = entry["ranking"]
    return provenance, lists


def _rate(row) -> str:
    low, high = row["interval"]
    return (f"{row['hits']:>3}/{row['n']:<3} = {row['rate']:.3f} "
            f"[{low:.3f}, {high:.3f}]")


def _reported_arms(summary) -> tuple:
    """The arms this summary holds, in reporting order.

    The three pre-registered arms first and always in their published order, so
    a Phase 3 row keeps its place when Phase 3b's row is appended below it.
    """
    order = scoring.ARMS + (scoring.GATED_ARM,)
    return tuple(arm for arm in order if arm in summary["arms"])


def _table(summary, k) -> list[str]:
    columns = [name for name in ("exact_entity", "conceptual", "pooled")
               if name in summary["arms"]["sparse"][k]]
    lines = [f"RECALL@{k}"]
    for arm in _reported_arms(summary):
        marker = "   <- PHASE 3b, POST-HOC" \
            if arm == scoring.GATED_ARM else ""
        lines.append(f"  {arm:<8}{marker}")
        for column in columns:
            row = summary["arms"][arm][k][column]
            flag = "" if row["reportable"] else \
                f"  (n < {scoring.MIN_REPORTABLE_N}, below the reporting floor)"
            lines.append(f"    {column:<14} {_rate(row)}{flag}")
    return lines


def _comparisons(summary, k) -> list[str]:
    lines = [f"PAIRED COMPARISON AT k={k} -- McNemar discordant pairs (b, c), "
             f"Wilson on b/(b+c)"]
    for entry in summary["comparisons"]:
        if entry["k"] != k:
            continue
        label = f"{entry['arm_a']} vs {entry['arm_b']}"
        if entry["rate"] is None:
            lines.append(
                f"    {label:<18} {entry['stratum']:<14} b=0 c=0 -- the two "
                f"arms agree on every query; there is no rate to report")
            continue
        low, high = entry["interval"]
        flag = "" if entry["reportable"] else \
            f"  (b+c = {entry['discordant']}, below n={scoring.MIN_REPORTABLE_N}"\
            f"; the interval is wider than any claim it could support)"
        lines.append(
            f"    {label:<18} {entry['stratum']:<14} "
            f"b={entry['b']:<3} c={entry['c']:<3} "
            f"b/(b+c) = {entry['rate']:.3f} [{low:.3f}, {high:.3f}]{flag}")
    return lines


def report(summary: dict, provenance: dict, frozen_advisories,
           gated_provenance: dict | None = None) -> str:
    counts = summary["queries"]
    dense = provenance.get("dense", {})
    sparse = provenance.get("sparse", {})
    hybrid = provenance.get("hybrid", {})
    has_gated = scoring.GATED_ARM in summary["arms"]
    lines = [
        "RETRIEVAL -- THREE ARMS, PRE-REGISTERED 2026-08-19"
        + (" -- PLUS PHASE 3b" if has_gated else ""),
        "=" * 72,
        f"  run            {provenance.get('run')}",
        f"  query set      {(provenance.get('query_set') or {}).get('set_sha256')}",
        f"  sparse         {sparse.get('configuration')} tsvector, lexemes "
        f"{sparse.get('lexeme_combination')}, {sparse.get('rank_function')} "
        f"normalization {sparse.get('rank_normalization')}, "
        f"depth {sparse.get('depth')}",
        f"  dense          {dense.get('model')} at {dense.get('dimensions')}d, "
        f"cosine, ef_search {dense.get('ef_search_applied')}, "
        f"depth {dense.get('depth')}",
        f"  hybrid         RRF k={hybrid.get('k')}, depth={hybrid.get('depth')}",
        f"  embeddings     {dense.get('embeddings_sha256')}",
        "",
    ]
    if has_gated:
        gp = gated_provenance or {}
        fired = gp.get("gate_fired", {})
        threshold = gp.get("threshold", {})
        lines += [
            "PHASE 3b -- THE GATED ARM. POST-HOC, AND THAT IS NOT A FORMALITY.",
            "-" * 72,
            "  The three arms above were pre-registered BLIND, before either "
            "index existed and",
            "  before any retrieval number was known. The gated arm was "
            "designed AFTER those",
            "  numbers were published, in response to a failure they revealed, "
            "and is measured on",
            "  the SAME 65 queries. Its number is a hypothesis consistent with "
            "the data that",
            "  suggested it -- never an independent confirmation. Only a "
            "held-out query set would",
            "  make it that, and there is not one.",
            "",
            f"  rule           s1 <= tau(L) removes the sparse arm from "
            f"fusion; otherwise identical to hybrid",
            f"  threshold      {threshold.get('percentile')}th percentile "
            f"of a null drawn from the store, "
            f"{threshold.get('bags_per_size')} bags per L, "
            f"seed {threshold.get('seed')}",
            "  gold in tau    NO -- no gold span, gold chunk, hit or recall "
            "figure enters the threshold",
            f"  gate fired     {fired.get('answerable_gated')}/"
            f"{fired.get('answerable_total')} answerable"
            + ("  (" + ", ".join(
                f"{s} {n}/{fired.get('stratum_totals', {}).get(s)}"
                for s, n in sorted(fired.get("by_stratum", {}).items())) + ")"
               if fired.get("by_stratum") else ""),
            "  tau is not moved now that it has been applied.",
            "",
        ]
    lines += [
        "DENOMINATORS",
        f"  {counts['total']} queries frozen; "
        f"{counts['excluded_unanswerable']} unanswerable EXCLUDED -- they carry "
        f"no gold and recall is undefined for them.",
        "  Abstention is a property of the QA layer and is measured against it "
        "in Phases 4 and 5.",
        f"  {counts['answerable']} answerable: " + ", ".join(
            f"{stratum} {n}" for stratum, n in sorted(summary["strata"].items())),
        f"  DUPLICATE-SPAN advisories among the answerable set (AMENDMENT 5): "
        f"{summary['duplicate_span_advisories']}/{counts['answerable']}",
        "  A flagged query's gold span also appears in another filing. Gold is "
        "accession-scoped, so",
        "  retrieving the other filing scores a miss -- and no text-only "
        "retriever can tell them apart.",
    ]
    if frozen_advisories is not None and \
            frozen_advisories != summary["duplicate_span_advisories"]:
        lines.append(
            f"  DISAGREEMENT: the freeze recorded {frozen_advisories}. The "
            f"chunk store has changed since the freeze.")
    lines += [
        "",
        "Stated precisely, because the name is loose: every answerable query "
        "has at least one gold",
        "chunk, so what follows is a HIT RATE AT k -- the share of queries with "
        "at least one gold chunk",
        "in the top k. It is called recall@k because that is the term the "
        "literature uses for it.",
        "",
    ]
    lines += _table(summary, 1) + [""] + _table(summary, 5) + [""]
    lines += _comparisons(summary, 1) + [""] + _comparisons(summary, 5)
    lines += [""]
    if has_gated:
        # The unconditional version of this sentence is FALSE once the fourth
        # arm is in the table: its parameters were registered on 2026-08-20,
        # after both indexes existed and after the first three arms' numbers
        # were known. Printing it anyway would be the exact overstatement this
        # report is written to prevent, and it would be printed by the tool a
        # reader trusts to check the claim.
        lines += [
            "The three arms' parameters were published before either index "
            "existed and before any",
            "query was written. The gated arm's were NOT: they were registered "
            "2026-08-20, after",
            "those numbers were known, and it is measured on the same queries. "
            "Its threshold is",
            "uncontaminated -- no gold, hit or recall enters it -- but that is "
            "a weaker property",
            "than blindness, and a gated figure quoted without this sentence "
            "misrepresents it.",
        ]
    else:
        lines += [
            "Every parameter above was published before either index existed "
            "and before any query was",
            "written.",
        ]
    lines += [
        f"Intervals are Wilson at {summary['confidence']:.0%}. This project "
        "reports retrieval quality at one",
        "configuration -- one embedding model, one tsvector configuration, one "
        "RRF constant --",
        "and cannot say whether another would do better.",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=pathlib.Path, default=None,
                        help="a specific rankings-*.jsonl (default: newest)")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--json", type=pathlib.Path, default=None,
                        help="also write summary + provenance here")
    parser.add_argument("--gated", type=pathlib.Path, default=None,
                        help="a gated-rankings-*.jsonl from run_gated_arm.py; "
                             "adds PHASE 3b as a fourth row. Without it this "
                             "script behaves exactly as it did when the three "
                             "arms were published.")
    args = parser.parse_args(argv)

    queries = review.read_queries()
    try:
        freeze = query_freeze.refuse_unless_frozen(queries)
    except (RuntimeError, FileNotFoundError) as exc:
        print("REFUSING to score:")
        print(f"  {exc}")
        return 2

    try:
        if args.rankings is None:
            rankings_path, provenance_path = newest_run(
                corpus_paths.retrieval_dir())
        else:
            rankings_path = args.rankings
            provenance_path = provenance_for(rankings_path)
    except FileNotFoundError as exc:
        print(f"REFUSING to score: {exc}")
        return 2

    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    problems = check_run(provenance, rankings_path, freeze)
    if problems:
        print("REFUSING to score:")
        for problem in problems:
            print(f"  {problem}")
        return 2

    rankings = load_rankings(rankings_path)
    records = chunk_store.read()

    gated_provenance = None
    if args.gated is not None:
        gated_path = args.gated
        if not gated_path.exists():
            gated_path = corpus_paths.retrieval_dir() / gated_path.name
        try:
            gated_provenance, gated_lists = load_gated(gated_path, actual_sha(
                rankings_path))
            rankings = scoring.merge_gated(rankings, gated_lists)
        except (FileNotFoundError, ValueError) as exc:
            print("REFUSING to score:")
            print(f"  {exc}")
            return 2

    try:
        summary = scoring.summarize(queries, records, rankings,
                                    confidence=args.confidence)
    except ValueError as exc:
        print("REFUSING to score:")
        print(f"  {exc}")
        return 2

    frozen_advisories = (freeze.get("composition") or {}).get(
        "duplicate_span_advisories")
    print(f"rankings   : {rankings_path}")
    print(f"provenance : {provenance_path}")
    print(f"store      : {len(records)} chunks")
    print()
    print(report(summary, provenance, frozen_advisories, gated_provenance))

    if args.json:
        args.json.write_text(json.dumps({
            "scored_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "rankings": rankings_path.name,
            "provenance": provenance,
            "summary": summary,
        }, indent=2, sort_keys=True, default=list) + "\n", encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
