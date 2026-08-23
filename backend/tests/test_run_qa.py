"""The QA runner: refusals, controls gating, one run, resume without re-ask.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
failures this file is written against:

  **An eval-set call before the gates.** The run must refuse without recorded
  green controls naming the current instrument, refuse a drifted artifact,
  and refuse a split that fails to re-derive the published numerators.

  **A second run.** A completed provenance means the one run exists; the
  runner must refuse, not append, not overwrite.

  **Resume re-asking.** A crashed run resumes by skipping recorded calls; a
  resume that re-called one would regenerate an answer, which the no-relax
  clause forbids. The fake client counts questions asked, so a re-ask is a
  counted fact rather than an inference.

The whole world here is synthetic and pinned per-test: the module's digest
pins are monkeypatched to the synthetic files' actual hashes, which is the
test seam -- the literal pins themselves are asserted in
`test_qa_contexts.py` and are not weakened by this.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_contexts  # noqa: E402
from scripts import run_qa  # noqa: E402

SPAN = "the twelve word span that answers the question sits here in full"


def _chunk(cid, accession, text, ticker="GWW"):
    return {"chunk_id": cid, "accession": accession, "ticker": ticker,
            "period": "2024-12-31", "item": "1", "title": "", "index": 0,
            "first_page": 1, "last_page": 1, "tokens": 10, "text": text}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A tiny pinned universe: 2 queries, 6 chunks, 4 arms, real digests."""
    data = tmp_path / "data"
    (data / "filings").mkdir(parents=True)
    (data / "retrieval").mkdir()
    (data / "chunks").mkdir()
    (data / "queries").mkdir()
    (data / "calibration").mkdir()
    monkeypatch.setenv("RAG_FILINGS_DIR", str(data / "filings"))
    monkeypatch.setenv("RAG_CALIBRATION_DIR", str(data / "calibration"))

    chunks = [_chunk("gold-1", "acc-A", f"Filler. {SPAN}. More filler.")]
    chunks += [_chunk(f"cold-{n}", "acc-A", f"nothing here {n}")
               for n in range(1, 6)]
    chunks_path = data / "chunks" / run_qa.CHUNKS_FILE
    chunks_path.write_text(
        "".join(json.dumps(c) + "\n" for c in chunks), encoding="utf-8")

    hit = [["gold-1", 0.1], ["cold-1", 0.2], ["cold-2", 0.3],
           ["cold-3", 0.4], ["cold-4", 0.5]]
    miss = [["cold-1", 0.1], ["cold-2", 0.2], ["cold-3", 0.3],
            ["cold-4", 0.4], ["cold-5", 0.5]]
    rankings_path = data / "retrieval" / run_qa.RANKINGS_FILE
    rankings_path.write_text("".join(
        json.dumps({"query_id": qid,
                    "arms": {"sparse": miss, "dense": hit, "hybrid": miss}})
        + "\n" for qid in ("q001", "q002")), encoding="utf-8")

    gated_path = data / "retrieval" / run_qa.GATED_FILE
    gated_path.write_text("".join(
        json.dumps({"query_id": qid, "gated": True, "ranking": hit}) + "\n"
        for qid in ("q001", "q002")), encoding="utf-8")

    queries = [
        {"query_id": "q001", "stratum": "exact_entity",
         "query": "Where does the span sit?",
         "gold": [{"accession": "acc-A", "span": SPAN}]},
        {"query_id": "q002", "stratum": "unanswerable",
         "query": "What is unknowable?", "gold": []},
    ]
    monkeypatch.setattr(run_qa.review, "read_queries", lambda: queries)
    monkeypatch.setattr(
        run_qa.query_freeze, "refuse_unless_frozen",
        lambda q, path=None: {"set_sha256": qa_contexts.FROZEN_SET_SHA256,
                              "frozen_at": "2026-08-20"})

    monkeypatch.setattr(qa_contexts, "RANKINGS_SHA256",
                        qa_contexts.file_sha256(rankings_path))
    monkeypatch.setattr(qa_contexts, "GATED_RANKINGS_SHA256",
                        qa_contexts.file_sha256(gated_path))
    monkeypatch.setattr(qa_contexts, "CHUNKS_SHA256",
                        qa_contexts.file_sha256(chunks_path))
    monkeypatch.setattr(qa_contexts, "SPLIT_CONTROL",
                        {"sparse": 0, "dense": 1, "hybrid": 0, "gated": 1})

    text = (f"{run_qa.CONTROL_ANCHORS[0]} filler text. "
            f"{run_qa.CONTROL_ANCHORS[1]} more filler. "
            f"{run_qa.CONTROL_ANCHORS[2]} Costco operated 914, 890, and 861 "
            f"warehouses worldwide at August 31, 2025. "
            f"{run_qa.CONTROL_ANCHORS[3]} yet more. "
            f"{run_qa.CONTROL_ANCHORS[4]} the end.")
    (data / "calibration" / run_qa.CONTROL_SOURCE).write_text(
        text, encoding="utf-8")
    return {"data": data, "queries": queries}


class _FakeAsk:
    """Stands in for services.qa.ask; counts and scripts the responses."""

    def __init__(self, respond):
        self.respond = respond
        self.questions = []

    def __call__(self, question, excerpts, client=None, sleep=None):
        self.questions.append(question)
        return {"raw": self.respond(question, excerpts), "attempts": 1,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


def _abstain(question, excerpts):
    return '{"answer": null, "citation": null, "quote": null}'


def _green_controls(question, excerpts):
    if question == run_qa.POSITIVE_QUESTION:
        return ('{"answer": "914 warehouses", "citation": 3, '
                '"quote": "operated 914, 890, and 861 warehouses"}')
    return _abstain(question, excerpts)


def test_preflight_builds_the_synthetic_world(world):
    state = run_qa.preflight()
    # sparse and hybrid share identical rankings here, so they dedupe
    # together: 2 distinct contexts per query, not 4.
    assert len(state["calls"]) == 4
    assert len(state["assignment"]) == 8  # 2 queries x 4 arms
    gated = state["assignment"][("q001", "gated")]
    assert gated == state["assignment"][("q001", "dense")]


def test_preflight_refuses_a_drifted_artifact(world, monkeypatch, capsys):
    monkeypatch.setattr(qa_contexts, "RANKINGS_SHA256", "0" * 64)
    assert run_qa.main(["--dry-run"]) == 2
    out = capsys.readouterr().out
    assert "REFUSING" in out and "different experiment" in out


def test_preflight_refuses_an_unpinned_freeze(world, monkeypatch, capsys):
    monkeypatch.setattr(
        run_qa.query_freeze, "refuse_unless_frozen",
        lambda q, path=None: {"set_sha256": "e" * 64})
    assert run_qa.main(["--dry-run"]) == 2
    assert "appendix pins" in capsys.readouterr().out


def test_dry_run_calls_nothing(world, monkeypatch):
    fake = _FakeAsk(_abstain)
    monkeypatch.setattr(run_qa.qa, "ask", fake)
    assert run_qa.main(["--dry-run"]) == 0
    assert fake.questions == []


def test_controls_record_green_and_permit_the_run(world, monkeypatch):
    fake = _FakeAsk(_green_controls)
    monkeypatch.setattr(run_qa.qa, "ask", fake)
    assert run_qa.main(["--controls"]) == 0
    record = run_qa.latest_controls(
        world["data"] / "qa")
    assert record["passed"] is True
    assert record["controls"]["positive"]["stable"] is True
    assert record["instrument_sha256"] == run_qa.qa.INSTRUMENT_SHA256
    assert len(fake.questions) == 2 * run_qa.STABILITY_REPEATS


def test_a_failed_negative_control_fails_the_controls(world, monkeypatch):
    def leaky(question, excerpts):
        if question == run_qa.NEGATIVE_QUESTION:
            return ('{"answer": "Ron Vachris", "citation": 1, '
                    '"quote": "Certain statements"}')
        return _green_controls(question, excerpts)
    monkeypatch.setattr(run_qa.qa, "ask", _FakeAsk(leaky))
    assert run_qa.main(["--controls"]) == 1
    record = run_qa.latest_controls(world["data"] / "qa")
    assert record["passed"] is False
    problems = record["controls"]["negative"]["repeats"][0]["problems"]
    assert any("memory" in problem for problem in problems)


def test_eval_refuses_without_controls(world, monkeypatch, capsys):
    monkeypatch.setattr(run_qa.qa, "ask", _FakeAsk(_abstain))
    assert run_qa.main([]) == 2
    assert "no controls" in capsys.readouterr().out


def test_eval_refuses_failed_controls(world, monkeypatch, capsys):
    def leaky(question, excerpts):
        if question == run_qa.NEGATIVE_QUESTION:
            return '{"answer": "x", "citation": 1, "quote": "Certain"}'
        return _green_controls(question, excerpts)
    monkeypatch.setattr(run_qa.qa, "ask", _FakeAsk(leaky))
    run_qa.main(["--controls"])
    assert run_qa.main([]) == 2
    assert "did not pass" in capsys.readouterr().out


def test_eval_refuses_a_stale_instrument(world, monkeypatch, capsys):
    monkeypatch.setattr(run_qa.qa, "ask", _FakeAsk(_green_controls))
    run_qa.main(["--controls"])
    monkeypatch.setattr(run_qa.qa, "INSTRUMENT_SHA256", "f" * 64)
    assert run_qa.main([]) == 2
    assert "different instrument" in capsys.readouterr().out


def _run_green(world, monkeypatch, respond=_abstain):
    monkeypatch.setattr(run_qa.qa, "ask", _FakeAsk(_green_controls))
    assert run_qa.main(["--controls"]) == 0
    fake = _FakeAsk(respond)
    monkeypatch.setattr(run_qa.qa, "ask", fake)
    return fake


def test_the_run_answers_every_distinct_context_once(world, monkeypatch):
    fake = _run_green(world, monkeypatch)
    assert run_qa.main([]) == 0
    qa_dir = world["data"] / "qa"
    answers_path = next(qa_dir.glob("answers-*.jsonl"))
    lines = [json.loads(line) for line in
             answers_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 4
    assert len({line["call_id"] for line in lines}) == 4
    assert len(fake.questions) == 4
    shared = [line for line in lines if line["query_id"] == "q001"
              and "dense" in line["arms"]]
    assert shared[0]["arms"] == ["dense", "gated"]
    assert all(line["query_sha256"] for line in lines)

    provenance = json.loads(next(
        qa_dir.glob("qa-provenance-*.json")).read_text(encoding="utf-8"))
    assert provenance["complete"] is True
    assert provenance["calls"] == 4
    assert provenance["arm_query_rows"] == 8
    assert len(provenance["assignment"]) == 8
    assert provenance["conditioned_split"]["dense"] == {
        "gold_in_context": 1, "answerable": 1}


def test_a_completed_run_refuses_to_run_again(world, monkeypatch, capsys):
    _run_green(world, monkeypatch)
    assert run_qa.main([]) == 0
    fake = _FakeAsk(_abstain)
    monkeypatch.setattr(run_qa.qa, "ask", fake)
    assert run_qa.main([]) == 2
    assert "One run, by pre-registration" in capsys.readouterr().out
    assert fake.questions == []


def test_resume_skips_recorded_calls_and_re_asks_nothing(world, monkeypatch):
    _run_green(world, monkeypatch)
    state = run_qa.preflight()
    first = sorted(state["calls"])[0]
    qa_dir = world["data"] / "qa"
    partial = qa_dir / "answers-19990101-000000.jsonl"
    call = state["calls"][first]
    partial.write_text(json.dumps({
        "call_id": first, "query_id": call["query_id"],
        "query_sha256": "x", "excerpt_ids": list(call["excerpt_ids"]),
        "arms": call["arms"], "raw": _abstain(None, None), "attempts": 1,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        "asked_at": "1999-01-01T00:00:00"}) + "\n", encoding="utf-8")

    fake = _FakeAsk(_abstain)
    monkeypatch.setattr(run_qa.qa, "ask", fake)
    assert run_qa.main([]) == 0
    assert len(fake.questions) == 3  # one of four was already recorded
    lines = [json.loads(line) for line in
             partial.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 4
    assert len({line["call_id"] for line in lines}) == 4
    provenance = qa_dir / "qa-provenance-19990101-000000.json"
    assert provenance.exists()


def test_control_excerpts_refuse_a_missing_anchor(world):
    path = (world["data"] / "calibration" / run_qa.CONTROL_SOURCE)
    path.write_text("no anchors at all", encoding="utf-8")
    with pytest.raises(RuntimeError, match="anchor 1"):
        run_qa.control_excerpts()
