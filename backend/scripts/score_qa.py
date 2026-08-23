"""Score the one QA run in the pre-registered format. No database, no API.

    python scripts/score_qa.py            # print the report
    python scripts/score_qa.py --json <p> # also write summary + provenance

Needs `RAG_FILINGS_DIR` (and `RAG_CALIBRATION_DIR` for the shared
preflight). Runs after the blind adjudication is complete and frozen.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*.
Every cell, denominator and comparison printed here was fixed there before
any QA output existed; this script computes them and refuses everything
else.

**Refusals, before a single cell:**

  preflight   the same gates as the run: frozen set, pinned artifact
              digests, split control reproducing 10/22/18/25
  complete    a completed run's provenance must exist, and the answers file
              must still hash to what that provenance recorded
  frozen      the adjudication file must be digest-frozen, its digest must
              still match the bytes, and every answered answerable item
              must carry a verdict (enforced again in assembly)

The gated arm's rows are printed under the PHASE 3b post-hoc disclosure,
every time -- the arm that produced their contexts was designed after
Phase 3's results, and a gated figure without that sentence misrepresents
what was measured.
"""

import argparse
import datetime
import json
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_adjudication, qa_contexts, qa_scoring  # noqa: E402
from evaluation.scoring import MIN_REPORTABLE_N  # noqa: E402
from scripts import adjudicate_qa  # noqa: E402
from scripts import run_qa  # noqa: E402
from services import qa  # noqa: E402


def completed_run(out_dir: pathlib.Path) -> tuple:
    """(provenance, answers_path), digest-verified, or a refusal."""
    path = run_qa.completed_provenance(out_dir)
    if path is None:
        raise FileNotFoundError(
            f"no completed run in {out_dir}. Run scripts/run_qa.py; a "
            f"partial run is finished there, never scored here.")
    provenance = json.loads(path.read_text(encoding="utf-8"))
    answers_path = out_dir / provenance["answers"]["file"]
    recorded = provenance["answers"]["sha256"]
    actual = qa_contexts.file_sha256(answers_path)
    if recorded != actual:
        raise RuntimeError(
            f"{answers_path.name} no longer matches its provenance "
            f"(recorded {recorded[:16]}..., actual {actual[:16]}...). The "
            f"answers are the run's one artifact; a drifted file is not "
            f"the run.")
    return provenance, answers_path


def frozen_verdicts(out_dir: pathlib.Path) -> tuple:
    """(verdicts, freeze), with the digest re-checked against the bytes."""
    verdicts_path = out_dir / adjudicate_qa.VERDICTS_NAME
    freeze_path = out_dir / adjudicate_qa.FREEZE_NAME
    if not freeze_path.exists():
        raise FileNotFoundError(
            f"no {adjudicate_qa.FREEZE_NAME} in {out_dir}. The adjudication "
            f"is digest-frozen before any per-arm table is assembled; run "
            f"scripts/adjudicate_qa.py --freeze when every item is judged.")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    actual = qa_contexts.file_sha256(verdicts_path)
    if freeze["file_sha256"] != actual:
        raise RuntimeError(
            f"the verdict file no longer matches its freeze (frozen "
            f"{freeze['file_sha256'][:16]}..., actual {actual[:16]}...). "
            f"A verdict edited after the freeze is disclosed and re-frozen "
            f"deliberately, never scored through silently.")
    return qa_adjudication.read_verdicts(verdicts_path), freeze


def fmt(cell) -> str:
    if cell is None:
        return "n=0, undefined"
    gate = "" if cell["reportable"] else "  (below n=25 gate)"
    return (f"{cell['hits']}/{cell['n']} = {cell['rate']:.3f} "
            f"[{cell['interval'][0]:.3f}, {cell['interval'][1]:.3f}]{gate}")


def report(summary: dict, provenance: dict, freeze: dict) -> str:
    lines = []
    out = lines.append
    out("PHASE 4/5 -- GROUNDED QA AND ABSTENTION")
    out("=" * 72)
    out(f"rules      : EVALUATION-SPEC.md, appendix PHASE 4/5, "
        f"pre-registered 2026-08-21")
    out(f"instrument : {provenance['model']} @ "
        f"temperature {provenance['temperature']}, "
        f"sha256 {provenance['instrument_sha256'][:16]}...")
    if provenance["instrument_sha256"] != qa.INSTRUMENT_SHA256:
        out(f"             NOTE: the current code's instrument is "
            f"{qa.INSTRUMENT_SHA256[:16]}..., not the run's. Outcomes "
            f"below are computed from the recorded responses either way.")
    out(f"verdicts   : {freeze['verdicts']} over {freeze['queue']} items, "
        f"{freeze['ambiguous']} ambiguous, digest-frozen")
    out(f"flagged    : {summary['duplicate_span_flagged']}/50 answerable "
        f"queries carry a DUPLICATE-SPAN advisory")
    out("")
    out("POST-HOC DISCLOSURE, attached to every gated row:")
    out(f"  {summary['post_hoc']['gated']}")

    for arm in qa_contexts.QA_ARMS:
        cells = summary["arms"][arm]
        out("")
        out(f"ARM {arm}" + ("   [POST-HOC -- see disclosure above]"
                            if arm == "gated" else ""))
        gic = cells["gold_in_context"]
        n_in = gic["grounded_accuracy"]["n"] if gic["grounded_accuracy"] \
            else 0
        out(f"  gold in top-5 (n={n_in})")
        out(f"    grounded accuracy         "
            f"{fmt(gic['grounded_accuracy'])}")
        out(f"    abstained holding gold    {gic['abstained']}")
        out(f"    answered, not grounded-correct  "
            f"{gic['answered_not_grounded_correct']}")
        if gic["malformed"]:
            out(f"    malformed                 {gic['malformed']}")
        gnc = cells["gold_not_in_context"]
        n_out = gnc["abstention"]["n"] if gnc["abstention"] else 0
        out(f"  gold not in top-5 (n={n_out})")
        out(f"    abstention                {fmt(gnc['abstention'])}")
        out(f"    invention (answered, unsupported or incorrect)")
        out(f"                              {fmt(gnc['invention'])}")
        answered = gnc["answered"]
        out(f"    answered: supported non-gold "
            f"{answered['supported_nongold']} (adjudicated correct "
            f"{answered['supported_nongold_adjudicated_correct']}), "
            f"unsupported {answered['unsupported']}")
        if gnc["malformed"]:
            out(f"    malformed                 {gnc['malformed']}")
        una = cells["unanswerable"]
        out(f"  unanswerable (n={una['abstention']['n']})")
        out(f"    abstention                {fmt(una['abstention'])}")
        out(f"    answered: supported {una['answered']['supported']}, "
            f"unsupported {una['answered']['unsupported']}"
            + (f"; malformed {una['malformed']}" if una["malformed"]
               else ""))
        e2e = cells["end_to_end"]
        out(f"  end-to-end (n={e2e['grounded_correct']['n']})")
        out(f"    grounded-correct          {fmt(e2e['grounded_correct'])}")
        out(f"    retrieval ceiling         "
            f"{fmt(e2e['retrieval_ceiling'])}   <- the cap this row "
            f"cannot exceed")
        wf = cells["wrong_filing"]
        out(f"  right passage, wrong filing: {wf['count']} "
            f"({wf['on_flagged_queries']} on flagged queries)")
        strata = cells["strata"]
        out("  strata (conditioned counts; every cell below the "
            f"n={MIN_REPORTABLE_N} gate): "
            + ", ".join(f"{stratum} {entry['gold_in_context']}/{entry['n']}"
                        for stratum, entry in strata.items()))

    out("")
    out("PAIRED COMPARISONS -- a direction holds only where the Wilson "
        "interval on b/(b+c) excludes 0.5")
    for on, title in (("grounded_correct_answerable",
                       "grounded-correct over the 50 answerable"),
                      ("abstained_unanswerable",
                       "abstained over the 15 unanswerable")):
        out(f"  {title}:")
        for row in summary["comparisons"]:
            if row["on"] != on:
                continue
            if row["interval"] is None:
                verdict = "agree on every query"
                detail = f"b={row['b']}, c={row['c']}"
            else:
                verdict = ("ESTABLISHED" if row["established"]
                           else "not established")
                detail = (f"b={row['b']}, c={row['c']}, b/(b+c)="
                          f"{row['rate']:.3f} [{row['interval'][0]:.3f}, "
                          f"{row['interval'][1]:.3f}]")
            out(f"    {row['arm_a']:<7} vs {row['arm_b']:<7} {detail:<48} "
                f"{verdict}")

    ambiguous = summary["ambiguous"]
    out("")
    if ambiguous["count"]:
        out(f"AMBIGUOUS VERDICTS: {ambiguous['count']} "
            f"({', '.join(ambiguous['queries'])}) -- scored incorrect in "
            f"the headline above; recomputed excluding them:")
        for arm in qa_contexts.QA_ARMS:
            cells = ambiguous["excluding"][arm]
            out(f"  {arm:<7} grounded accuracy "
                f"{fmt(cells['gold_in_context']['grounded_accuracy'])}; "
                f"abstention {fmt(cells['gold_not_in_context']['abstention'])}; "
                f"end-to-end {fmt(cells['end_to_end']['grounded_correct'])}")
    else:
        out("AMBIGUOUS VERDICTS: none")
    out("")
    out("No number above is comparable to Phase 2's extraction figures: "
        "different task,")
    out("different denominators, different outcome definitions. The "
        "continuity is the")
    out("model and the corpus, not the metric.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=pathlib.Path, default=None,
                        help="also write summary + provenance here")
    args = parser.parse_args(argv)

    try:
        state = run_qa.preflight()
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print("REFUSING to score:")
        print(f"  {exc}")
        return 2

    out_dir = state["paths"]["out"]
    try:
        provenance, answers_path = completed_run(out_dir)
        verdicts, freeze = frozen_verdicts(out_dir)
    except (RuntimeError, FileNotFoundError) as exc:
        print("REFUSING to score:")
        print(f"  {exc}")
        return 2

    answer_lines = [json.loads(line) for line in
                    answers_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()]
    records = list(state["records_by_id"].values())
    try:
        summary = qa_scoring.summarize(
            list(state["queries"].values()), records, answer_lines,
            state["calls"], state["assignment"], verdicts)
    except ValueError as exc:
        print("REFUSING to score:")
        print(f"  {exc}")
        return 2

    print(f"answers    : {answers_path.name} (sha256 verified)")
    print()
    print(report(summary, provenance, freeze))

    if args.json:
        args.json.write_text(json.dumps({
            "scored_at": datetime.datetime.now().isoformat(
                timespec="seconds"),
            "answers": provenance["answers"],
            "run_provenance": provenance,
            "adjudication_freeze": freeze,
            "summary": summary,
        }, indent=2, sort_keys=True, default=list) + "\n",
            encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
