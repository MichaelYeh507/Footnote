"""The blind queue and the verdicts: strip, dedupe, shuffle, freeze.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
failures this file is written against:

  **A field surviving the strip.** The queue is the only thing the
  adjudication server holds, so a citation or an arm name that survives
  `blind_queue` is shown to the adjudicator. The shape is asserted exactly:
  five keys, no more.

  **Two verdicts for one answer.** Identical (query, normalized answer)
  pairs must collapse to one item, or identical outputs could drift into
  different verdicts across arms.

  **A freeze over partial coverage.** The digest is only meaningful if it
  covers a complete adjudication; freezing early would let missing verdicts
  default silently in the scorer.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_adjudication as adj  # noqa: E402


def _query(qid, stratum="exact_entity", span="the gold span text"):
    gold = [] if stratum == "unanswerable" else [
        {"accession": "acc-A", "span": span}]
    return {"query_id": qid, "stratum": stratum,
            "query": f"question for {qid}?", "gold": gold}


def _line(qid, raw):
    return {"call_id": f"{qid}-abc", "query_id": qid, "raw": raw}


_ANSWERED = '{"answer": "the answer", "citation": 1, "quote": "q"}'
_ABSTAINED = '{"answer": null, "citation": null, "quote": null}'


def test_answer_key_folds_through_the_published_normalization():
    a, b = "More  Than 5,000", "more than 5,000"
    assert a != b  # the guard: raw strings differ, only normalize equates
    assert adj.answer_key("q001", a) == adj.answer_key("q001", b)
    assert adj.answer_key("q001", a) != adj.answer_key("q002", a)


def test_blind_queue_strips_to_exactly_five_fields():
    queue = adj.blind_queue([_line("q001", _ANSWERED)], [_query("q001")])
    assert len(queue) == 1
    assert set(queue[0]) == {"key", "query_id", "question", "gold_spans",
                             "answer"}
    assert queue[0]["answer"] == "the answer"
    assert queue[0]["gold_spans"] == ["the gold span text"]


def test_blind_queue_skips_what_needs_no_verdict():
    lines = [
        _line("q001", _ABSTAINED),            # abstained: asserts nothing
        _line("q002", "not json"),            # malformed: instrument state
        _line("q003", _ANSWERED),             # unanswerable: mechanical
    ]
    queries = [_query("q001"), _query("q002"),
               _query("q003", stratum="unanswerable")]
    assert adj.blind_queue(lines, queries) == []


def test_blind_queue_dedupes_identical_answers_per_query():
    lines = [
        _line("q001", '{"answer": "Same  Answer", "citation": 1, '
                      '"quote": "a"}'),
        _line("q001", '{"answer": "same answer", "citation": 2, '
                      '"quote": "b"}'),
        _line("q002", '{"answer": "same answer", "citation": 1, '
                      '"quote": "c"}'),
    ]
    queue = adj.blind_queue(lines, [_query("q001"), _query("q002")])
    assert len(queue) == 2
    assert {item["query_id"] for item in queue} == {"q001", "q002"}


def test_blind_queue_refuses_an_unknown_query():
    with pytest.raises(ValueError, match="different worlds"):
        adj.blind_queue([_line("q999", _ANSWERED)], [_query("q001")])


def test_queue_order_is_a_deterministic_seeded_shuffle():
    lines = [_line(f"q{n:03d}",
                   '{"answer": "answer %d", "citation": 1, "quote": "q"}'
                   % n)
             for n in range(1, 13)]
    queries = [_query(f"q{n:03d}") for n in range(1, 13)]
    first = adj.blind_queue(lines, queries)
    second = adj.blind_queue(lines, queries)
    assert [i["key"] for i in first] == [i["key"] for i in second]
    assert [i["key"] for i in first] != sorted(i["key"] for i in first)


def test_validate_verdict_names_each_problem():
    keys = {"k1"}
    assert adj.validate_verdict(
        {"key": "k1", "verdict": "correct", "ambiguous": False}, keys) == []
    problems = adj.validate_verdict(
        {"key": "k9", "verdict": "maybe", "ambiguous": None}, keys)
    assert len(problems) == 3


def test_read_verdicts_is_last_write_wins(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    assert adj.read_verdicts(path) == {}
    with open(path, "a", encoding="utf-8") as handle:
        for verdict in ("incorrect", "correct"):
            record = adj.verdict_record("k1", verdict, False)
            import json
            handle.write(json.dumps(record) + "\n")
    verdicts = adj.read_verdicts(path)
    assert verdicts["k1"]["verdict"] == "correct"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_freeze_refuses_partial_coverage(tmp_path):
    path = tmp_path / "verdicts.jsonl"
    path.write_text("", encoding="utf-8")
    queue = adj.blind_queue([_line("q001", _ANSWERED)], [_query("q001")])
    with pytest.raises(RuntimeError, match="1 of 1 items"):
        adj.freeze_verdicts(path, queue)


def test_freeze_records_the_digest_and_the_ambiguous_count(tmp_path):
    import hashlib
    import json
    path = tmp_path / "verdicts.jsonl"
    queue = adj.blind_queue(
        [_line("q001", _ANSWERED),
         _line("q002", '{"answer": "other", "citation": 1, "quote": "q"}')],
        [_query("q001"), _query("q002")])
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(adj.verdict_record(
            queue[0]["key"], "correct", False)) + "\n")
        handle.write(json.dumps(adj.verdict_record(
            queue[1]["key"], "incorrect", True)) + "\n")
    record = adj.freeze_verdicts(path, queue)
    assert record["verdicts"] == 2
    assert record["ambiguous"] == 1
    assert record["shuffle_seed"] == adj.SHUFFLE_SEED == 20260821
    assert record["file_sha256"] == hashlib.sha256(
        path.read_bytes()).hexdigest()
