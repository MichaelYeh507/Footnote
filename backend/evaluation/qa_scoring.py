"""Turning recorded answers and blind verdicts into the pre-registered cells.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*,
published before any QA output existed. Nothing here defines an outcome --
that is `evaluation/qa_outcomes.py` -- and nothing here defines a hit, a
denominator or a cutoff: contexts and the split control come from
`evaluation/qa_contexts.py`, the hit test from
`evaluation/retrieval_gold.py`, Wilson rows and the paired test from
`evaluation/retrieval_scoring.py`. This module only assembles.

**The cells, per arm, exactly as registered:**

  gold_in_context      grounded accuracy -- cited gold, quote supported,
                       adjudicated correct -- plus who abstained while
                       holding the answer, and who answered without being
                       grounded-correct
  gold_not_in_context  the abstention rate, the answered breakdown, and the
                       registered invention measure: the answered share that
                       is unsupported or adjudicated incorrect
  unanswerable         the abstention rate; any answered outcome is a
                       failure, mechanically
  end_to_end           grounded-correct over all 50, never printed without
                       the arm's recall@5 ceiling beside it
  wrong_filing         the right-passage-wrong-filing count, and its
                       incidence on the flagged DUPLICATE-SPAN queries
  strata               per-stratum conditioned counts -- counts, labeled,
                       every such cell below the reporting gate

**The split is derived from the recorded excerpt ids** -- the bytes the
model actually saw -- under the published `hit_at_k`, then required to
reproduce the published recall@5 numerators via `verify_split_control`. A
recorded run that does not re-derive 10/22/18/25 refuses to score.

**Ambiguous is scored against the pipeline and reported both ways.** An
ambiguous verdict counts as incorrect in every headline cell; the affected
tables are recomputed with ambiguous items excluded (numerator and
denominator) so a reader sees the judgement's leverage.

**Paired comparisons only on denominators the arms share whole**: grounded-
correct over the 50 answerable and abstained over the 15 unanswerable, all
six pairs, direction claimed only where the Wilson interval on b/(b+c)
excludes 0.5. The conditioned cells are never paired -- their denominators
are different query subsets per arm.

**Refusals over silence.** A missing verdict for an answered answerable
item, an answers file whose calls disagree with the re-derived contexts, or
a recorded context that differs from what the pinned artifacts derive all
refuse by name. A scorer that defaulted any of them would move a number
quietly.
"""

from itertools import combinations

from evaluation import qa_adjudication
from evaluation import qa_contexts
from evaluation import qa_outcomes
from evaluation import retrieval_gold as gold
from evaluation import retrieval_scoring as scoring
from evaluation.qa_outcomes import QAOutcome

POST_HOC_NOTE = (
    "gated is Phase 3b's post-hoc arm: designed after Phase 3's results "
    "were published, on the same 65 queries. Its rows here inherit that "
    "disclosure -- a hypothesis consistent with the data that suggested "
    "it, never an independent confirmation.")

COMPARISON_PAIRS = tuple(combinations(qa_contexts.QA_ARMS, 2))


def direction_established(interval) -> bool:
    """Whether a paired direction may be claimed: the Wilson interval on
    b/(b+c) excludes 0.5. None -- no discordant pairs -- establishes
    nothing, and neither does an interval that straddles the null."""
    return interval is not None and (interval[0] > 0.5 or interval[1] < 0.5)


def derive_gold(queries: list[dict], records: list[dict]) -> tuple:
    """(gold_ids, locations) per query id, from the store at scoring time."""
    gold_ids, locations = {}, {}
    for query in queries:
        qid = query["query_id"]
        locations[qid] = scoring.locations(query)
        gold_ids[qid] = (gold.gold_chunk_ids_for(records, locations[qid])
                        if query.get("gold") else [])
    return gold_ids, locations


def conditioned_split(answerable: list[dict], calls: dict, assignment: dict,
                      gold_ids: dict) -> dict:
    """arm -> {query_id: gold in the recorded context}, then the control.

    Derived from the *recorded* excerpt ids under the published `hit_at_k`,
    so the denominators describe exactly what the model saw -- and required
    to reproduce the published numerators, so a drifted recording cannot
    move a denominator quietly.
    """
    split = {
        arm: {q["query_id"]: gold.hit_at_k(
            list(calls[assignment[(q["query_id"], arm)]]["excerpt_ids"]),
            gold_ids[q["query_id"]], qa_contexts.CONTEXT_K)
            for q in answerable}
        for arm in qa_contexts.QA_ARMS}
    qa_contexts.verify_split_control(split)
    return split


def assemble_rows(queries: list[dict], records: list[dict],
                  answer_lines: list[dict], calls: dict,
                  assignment: dict, verdicts: dict, gold_ids: dict,
                  locations: dict) -> dict:
    """(query_id, arm) -> everything one row's cells need.

    Verifies the answers file against the re-derived contexts first: same
    call ids, same excerpt ids per call. The answers are the one artifact
    produced by the run rather than pinned in the appendix, so they are
    checked against what the pinned artifacts imply instead.
    """
    by_call = {}
    for line in answer_lines:
        if line["call_id"] in by_call:
            raise ValueError(
                f"call {line['call_id']} appears twice in the answers. One "
                f"context is asked once; a duplicate means the file mixes "
                f"invocations.")
        by_call[line["call_id"]] = line
    missing = sorted(set(calls) - set(by_call))
    extra = sorted(set(by_call) - set(calls))
    if missing or extra:
        raise ValueError(
            f"the answers do not cover the re-derived contexts exactly "
            f"(missing {missing[:3]}, extra {extra[:3]}). The run and the "
            f"scorer must read the same pinned artifacts.")
    for cid, call in calls.items():
        if tuple(by_call[cid]["excerpt_ids"]) != call["excerpt_ids"]:
            raise ValueError(
                f"call {cid} was answered over a different context than the "
                f"pinned artifacts derive. The answers file is from another "
                f"world.")

    records_by_id = {record["chunk_id"]: record for record in records}
    answerable_ids = {q["query_id"] for q in
                      scoring.split_by_answerability(queries)["answerable"]}

    rows = {}
    for (qid, arm), cid in assignment.items():
        line = by_call[cid]
        parsed = qa_outcomes.parse_response(line["raw"])
        excerpts = qa_contexts.excerpts_for(calls[cid]["excerpt_ids"],
                                            records_by_id)
        outcome = qa_outcomes.classify(parsed, excerpts, gold_ids[qid],
                                       locations[qid])
        answered = parsed["ok"] and parsed["answer"] is not None
        row = {
            "outcome": outcome["outcome"],
            "wrong_filing": outcome["wrong_filing"],
            "abstained": outcome["outcome"] is QAOutcome.ABSTAINED,
            "malformed": outcome["outcome"] is QAOutcome.MALFORMED,
            "answered": answered,
            "verdict_correct": None,
            "ambiguous": False,
        }
        if answered and qid in answerable_ids:
            key = qa_adjudication.answer_key(qid, parsed["answer"])
            verdict = verdicts.get(key)
            if (verdict is None
                    or verdict.get("verdict") == qa_adjudication.RETRACTED):
                raise ValueError(
                    f"{qid}/{arm}: answered, but no standing verdict for "
                    f"its key {key} (missing or retracted). Every answered "
                    f"answerable item is adjudicated before any cell is "
                    f"assembled; a retraction is an un-judgement, never a "
                    f"default.")
            row["ambiguous"] = verdict["ambiguous"]
            # Ambiguous scores incorrect in the headline: the tie resolves
            # against the pipeline.
            row["verdict_correct"] = (verdict["verdict"] == "correct"
                                      and not verdict["ambiguous"])
        rows[(qid, arm)] = row
    return rows


def grounded_correct(row: dict) -> bool:
    return (row["outcome"] is QAOutcome.SUPPORTED_GOLD
            and row["verdict_correct"] is True)


def _cells_for_arm(arm: str, rows: dict, split: dict,
                   answerable: list[dict], unanswerable_ids: list[str],
                   confidence: float, exclude_ambiguous: bool) -> dict:
    def keep(qid):
        return not (exclude_ambiguous and rows[(qid, arm)]["ambiguous"])

    in_ids = [q["query_id"] for q in answerable
              if split[arm][q["query_id"]] and keep(q["query_id"])]
    out_ids = [q["query_id"] for q in answerable
               if not split[arm][q["query_id"]] and keep(q["query_id"])]
    all_ids = [q["query_id"] for q in answerable if keep(q["query_id"])]

    def row(qid):
        return rows[(qid, arm)]

    cells = {
        "gold_in_context": {
            "grounded_accuracy": scoring.recall_row(
                sum(1 for qid in in_ids if grounded_correct(row(qid))),
                len(in_ids), confidence) if in_ids else None,
            "abstained": sum(1 for qid in in_ids if row(qid)["abstained"]),
            "answered_not_grounded_correct": sum(
                1 for qid in in_ids
                if row(qid)["answered"] and not grounded_correct(row(qid))),
            "malformed": sum(1 for qid in in_ids if row(qid)["malformed"]),
        },
        "gold_not_in_context": {
            "abstention": scoring.recall_row(
                sum(1 for qid in out_ids if row(qid)["abstained"]),
                len(out_ids), confidence) if out_ids else None,
            "answered": {
                "supported_nongold": sum(
                    1 for qid in out_ids
                    if row(qid)["outcome"] is QAOutcome.SUPPORTED_NONGOLD),
                "supported_nongold_adjudicated_correct": sum(
                    1 for qid in out_ids
                    if row(qid)["outcome"] is QAOutcome.SUPPORTED_NONGOLD
                    and row(qid)["verdict_correct"] is True),
                "unsupported": sum(
                    1 for qid in out_ids
                    if row(qid)["outcome"] is QAOutcome.UNSUPPORTED),
            },
            "malformed": sum(1 for qid in out_ids if row(qid)["malformed"]),
            # The registered invention measure: answered, and either
            # unsupported or adjudicated incorrect.
            "invention": scoring.recall_row(
                sum(1 for qid in out_ids
                    if row(qid)["answered"]
                    and (row(qid)["outcome"] is QAOutcome.UNSUPPORTED
                         or row(qid)["verdict_correct"] is False)),
                len(out_ids), confidence) if out_ids else None,
        },
        "end_to_end": {
            "grounded_correct": scoring.recall_row(
                sum(1 for qid in all_ids if grounded_correct(row(qid))),
                len(all_ids), confidence) if all_ids else None,
            "retrieval_ceiling": scoring.recall_row(
                sum(1 for qid in all_ids if split[arm][qid]),
                len(all_ids), confidence) if all_ids else None,
        },
    }
    if not exclude_ambiguous:
        cells["unanswerable"] = {
            "abstention": scoring.recall_row(
                sum(1 for qid in unanswerable_ids if row(qid)["abstained"]),
                len(unanswerable_ids), confidence),
            "answered": {
                "supported": sum(
                    1 for qid in unanswerable_ids
                    if row(qid)["outcome"] is QAOutcome.SUPPORTED_NONGOLD),
                "unsupported": sum(
                    1 for qid in unanswerable_ids
                    if row(qid)["outcome"] is QAOutcome.UNSUPPORTED),
            },
            "malformed": sum(1 for qid in unanswerable_ids
                             if row(qid)["malformed"]),
        }
    return cells


def summarize(queries: list[dict], records: list[dict],
              answer_lines: list[dict], calls: dict, assignment: dict,
              verdicts: dict, confidence: float = 0.95) -> dict:
    gold_ids, locations = derive_gold(queries, records)
    rows = assemble_rows(queries, records, answer_lines, calls, assignment,
                         verdicts, gold_ids, locations)

    split_all = scoring.split_by_answerability(queries)
    answerable = split_all["answerable"]
    unanswerable_ids = split_all["excluded"]
    split = conditioned_split(answerable, calls, assignment, gold_ids)

    flagged = {q["query_id"] for q in answerable
               if any(note.startswith(gold.DUPLICATE_NOTE)
                      for note in gold.advisory_notes(
                          scoring.locations(q), records))}

    arms = {}
    for arm in qa_contexts.QA_ARMS:
        cells = _cells_for_arm(arm, rows, split, answerable,
                               unanswerable_ids, confidence,
                               exclude_ambiguous=False)
        cells["wrong_filing"] = {
            "count": sum(1 for (qid, row_arm), row in rows.items()
                         if row_arm == arm and row["wrong_filing"]),
            "on_flagged_queries": sum(
                1 for (qid, row_arm), row in rows.items()
                if row_arm == arm and row["wrong_filing"]
                and qid in flagged),
        }
        cells["strata"] = {
            stratum: {
                "gold_in_context": sum(
                    1 for q in answerable if q["stratum"] == stratum
                    and split[arm][q["query_id"]]),
                "n": sum(1 for q in answerable
                         if q["stratum"] == stratum),
                "below_reporting_gate": True,
            }
            for stratum in scoring.ANSWERABLE_STRATA}
        arms[arm] = cells

    ambiguous_rows = sorted({qid for (qid, _), row in rows.items()
                             if row["ambiguous"]})
    excluding = {
        arm: _cells_for_arm(arm, rows, split, answerable, unanswerable_ids,
                            confidence, exclude_ambiguous=True)
        for arm in qa_contexts.QA_ARMS} if ambiguous_rows else None

    comparisons = []
    for arm_a, arm_b in COMPARISON_PAIRS:
        for name, ids, predicate in (
                ("grounded_correct_answerable",
                 [q["query_id"] for q in answerable], grounded_correct),
                ("abstained_unanswerable", unanswerable_ids,
                 lambda row: row["abstained"])):
            result = scoring.mcnemar(
                {qid: predicate(rows[(qid, arm_a)]) for qid in ids},
                {qid: predicate(rows[(qid, arm_b)]) for qid in ids},
                confidence)
            comparisons.append(dict(
                result, arm_a=arm_a, arm_b=arm_b, on=name,
                established=direction_established(result["interval"])))

    return {
        "confidence": confidence,
        "arms": arms,
        "ambiguous": {"queries": ambiguous_rows,
                      "count": len(ambiguous_rows),
                      "excluding": excluding},
        "duplicate_span_flagged": len(flagged),
        "comparisons": comparisons,
        "post_hoc": {"gated": POST_HOC_NOTE},
        "rows": {f"{qid}|{arm}": {
            "outcome": row["outcome"].value,
            "wrong_filing": row["wrong_filing"],
            "verdict_correct": row["verdict_correct"],
            "ambiguous": row["ambiguous"]}
            for (qid, arm), row in sorted(rows.items())},
    }
