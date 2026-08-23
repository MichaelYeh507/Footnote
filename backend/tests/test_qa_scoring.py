"""The QA cells: assembled only, from one hit test and blind verdicts.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
failures this file is written against:

  **A defaulted verdict.** An answered answerable item without a verdict
  must refuse, never score as anything.

  **Ambiguity flattering the pipeline.** An ambiguous verdict scores
  incorrect in the headline; the excluded re-table drops the item from
  numerator and denominator both. Getting either half wrong moves the
  phase's primary cell.

  **The invention measure drifting.** It is registered as "answered, and
  unsupported or adjudicated incorrect". A supported non-gold answer the
  adjudicator called correct is NOT invention, and malformed is not
  answered.

  **Wrong-filing counted off its definition**, or off the flagged set.

The world: qA's gold span also lives byte-identical in a second accession
(the DUPLICATE-SPAN case), hybrid retrieves that twin and cites it; qB's
gold answer draws an ambiguous verdict; the unanswerable qU is abstained by
three arms and answered by one.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_adjudication as adj  # noqa: E402
from evaluation import qa_contexts  # noqa: E402
from evaluation import qa_scoring  # noqa: E402

SPAN_A = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
SPAN_B = "one two three four five six seven eight nine ten eleven twelve"


def _record(cid, accession, text):
    return {"chunk_id": cid, "accession": accession, "ticker": "TST",
            "period": "2024-12-31", "item": "1", "title": "", "index": 0,
            "first_page": 1, "last_page": 1, "tokens": 12, "text": text}


RECORDS = [
    _record("gA", "acc-A", f"Ahead. {SPAN_A}. Behind."),
    _record("gA2", "acc-A2", f"Ahead. {SPAN_A}. Behind."),
    _record("gB", "acc-B", f"Start. {SPAN_B}. End."),
    _record("cold1", "acc-C", "cold text one"),
    _record("cold2", "acc-C", "cold text two"),
    _record("cold3", "acc-C", "cold text three"),
    _record("cold4", "acc-C", "cold text four"),
    _record("cold5", "acc-C", "cold text five"),
]

QUERIES = [
    {"query_id": "qA", "stratum": "exact_entity", "query": "About A?",
     "gold": [{"accession": "acc-A", "span": SPAN_A}]},
    {"query_id": "qB", "stratum": "conceptual", "query": "About B?",
     "gold": [{"accession": "acc-B", "span": SPAN_B}]},
    {"query_id": "qU", "stratum": "unanswerable", "query": "About U?",
     "gold": []},
]

COLDS = ("cold1", "cold2", "cold3", "cold4", "cold5")
CTXS = {
    "qA": {"sparse": COLDS,
           "dense": ("gA", "cold1", "cold2", "cold3", "cold4"),
           "hybrid": ("gA2", "cold1", "cold2", "cold3", "cold4"),
           "gated": ("gA", "cold1", "cold2", "cold3", "cold4")},
    "qB": {"sparse": COLDS,
           "dense": ("gB", "cold1", "cold2", "cold3", "cold4"),
           "hybrid": ("cold2", "cold3", "cold4", "cold5", "cold1"),
           "gated": ("gB", "cold1", "cold2", "cold3", "cold4")},
    "qU": {"sparse": COLDS, "dense": COLDS,
           "hybrid": ("cold5", "cold4", "cold3", "cold2", "cold1"),
           "gated": COLDS},
}

ABSTAIN = '{"answer": null, "citation": null, "quote": null}'
RESPONSES = {
    ("qA", COLDS): ABSTAIN,
    ("qA", CTXS["qA"]["dense"]):
        '{"answer": "the A value", "citation": 1, '
        '"quote": "alpha beta gamma delta"}',
    ("qA", CTXS["qA"]["hybrid"]):
        '{"answer": "the A value", "citation": 1, '
        '"quote": "alpha beta gamma delta"}',
    ("qB", COLDS):
        '{"answer": "a B guess", "citation": null, "quote": null}',
    ("qB", CTXS["qB"]["dense"]):
        '{"answer": "the B value", "citation": 1, '
        '"quote": "one two three four"}',
    ("qB", CTXS["qB"]["hybrid"]): "oops not json",
    ("qU", COLDS): ABSTAIN,
    ("qU", CTXS["qU"]["hybrid"]):
        '{"answer": "a U claim", "citation": 1, "quote": "cold text five"}',
}

VERDICTS = {
    adj.answer_key("qA", "the A value"):
        {"verdict": "correct", "ambiguous": False},
    adj.answer_key("qB", "a B guess"):
        {"verdict": "incorrect", "ambiguous": False},
    adj.answer_key("qB", "the B value"):
        {"verdict": "correct", "ambiguous": True},
}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(qa_contexts, "SPLIT_CONTROL",
                        {"sparse": 0, "dense": 2, "hybrid": 0, "gated": 2})
    calls, assignment = qa_contexts.dedupe(CTXS)
    lines = [{"call_id": cid, "query_id": call["query_id"],
              "excerpt_ids": list(call["excerpt_ids"]),
              "raw": RESPONSES[(call["query_id"], call["excerpt_ids"])]}
             for cid, call in calls.items()]
    return {"calls": calls, "assignment": assignment, "lines": lines}


def _summary(world, verdicts=None):
    return qa_scoring.summarize(QUERIES, RECORDS, world["lines"],
                                world["calls"], world["assignment"],
                                VERDICTS if verdicts is None else verdicts)


def test_the_world_dedupes_as_designed(world):
    assert len(world["calls"]) == 8
    assert len(world["assignment"]) == 12


def test_grounded_accuracy_counts_gold_citation_and_verdict(world):
    dense = _summary(world)["arms"]["dense"]
    cell = dense["gold_in_context"]
    # qA is grounded-correct; qB cited gold but its verdict is ambiguous,
    # which scores incorrect in the headline.
    assert cell["grounded_accuracy"]["hits"] == 1
    assert cell["grounded_accuracy"]["n"] == 2
    assert cell["abstained"] == 0
    assert cell["answered_not_grounded_correct"] == 1


def test_abstention_and_invention_in_the_gold_out_cell(world):
    sparse = _summary(world)["arms"]["sparse"]
    cell = sparse["gold_not_in_context"]
    assert cell["abstention"]["hits"] == 1 and cell["abstention"]["n"] == 2
    assert cell["invention"]["hits"] == 1  # qB: answered, unsupported
    assert cell["answered"]["unsupported"] == 1


def test_supported_correct_nongold_is_not_invention(world):
    hybrid = _summary(world)["arms"]["hybrid"]
    cell = hybrid["gold_not_in_context"]
    assert cell["answered"]["supported_nongold"] == 1
    assert cell["answered"]["supported_nongold_adjudicated_correct"] == 1
    assert cell["malformed"] == 1  # qB's not-json
    # qA: supported and adjudicated correct -> not invention; qB: malformed
    # -> not answered -> not invention.
    assert cell["invention"]["hits"] == 0


def test_wrong_filing_is_counted_and_landed_on_the_flagged_query(world):
    summary = _summary(world)
    assert summary["duplicate_span_flagged"] == 1
    assert summary["arms"]["hybrid"]["wrong_filing"] == {
        "count": 1, "on_flagged_queries": 1}
    assert summary["arms"]["dense"]["wrong_filing"]["count"] == 0


def test_unanswerable_cell_is_mechanical(world):
    summary = _summary(world)
    assert summary["arms"]["dense"]["unanswerable"]["abstention"]["hits"] == 1
    hybrid = summary["arms"]["hybrid"]["unanswerable"]
    assert hybrid["abstention"]["hits"] == 0
    assert hybrid["answered"]["supported"] == 1


def test_end_to_end_carries_the_ceiling(world):
    dense = _summary(world)["arms"]["dense"]["end_to_end"]
    assert dense["grounded_correct"]["hits"] == 1
    assert dense["retrieval_ceiling"]["hits"] == 2
    assert dense["retrieval_ceiling"]["n"] == 2


def test_ambiguous_scores_against_the_pipeline_and_is_re_reported(world):
    summary = _summary(world)
    assert summary["ambiguous"]["count"] == 1
    assert summary["ambiguous"]["queries"] == ["qB"]
    excluding = summary["ambiguous"]["excluding"]["dense"]
    # qB dropped from numerator and denominator both.
    assert excluding["gold_in_context"]["grounded_accuracy"]["hits"] == 1
    assert excluding["gold_in_context"]["grounded_accuracy"]["n"] == 1
    assert excluding["end_to_end"]["grounded_correct"]["n"] == 1


def test_gated_shares_dense_cells_by_construction(world):
    summary = _summary(world)
    assert summary["arms"]["gated"]["gold_in_context"] == \
        summary["arms"]["dense"]["gold_in_context"]
    assert "gated" in summary["post_hoc"]
    assert "post-hoc" in summary["post_hoc"]["gated"]


def test_all_six_pairs_on_both_shared_denominators(world):
    comparisons = _summary(world)["comparisons"]
    assert len(comparisons) == 12  # 6 pairs x 2 outcomes
    row = next(c for c in comparisons
               if c["arm_a"] == "sparse" and c["arm_b"] == "dense"
               and c["on"] == "grounded_correct_answerable")
    assert (row["b"], row["c"]) == (0, 1)
    assert row["established"] is False
    agree = next(c for c in comparisons
                 if c["arm_a"] == "dense" and c["arm_b"] == "gated"
                 and c["on"] == "grounded_correct_answerable")
    assert agree["interval"] is None and agree["established"] is False


def test_direction_established_is_the_interval_test():
    assert qa_scoring.direction_established((0.646, 1.0)) is True
    assert qa_scoring.direction_established((0.0, 0.39)) is True
    assert qa_scoring.direction_established((0.487, 0.974)) is False
    assert qa_scoring.direction_established(None) is False


def test_a_missing_verdict_refuses_by_name(world):
    verdicts = dict(VERDICTS)
    del verdicts[adj.answer_key("qB", "a B guess")]
    with pytest.raises(ValueError, match="qB/sparse.*no standing verdict"):
        _summary(world, verdicts)


def test_a_retracted_verdict_refuses_rather_than_defaults(world):
    verdicts = dict(VERDICTS)
    verdicts[adj.answer_key("qB", "a B guess")] = {
        "verdict": adj.RETRACTED, "ambiguous": False}
    with pytest.raises(ValueError, match="qB/sparse.*retracted"):
        _summary(world, verdicts)


def test_answers_must_cover_the_contexts_exactly(world):
    with pytest.raises(ValueError, match="cover the re-derived contexts"):
        qa_scoring.summarize(QUERIES, RECORDS, world["lines"][:-1],
                             world["calls"], world["assignment"], VERDICTS)


def test_a_recorded_context_from_another_world_refuses(world):
    lines = [dict(line) for line in world["lines"]]
    lines[0] = dict(lines[0], excerpt_ids=list(reversed(
        lines[0]["excerpt_ids"])))
    with pytest.raises(ValueError, match="another world"):
        qa_scoring.summarize(QUERIES, RECORDS, lines, world["calls"],
                             world["assignment"], VERDICTS)


def test_a_duplicate_call_refuses(world):
    lines = world["lines"] + [world["lines"][0]]
    with pytest.raises(ValueError, match="appears twice"):
        qa_scoring.summarize(QUERIES, RECORDS, lines, world["calls"],
                             world["assignment"], VERDICTS)


def test_split_is_rederived_from_recorded_contexts(world, monkeypatch):
    monkeypatch.setattr(qa_contexts, "SPLIT_CONTROL",
                        {"sparse": 0, "dense": 1, "hybrid": 0, "gated": 2})
    with pytest.raises(ValueError, match="never the denominator"):
        _summary(world)
