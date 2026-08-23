"""Phase 4/5's one QA run, and the controls that must precede it.

    python scripts/run_qa.py --dry-run    # refusals only, nothing called
    python scripts/run_qa.py --controls   # the calibration controls, ~12 calls
    python scripts/run_qa.py              # the one eval run, 195 calls

Needs `RAG_FILINGS_DIR`, `RAG_CALIBRATION_DIR` and `OPENAI_API_KEY`. No
database.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*,
published before this file was written and before any QA output existed.

**One call per distinct context, once, ever.** The 260 arm-query pairs
collapse to 195 distinct (query, ordered top-5) contexts, counted from the
pinned artifacts; each is called exactly once and every arm-query row records
which call it reuses. A completed run refuses to run again -- sampling
variation is not a reason to re-call -- and a crashed run resumes by skipping
the calls whose answers are already on disk, which completes the one run
rather than starting a second: no recorded answer is ever regenerated.

**Controls before the run, from calibration material only.** The known
positive plants a fact from `costco.txt` (the Phase 2 dev set -- outside the
corpus, outside the store, touched by no eval query) in the third of five
fixed excerpts and requires the model to find, cite and quote it; the known
negative asks those same excerpts a question they do not answer and requires
`{"answer": null, ...}`. Both run three times, mirroring the Phase 2
stability protocol; whether the raw responses were byte-identical is
recorded and printed, so run-to-run noise is characterized before the run
rather than discovered inside it. A harness that cannot detect a right
answer cannot report a wrong one as a finding.

**This script prints no outcome.** The adjudication that follows the run is
blind, so the runner reports call counts, token totals and nothing about
what the model said.

**Refusals, before a single call:**

  frozen      the live query set must match the freeze, and the freeze must
              carry the pinned set digest
  pinned      all three artifacts must hash to the digests the appendix
              publishes -- the exact bytes, or a different experiment
  split       the conditioned split must re-derive the published recall@5
              numerators (10, 22, 18, 25) exactly
  controls    the eval run refuses without a passed controls file naming the
              same instrument fingerprint
  once        the eval run refuses if a completed run's provenance exists
  output      the output directory must be outside the repo
"""

import argparse
import datetime
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
from evaluation import qa_contexts, qa_outcomes, query_freeze  # noqa: E402
from evaluation import retrieval_scoring as scoring  # noqa: E402
from services import chunk_store  # noqa: E402
from services import qa  # noqa: E402
from scripts import review_queries as review  # noqa: E402
from scripts import score_retrieval  # noqa: E402

REPO = BACKEND.parent

# The pinned artifacts by name as well as digest: discovery-by-newest would
# quietly pick up a later file, and the digest check would then fail with a
# message about bytes when the actual mistake was a path.
RANKINGS_FILE = "rankings-20260820-153615.jsonl"
GATED_FILE = "gated-rankings-20260821-091433.jsonl"
CHUNKS_FILE = "chunks.jsonl"

# ---------------------------------------------------------------------------
# Controls. Calibration material only: costco.txt is the Phase 2 dev set,
# outside the corpus and the store. Five fixed excerpts, cut as 1600-character
# windows from pinned anchor strings; the planted fact sits in the third, so
# the expected citation is not merely "the first".
CONTROL_SOURCE = "costco.txt"
CONTROL_WINDOW_CHARS = 1600
CONTROL_ANCHORS = (
    "Certain statements contained in this document",
    "offering low prices on a limited selection",
    "Costco Wholesale Corporation and its subsidiaries",
    "Our e-commerce operations",
    "Take Care of Our Employees",
)
CONTROL_TICKER = "COST"
CONTROL_PERIOD = "2025-08-31"
CONTROL_ITEMS = ("1", "1", "1", "1", "1")
POSITIVE_QUESTION = ("How many warehouses did Costco operate worldwide at "
                     "August 31, 2025?")
POSITIVE_CITATION = 3
POSITIVE_ANSWER_MUST_CONTAIN = "914"
NEGATIVE_QUESTION = "What is the name of Costco's Chief Executive Officer?"
STABILITY_REPEATS = 3


def stamp(now: datetime.datetime | None = None) -> str:
    now = now or datetime.datetime.now()
    return now.strftime("%Y%m%d-%H%M%S")


def _refuse_repo_output(directory: pathlib.Path) -> None:
    resolved = directory.resolve()
    if resolved == REPO or REPO in resolved.parents:
        raise RuntimeError(
            f"refusing to write QA output inside the repo ({resolved}). "
            f"Answers are model output over corpus text and adjudications "
            f"are labels; both live beside the filings.")


def preflight() -> dict:
    """Everything both the controls and the run agree on, or a refusal.

    Returns the queries, the freeze, the chunk records (by id and as a
    list), the per-call context map and the conditioned split -- all derived
    from artifacts that just hashed to their published digests.
    """
    queries = review.read_queries()
    freeze = query_freeze.refuse_unless_frozen(queries)
    if freeze.get("set_sha256") != qa_contexts.FROZEN_SET_SHA256:
        raise RuntimeError(
            f"the freeze carries set digest {freeze.get('set_sha256')}, not "
            f"the {qa_contexts.FROZEN_SET_SHA256} the appendix pins. This "
            f"phase is registered against that exact set.")

    retrieval = corpus_paths.retrieval_dir()
    rankings_path = retrieval / RANKINGS_FILE
    gated_path = retrieval / GATED_FILE
    chunks_path = corpus_paths.chunks_dir() / CHUNKS_FILE
    digests = {
        "rankings": qa_contexts.verify_pinned(
            rankings_path, qa_contexts.RANKINGS_SHA256, "the Phase 3 rankings"),
        "gated_rankings": qa_contexts.verify_pinned(
            gated_path, qa_contexts.GATED_RANKINGS_SHA256,
            "the Phase 3b gated rankings"),
        "chunks": qa_contexts.verify_pinned(
            chunks_path, qa_contexts.CHUNKS_SHA256, "the chunk file"),
    }

    rankings = score_retrieval.load_rankings(rankings_path)
    gated_lists = {}
    for line in gated_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            gated_lists[record["query_id"]] = record["ranking"]
    merged = scoring.merge_gated(rankings, gated_lists)

    records = chunk_store.read(chunks_path)
    records_by_id = {record["chunk_id"]: record for record in records}

    ctxs = qa_contexts.contexts(merged)
    calls, assignment = qa_contexts.dedupe(ctxs)
    split = qa_contexts.conditioned_split(queries, records, merged)
    qa_contexts.verify_split_control(split)

    out_dir = corpus_paths.qa_dir()
    _refuse_repo_output(out_dir)

    return {
        "queries": {q["query_id"]: q for q in queries},
        "freeze": freeze,
        "digests": digests,
        "paths": {"rankings": rankings_path, "gated": gated_path,
                  "chunks": chunks_path, "out": out_dir},
        "records_by_id": records_by_id,
        "calls": calls,
        "assignment": assignment,
        "split": split,
    }


def control_excerpts() -> list[dict]:
    """The five fixed calibration excerpts, or a refusal naming the anchor.

    A missing anchor means the calibration file changed, which is a broken
    control rather than a failed one -- the distinction matters because a
    failed control is evidence about the instrument and a broken one is not.
    """
    path = corpus_paths.calibration_dir() / CONTROL_SOURCE
    if not path.exists():
        raise FileNotFoundError(
            f"no {CONTROL_SOURCE} at {path}. The controls are built from the "
            f"calibration set; run scripts/fetch_calibration_filings.py.")
    text = path.read_text(encoding="utf-8")
    excerpts = []
    for number, anchor in enumerate(CONTROL_ANCHORS, start=1):
        start = text.find(anchor)
        if start < 0:
            raise RuntimeError(
                f"control anchor {number} not found in {CONTROL_SOURCE}: "
                f"{anchor!r}. The calibration file is not the one the "
                f"controls were pinned against.")
        excerpts.append({
            "chunk_id": f"control-{number}",
            "accession": "calibration",
            "ticker": CONTROL_TICKER,
            "period": CONTROL_PERIOD,
            "item": CONTROL_ITEMS[number - 1],
            "text": text[start:start + CONTROL_WINDOW_CHARS],
        })
    return excerpts


def check_positive(raw: str, excerpts: list[dict]) -> list[str]:
    """Why one known-positive response fails, empty if it passes."""
    problems = []
    parsed = qa_outcomes.parse_response(raw)
    if not parsed["ok"]:
        return [f"malformed: {parsed['reason']}"]
    if parsed["answer"] is None:
        return ["abstained on a question the planted excerpt answers"]
    if parsed["citation"] != POSITIVE_CITATION:
        problems.append(f"cited {parsed['citation']}, "
                        f"expected {POSITIVE_CITATION}")
    result = qa_outcomes.classify(parsed, excerpts, gold_ids=[],
                                  gold_locations=[])
    if result["outcome"] is qa_outcomes.QAOutcome.UNSUPPORTED:
        problems.append("quote does not verify against the cited excerpt")
    if POSITIVE_ANSWER_MUST_CONTAIN not in (parsed["answer"] or ""):
        problems.append(
            f"answer {parsed['answer']!r} does not contain "
            f"{POSITIVE_ANSWER_MUST_CONTAIN!r}")
    return problems


def check_negative(raw: str) -> list[str]:
    """Why one known-negative response fails, empty if it passes."""
    parsed = qa_outcomes.parse_response(raw)
    if not parsed["ok"]:
        return [f"malformed: {parsed['reason']}"]
    if parsed["answer"] is not None:
        return [f"answered {parsed['answer']!r} where the excerpts hold no "
                f"answer -- the instrument cannot express abstention, or the "
                f"model answered from memory"]
    return []


def run_controls(out_dir: pathlib.Path, client=None) -> dict:
    """Both controls, STABILITY_REPEATS times each, recorded whatever happens.

    `passed` requires every repeat of each control to meet its expectation.
    `stable` records whether each control's raw responses were byte-identical
    across repeats -- run-to-run noise is characterized here, not blocking by
    itself, and the record travels into the eval run's provenance.
    """
    excerpts = control_excerpts()
    results = {}
    for name, question, check in (
            ("positive", POSITIVE_QUESTION,
             lambda raw: check_positive(raw, excerpts)),
            ("negative", NEGATIVE_QUESTION, check_negative)):
        repeats = []
        for _ in range(STABILITY_REPEATS):
            response = qa.ask(question, excerpts, client=client)
            repeats.append({
                "raw": response["raw"],
                "attempts": response["attempts"],
                "usage": response["usage"],
                "problems": check(response["raw"]),
            })
        results[name] = {
            "question": question,
            "repeats": repeats,
            "stable": len({r["raw"] for r in repeats}) == 1,
            "passed": all(not r["problems"] for r in repeats),
        }

    record = {
        "ran_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "pre_registered": "EVALUATION-SPEC.md, appendix PHASE 4/5, 2026-08-21",
        "instrument_sha256": qa.INSTRUMENT_SHA256,
        "source": CONTROL_SOURCE,
        "anchors": list(CONTROL_ANCHORS),
        "window_chars": CONTROL_WINDOW_CHARS,
        "stability_repeats": STABILITY_REPEATS,
        "controls": results,
        "passed": all(entry["passed"] for entry in results.values()),
    }
    out_path = out_dir / f"qa-controls-{stamp()}.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    record["path"] = out_path
    return record


def latest_controls(out_dir: pathlib.Path) -> dict | None:
    found = sorted(out_dir.glob("qa-controls-*.json"))
    if not found:
        return None
    record = json.loads(found[-1].read_text(encoding="utf-8"))
    record["path"] = found[-1]
    return record


def completed_provenance(out_dir: pathlib.Path) -> pathlib.Path | None:
    for path in sorted(out_dir.glob("qa-provenance-*.json")):
        if json.loads(path.read_text(encoding="utf-8")).get("complete"):
            return path
    return None


def resumable_answers(out_dir: pathlib.Path) -> pathlib.Path | None:
    """An answers file from a crashed run, if one is waiting to be finished."""
    for path in sorted(out_dir.glob("answers-*.jsonl")):
        run = path.stem.replace("answers-", "")
        provenance = out_dir / f"qa-provenance-{run}.json"
        if not provenance.exists():
            return path
    return None


def answered_call_ids(path: pathlib.Path) -> set:
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            done.add(json.loads(line)["call_id"])
    return done


def run_eval(state: dict, client=None) -> int:
    out_dir = state["paths"]["out"]

    controls = latest_controls(out_dir)
    if controls is None:
        print("REFUSING the eval run: no controls have been recorded.")
        print("  Run  python scripts/run_qa.py --controls  first -- a "
              "negative result needs a known positive.")
        return 2
    if not controls.get("passed"):
        print(f"REFUSING the eval run: the controls at "
              f"{controls['path'].name} did not pass.")
        print("  A failed control is amended into the appendix, dated, "
              "before any eval-set call -- never worked around.")
        return 2
    if controls.get("instrument_sha256") != qa.INSTRUMENT_SHA256:
        print("REFUSING the eval run: the controls were run against a "
              "different instrument.")
        print(f"  controls  {controls.get('instrument_sha256')}")
        print(f"  current   {qa.INSTRUMENT_SHA256}")
        return 2

    already = completed_provenance(out_dir)
    if already is not None:
        print(f"REFUSING: a completed run exists ({already.name}).")
        print("  One run, by pre-registration. Sampling variation is not a "
              "reason to re-call, and a second run would need its own dated "
              "registration.")
        return 2

    partial = resumable_answers(out_dir)
    if partial is not None:
        run = partial.stem.replace("answers-", "")
        done = answered_call_ids(partial)
        answers_path = partial
        print(f"resuming   : {partial.name} ({len(done)} calls already "
              f"recorded; none will be re-asked)")
    else:
        run = stamp()
        done = set()
        answers_path = out_dir / f"answers-{run}.jsonl"
        out_dir.mkdir(parents=True, exist_ok=True)

    calls = state["calls"]
    queries = state["queries"]
    records_by_id = state["records_by_id"]
    todo = [cid for cid in sorted(calls) if cid not in done]
    print(f"contexts   : {len(calls)} distinct "
          f"({len(state['assignment'])} arm-query rows), "
          f"{len(todo)} to ask")

    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    with open(answers_path, "a", encoding="utf-8", newline="\n") as handle:
        for number, cid in enumerate(todo, start=1):
            call = calls[cid]
            query = queries[call["query_id"]]
            excerpts = qa_contexts.excerpts_for(call["excerpt_ids"],
                                                records_by_id)
            response = qa.ask(query["query"], excerpts, client=client)
            handle.write(json.dumps({
                "call_id": cid,
                "query_id": call["query_id"],
                "query_sha256": query_freeze.query_sha256(query),
                "excerpt_ids": list(call["excerpt_ids"]),
                "arms": call["arms"],
                "raw": response["raw"],
                "attempts": response["attempts"],
                "usage": response["usage"],
                "asked_at": datetime.datetime.now().isoformat(
                    timespec="seconds"),
            }, ensure_ascii=False) + "\n")
            handle.flush()
            for key in usage:
                if response["usage"].get(key):
                    usage[key] += response["usage"][key]
            if number % 20 == 0 or number == len(todo):
                print(f"  {number}/{len(todo)} calls made")

    provenance = {
        "run": run,
        "completed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "pre_registered": "EVALUATION-SPEC.md, appendix PHASE 4/5, 2026-08-21",
        "instrument_sha256": qa.INSTRUMENT_SHA256,
        "model": qa.MODEL,
        "temperature": qa.TEMPERATURE,
        "context_k": qa_contexts.CONTEXT_K,
        "arms": list(qa_contexts.QA_ARMS),
        "artifacts": {
            "rankings": {"file": RANKINGS_FILE,
                         "sha256": state["digests"]["rankings"]},
            "gated_rankings": {"file": GATED_FILE,
                               "sha256": state["digests"]["gated_rankings"]},
            "chunks": {"file": CHUNKS_FILE,
                       "sha256": state["digests"]["chunks"]},
        },
        "query_set": {"set_sha256": state["freeze"]["set_sha256"],
                      "frozen_at": state["freeze"].get("frozen_at")},
        "controls": {"file": controls["path"].name,
                     "passed": controls["passed"],
                     "stable": {name: entry["stable"]
                                for name, entry in
                                controls["controls"].items()}},
        "calls": len(calls),
        "arm_query_rows": len(state["assignment"]),
        "assignment": {f"{qid}|{arm}": cid
                       for (qid, arm), cid in
                       sorted(state["assignment"].items())},
        "conditioned_split": {
            arm: {"gold_in_context":
                  sum(1 for hit in state["split"][arm].values() if hit),
                  "answerable": len(state["split"][arm])}
            for arm in qa_contexts.QA_ARMS},
        "usage_this_invocation": usage,
        "answers": {"file": answers_path.name,
                    "sha256": qa_contexts.file_sha256(answers_path)},
        "complete": True,
    }
    provenance_path = out_dir / f"qa-provenance-{run}.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False),
        encoding="utf-8")

    print()
    print("THE RUN IS COMPLETE. No outcome is printed here: the adjudication "
          "that follows is blind.")
    print(f"  answers    {answers_path.name}")
    print(f"  provenance {provenance_path.name}")
    print(f"  tokens     {usage['prompt_tokens']} prompt, "
          f"{usage['completion_tokens']} completion (this invocation)")
    print()
    print("  Next:  python scripts/adjudicate_qa.py")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="refusals only; nothing is called")
    parser.add_argument("--controls", action="store_true",
                        help="run the calibration controls and record them")
    args = parser.parse_args(argv)

    try:
        state = preflight()
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print("REFUSING before any call:")
        print(f"  {exc}")
        return 2

    split_counts = {arm: sum(1 for hit in state["split"][arm].values()
                             if hit)
                    for arm in qa_contexts.QA_ARMS}
    print(f"query set  : {len(state['queries'])} queries, frozen, digest "
          f"pinned")
    print(f"artifacts  : 3 pinned digests verified")
    print(f"split      : {split_counts} == published numerators")
    print(f"contexts   : {len(state['calls'])} distinct calls for "
          f"{len(state['assignment'])} arm-query rows")
    print(f"instrument : {qa.MODEL} @ temperature {qa.TEMPERATURE}, "
          f"sha256 {qa.INSTRUMENT_SHA256[:16]}...")

    if args.dry_run:
        print("\n--dry-run: every refusal passed, nothing called.")
        return 0

    if args.controls:
        record = run_controls(state["paths"]["out"])
        print()
        for name, entry in record["controls"].items():
            state_word = "PASSED" if entry["passed"] else "FAILED"
            stability = "byte-identical" if entry["stable"] else \
                "NOT byte-identical (noise recorded)"
            print(f"  {name:<9} {state_word}, {STABILITY_REPEATS} repeats "
                  f"{stability}")
            for repeat in entry["repeats"]:
                for problem in repeat["problems"]:
                    print(f"            - {problem}")
        print(f"\n  recorded {record['path'].name}")
        if not record["passed"]:
            print("  A failed control is evidence about the instrument. The "
                  "fix is a dated amendment\n  to the appendix before any "
                  "eval-set call -- never a workaround.")
            return 1
        print("  Controls green. The eval run is now permitted:  "
              "python scripts/run_qa.py")
        return 0

    return run_eval(state)


if __name__ == "__main__":
    raise SystemExit(main())
