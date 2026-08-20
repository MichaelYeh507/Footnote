"""Freeze the 65-query retrieval set, and refuse it if anything is outstanding.

    python scripts/freeze_queries.py --check      # report, write nothing
    python scripts/freeze_queries.py --verify     # live set vs the freeze
    python scripts/freeze_queries.py --write --attest "..."

Needs RAG_FILINGS_DIR: the query set, the decision log and the chunk store are
all data and live beside the filings.

**What this replaces.** "All 65 are approved" meant collapsing an append-only
log and counting. The log records `query_id` and `verdict` and no query text,
so a verdict silently went stale whenever its query changed -- which happened
twice, to q009 and q030, and was caught by a person remembering rather than by
anything mechanical. After this runs, every query carries a sha256 of its own
bytes, the set carries one digest over those, both are committed, and any later
edit fails `--verify` by name.

**Six refusals, every one before anything is written:**

  schema      `evaluation/query_set.py` on every record and on the set --
              the pre-registered strata and counts, the conceptual rule
  gold        `evaluation/retrieval_gold.py` -- every span resolves in the
              store, and to at most 5 chunks (AMENDMENT 4)
  smoke       no gold in a goodwill-impairment passage in MA, DOW or WYNN,
              the constraint disclosed 2026-08-19
  decided     every query has a decision, and it is `approved`
  binding     no approval whose recorded hash disagrees with the query as it
              now stands
  attested    any approval carrying no hash at all needs the owner's dated
              attestation, passed with --attest and recorded as an
              attestation rather than as a verification

The store checks are the slow ones -- they scan 11,621 chunks per span -- and
`--no-store` skips them for a quick look. `--write` refuses without them: the
freeze is the record of what was frozen, and a record that skipped its own
validation is not one.

Writes `corpus/query-set-freeze.json`, which is **committed**. It holds ids,
strata, accessions, items and hashes; never a query and never a span. Gold is
verbatim filing text, which is why the set itself lives outside the repo.
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
import services.chunk_store as chunk_store  # noqa: E402
from evaluation import query_freeze as freeze  # noqa: E402
from evaluation import query_set  # noqa: E402
from evaluation import retrieval_gold as gold  # noqa: E402

ATTESTATION_KIND = "human attestation, not a mechanical verification"

ATTESTATION_WHY = (
    "The decision log predates hash recording: it stores query_id and verdict "
    "and no query text, so nothing in it can show these verdicts were cast "
    "against this text. Decisions made after 2026-08-20 record the query "
    "hash and are checked mechanically."
)


def store_problems(queries: list[dict], records: list[dict]):
    """(gold refusals, smoke refusals, the AMENDMENT 5 advisory count).

    Kept apart because they are three separate pre-registered rules, and one
    line reading "ok" for all three would hide which of them actually ran.
    """
    gold_found, smoke_found, advisories = [], [], 0
    for query in queries:
        gold_locations = query.get("gold") or []
        if not gold_locations:
            continue
        locations = [(g["accession"], g["span"]) for g in gold_locations]
        for problem in gold.validate_gold(records, locations):
            gold_found.append(f"{query['query_id']}: {problem}")
        for problem in query_set.check_smoke_constraint(gold_locations, records):
            smoke_found.append(f"{query['query_id']}: {problem}")
        if any(note.startswith(gold.DUPLICATE_NOTE)
               for note in gold.advisory_notes(locations, records)):
            advisories += 1
    return gold_found, smoke_found, advisories


def controls(records: list[dict]) -> list[str]:
    """Make each store check fire on a case that MUST fail. Problems returned.

    Both checks above report a zero, and a zero is the one result that looks
    identical whether the check worked or did nothing at all. That is not
    hypothetical here: last session a regex probe reported all six issuers
    clean -- including one already known to be dirty -- because a `$` had
    escaped into a raw string and the pattern matched nothing.

    So each control is a known positive taken from **this** store, not a
    fixture: if the smoke check cannot be made to fire against the real
    corpus, its clean verdict over the 50 gold spans establishes nothing.
    """
    problems = []

    # An empty store passes every check below by having nothing to fail, and
    # would otherwise reach `records[0]` as a traceback rather than a reason.
    if not records:
        return ["the chunk store is empty, so no check that reads it can "
                "fail and none of their verdicts mean anything. Build it "
                "with `python scripts/build_chunks.py`."]

    # Control 1 -- a span that exists nowhere must be refused.
    invented = "this sentence appears in no SEC filing anywhere, by construction"
    if not gold.validate_gold(records, [(records[0]["accession"], invented)]):
        problems.append("the gold check accepted a span that is in no chunk")

    # Control 2 -- a real goodwill-impairment passage in a smoke issuer must be
    # refused. Found in the store rather than quoted here, since quoting it
    # would put filing text in the repo.
    positive = next(
        (r for r in records
         if r.get("ticker") in query_set.SMOKE_TICKERS
         and all(term in r["text"].casefold() for term in query_set.SMOKE_TERMS)),
        None)
    if positive is None:
        problems.append(
            f"no chunk in {query_set.SMOKE_TICKERS} contains all of "
            f"{query_set.SMOKE_TERMS}, so the smoke check cannot be shown to "
            f"fire against this store and its clean verdict means nothing")
    else:
        span = " ".join(positive["text"].split()[:20])
        refused = query_set.check_smoke_constraint(
            [{"accession": positive["accession"], "span": span}], records)
        if not refused:
            problems.append(
                f"the smoke check accepted a goodwill-impairment span from "
                f"{positive['ticker']} {positive['accession']}, which the "
                f"2026-08-19 disclosure puts off limits")
    return problems


def schema_problems(queries: list[dict]) -> list[str]:
    problems = [f"{q['query_id']}: {p}"
                for q in queries for p in query_set.check_record(q)]
    return problems + query_set.check_set(queries)


def report(title: str, problems: list[str]) -> bool:
    """Print one check's outcome. True when it passed.

    Every count carries its denominator: a bare "3 problems" says nothing
    about whether that is most of the set or a rounding error.
    """
    if problems:
        print(f"{title}: FAIL -- {len(problems)} problem(s)")
        for problem in problems:
            print(f"    {problem}")
        return False
    print(f"{title}: ok")
    return True


def describe_composition(counted: dict, records: list[dict] | None = None) -> None:
    """The set's shape, every share against the denominator it is a share of.

    The corpus totals come from the store rather than from constants: writing
    "44/44" against a hardcoded 44 would keep printing 44 after a corpus change
    and would read as coverage when it had become a coincidence.
    """
    total = counted["queries"]
    print(f"  queries              {total}")
    for stratum, n in counted["strata"].items():
        print(f"    {stratum:<16} {n}/{total}")
    locations = counted["gold_locations"]
    print(f"  answerable           {counted['answerable']}/{total}")
    print(f"  gold locations       {locations}")
    accessions = str(counted["distinct_accessions"])
    issuers = str(counted["distinct_tickers"])
    if records:
        accessions += "/" + str(len({r["accession"] for r in records}))
        issuers += "/" + str(len({r["ticker"] for r in records}))
    print(f"  accessions covered   {accessions}")
    print(f"  issuers covered      {issuers}")
    if locations:
        for item, n in sorted(counted["items"].items(),
                              key=lambda kv: (-kv[1], kv[0])):
            share = 100.0 * n / locations
            print(f"    item {item:<12} {n}/{locations} = {share:.1f}%")


def run_checks(queries, decisions, records):
    """(every problem found, the advisory count or None). Reads, never writes."""
    problems = []
    ok = report("schema and strata", schema_problems(queries))
    problems += [] if ok else ["schema"]

    advisories = None
    if records is None:
        for skipped in ("controls", "gold in the store", "smoke constraint"):
            print(f"{skipped}: SKIPPED (--no-store)")
    else:
        # Controls first. A clean run of the two checks below is only evidence
        # once they have been shown to fire on this store.
        if not report("controls", controls(records)):
            problems += ["controls"]
        gold_found, smoke_found, advisories = store_problems(queries, records)
        if not report("gold in the store", gold_found):
            problems += ["gold"]
        if not report("smoke constraint", smoke_found):
            problems += ["smoke"]

    approvals = freeze.check_approvals(queries, decisions)
    # "approved, none stale" rather than "approved and bound": this check
    # passes when every query is approved and no *recorded* hash disagrees, and
    # it passes vacuously for an approval that records no hash at all. Calling
    # it "bound" would read as a verification that had not happened -- the
    # bound/unbound counts print separately, below.
    if not report("approved, none stale", approvals["problems"]):
        problems += ["approvals"]
    return problems, advisories, approvals


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true",
                      help="report and write nothing (the default)")
    mode.add_argument("--write", action="store_true",
                      help="write the freeze, once every check passes")
    mode.add_argument("--verify", action="store_true",
                      help="compare the live set against the committed freeze")
    parser.add_argument("--attest", default="",
                        help="the owner's words covering approvals that carry "
                             "no hash. Recorded verbatim, and labelled an "
                             "attestation rather than a verification.")
    parser.add_argument("--refreeze", action="store_true",
                        help="overwrite an existing freeze. A disclosed event: "
                             "the reviewed set and the measured set differ.")
    parser.add_argument("--no-store", action="store_true",
                        help="skip the store checks. Refused with --write.")
    parser.add_argument("--freeze-path", type=pathlib.Path,
                        default=freeze.FREEZE_PATH)
    args = parser.parse_args(argv)

    queries_file = review.queries_path()
    if not queries_file.exists():
        print(f"no query set at {queries_file}")
        print("If that path is inside the repo, RAG_FILINGS_DIR is not set.")
        return 2
    queries = review.read_queries()
    print(f"query set : {queries_file} ({len(queries)} queries)")

    if args.verify:
        try:
            record = freeze.load_freeze(args.freeze_path)
        except FileNotFoundError as exc:
            print(str(exc))
            return 2
        problems = freeze.verify(queries, record)
        print(f"freeze    : {args.freeze_path} "
              f"(frozen {record.get('frozen_at')}, "
              f"{len(record.get('queries') or [])} queries)")
        if not report("unchanged since the freeze", problems):
            return 1
        print(f"set sha256: {record.get('set_sha256')}")
        return 0

    decisions = review.read_decisions()
    print(f"decisions : {review.decisions_path()} "
          f"({len(decisions)} queries decided)")

    records = None
    if not args.no_store:
        print("reading the store (one pass per span; this takes minutes) ...")
        records = chunk_store.read()
        print(f"store     : {len(records)} chunks")

    problems, advisories, approvals = run_checks(queries, decisions, records)

    unbound = approvals["unbound"]
    bound = approvals["bound"]
    print(f"\napprovals : {len(bound)}/{len(queries)} bound to text by hash, "
          f"{len(unbound)}/{len(queries)} unbound")
    if unbound:
        print("            unbound approvals predate hash recording. Nothing "
              "in the log\n            binds them to the text on disk; only "
              "an attestation can cover them.")
    if advisories is not None:
        answerable = sum(1 for q in queries if q.get("gold"))
        print(f"advisories: {advisories}/{answerable} answerable queries carry "
              f"a {gold.DUPLICATE_NOTE} note (AMENDMENT 5)")

    print("\ncomposition")
    describe_composition(freeze.composition(queries), records)

    if not args.write:
        if problems:
            print(f"\nNOT READY TO FREEZE: {len(problems)} check(s) failed.")
            return 1
        if unbound and not args.attest:
            print(f"\nNOT READY TO FREEZE: {len(unbound)}/{len(queries)} "
                  f"approvals carry no hash and no attestation was given.")
            print("Re-run with --write --attest \"<your words>\" to freeze.")
            return 1
        # Not "every check passed" under --no-store: three of them did not
        # run, and a summary line that counts a skipped check as a passed one
        # is the same overstatement the controls exist to prevent.
        if args.no_store:
            print("\nEvery check that RAN passed, but --no-store skipped the "
                  "controls, the gold check and the smoke check. Re-run "
                  "without it before freezing.")
        else:
            print("\nEvery check passed. Re-run with --write to freeze.")
        return 0

    if problems:
        print(f"\nREFUSING to write: {len(problems)} check(s) failed.")
        return 1
    if args.no_store:
        print("\nREFUSING to write: --no-store skips the gold and smoke "
              "checks, and a freeze that skipped its own validation is not a "
              "record of anything.")
        return 1
    if unbound and not args.attest.strip():
        print(f"\nREFUSING to write: {len(unbound)}/{len(queries)} approvals "
              f"carry no hash.\n{ATTESTATION_WHY}\nPass --attest \"<your "
              f"words>\" to cover them, or re-review them in the app.")
        return 1
    if args.freeze_path.exists() and not args.refreeze:
        print(f"\nREFUSING to overwrite {args.freeze_path}. The set is already "
              f"frozen; re-freezing means the reviewed set and the measured "
              f"set differ, which is a disclosed event. Pass --refreeze.")
        return 1

    today = datetime.date.today().isoformat()
    entries = freeze.manifest_entries(queries)
    attestation = None
    if unbound:
        attestation = {
            "date": today,
            "by": "owner",
            "set_sha256": freeze.set_digest(entries),
            "covers": len(unbound),
            "text": args.attest.strip(),
            "kind": ATTESTATION_KIND,
            "why": ATTESTATION_WHY,
        }
    record = freeze.build_freeze(
        queries,
        frozen_at=today,
        file_sha256=hashlib.sha256(queries_file.read_bytes()).hexdigest(),
        approvals={
            "source": review.decisions_path().name,
            "decided": len(decisions),
            "approved": len(bound) + len(unbound),
            "bound_by_hash": len(bound),
            "unbound_by_hash": unbound,
        },
        attestation=attestation,
        duplicate_span_advisories=advisories,
    )
    args.freeze_path.parent.mkdir(parents=True, exist_ok=True)
    args.freeze_path.write_text(
        json.dumps(record, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")

    print(f"\nFROZEN {today}")
    print(f"  {args.freeze_path}")
    print(f"  set sha256  {record['set_sha256']}")
    print(f"  file sha256 {record['file_sha256']}  (provenance; the set "
          f"digest is what refuses)")
    if attestation:
        print(f"  attestation covers {attestation['covers']}/{len(queries)} "
              f"approvals, dated {today}")
    print("\nCommit it. Any later edit to any query now fails --verify by "
          "name.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
