"""score_qa end to end: run, adjudicate, freeze, score -- and its refusals.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). This
file walks the whole pipeline over a synthetic pinned world: controls, the
one run, a blind queue built from its answers, verdicts, the freeze, and
the report. The refusal tests pin the gates in the order a reader relies
on them: no freeze -> no cells; a verdict file edited after its freeze ->
no cells; a drifted answers file -> no cells.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_adjudication as adj  # noqa: E402
from evaluation import qa_contexts  # noqa: E402
from scripts import adjudicate_qa  # noqa: E402
from scripts import run_qa  # noqa: E402
from scripts import score_qa  # noqa: E402

SPAN = "the twelve word span that answers the question sits here in full"


def _chunk(cid, accession, text):
    return {"chunk_id": cid, "accession": accession, "ticker": "GWW",
            "period": "2024-12-31", "item": "1", "title": "", "index": 0,
            "first_page": 1, "last_page": 1, "tokens": 10, "text": text}


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for name in ("filings", "retrieval", "chunks", "queries", "calibration"):
        (data / name).mkdir(parents=True)
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
    (data / "retrieval" / run_qa.RANKINGS_FILE).write_text("".join(
        json.dumps({"query_id": qid,
                    "arms": {"sparse": miss, "dense": hit, "hybrid": miss}})
        + "\n" for qid in ("q001", "q002")), encoding="utf-8")
    (data / "retrieval" / run_qa.GATED_FILE).write_text("".join(
        json.dumps({"query_id": qid, "gated": True, "ranking": hit}) + "\n"
        for qid in ("q001", "q002")), encoding="utf-8")

    queries = [
        {"query_id": "q001", "stratum": "exact_entity",
         "query": "Where does the span sit?",
         "gold": [{"accession": "acc-A", "span": SPAN}]},
        {"query_id": "q002", "stratum": "unanswerable",
         "query": "What is unknowable?", "gold": []},
    ]
    for module in (run_qa, adjudicate_qa):
        monkeypatch.setattr(module.review, "read_queries", lambda: queries)
    monkeypatch.setattr(
        run_qa.query_freeze, "refuse_unless_frozen",
        lambda q, path=None: {"set_sha256": qa_contexts.FROZEN_SET_SHA256,
                              "frozen_at": "2026-08-20"})

    for pin, path in (("RANKINGS_SHA256", data / "retrieval"
                       / run_qa.RANKINGS_FILE),
                      ("GATED_RANKINGS_SHA256", data / "retrieval"
                       / run_qa.GATED_FILE),
                      ("CHUNKS_SHA256", chunks_path)):
        monkeypatch.setattr(qa_contexts, pin, qa_contexts.file_sha256(path))
    monkeypatch.setattr(qa_contexts, "SPLIT_CONTROL",
                        {"sparse": 0, "dense": 1, "hybrid": 0, "gated": 1})

    text = (f"{run_qa.CONTROL_ANCHORS[0]} filler. "
            f"{run_qa.CONTROL_ANCHORS[1]} filler. "
            f"{run_qa.CONTROL_ANCHORS[2]} Costco operated 914, 890, and "
            f"861 warehouses worldwide at August 31, 2025. "
            f"{run_qa.CONTROL_ANCHORS[3]} filler. "
            f"{run_qa.CONTROL_ANCHORS[4]} the end.")
    (data / "calibration" / run_qa.CONTROL_SOURCE).write_text(
        text, encoding="utf-8")
    return {"data": data, "queries": queries}


class _ScriptedAsk:
    """Green controls, then per-context eval answers keyed by what was fed."""

    def __call__(self, question, excerpts, client=None, sleep=None):
        return {"raw": self.respond(question, excerpts), "attempts": 1,
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def respond(self, question, excerpts):
        if question == run_qa.POSITIVE_QUESTION:
            return ('{"answer": "914 warehouses", "citation": 3, '
                    '"quote": "operated 914, 890, and 861 warehouses"}')
        if question == run_qa.NEGATIVE_QUESTION:
            return '{"answer": null, "citation": null, "quote": null}'
        first = excerpts[0]["chunk_id"]
        if question.startswith("Where") and first == "gold-1":
            return ('{"answer": "in the gold chunk", "citation": 1, '
                    '"quote": "the twelve word span"}')
        return '{"answer": null, "citation": null, "quote": null}'


def _run_and_adjudicate(world, monkeypatch):
    monkeypatch.setattr(run_qa.qa, "ask", _ScriptedAsk())
    assert run_qa.main(["--controls"]) == 0
    assert run_qa.main([]) == 0
    queue = adjudicate_qa.build_queue()
    assert len(queue) == 1  # one distinct answered answerable item
    verdicts_path = world["data"] / "qa" / adjudicate_qa.VERDICTS_NAME
    with open(verdicts_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(adj.verdict_record(
            queue[0]["key"], "correct", False)) + "\n")
    assert adjudicate_qa.main(["--freeze"]) == 0
    return world["data"] / "qa"


def test_the_whole_pipeline_scores_and_reports(world, monkeypatch, capsys):
    qa_dir = _run_and_adjudicate(world, monkeypatch)
    out_json = qa_dir / "qa-scores-test.json"
    assert score_qa.main(["--json", str(out_json)]) == 0
    out = capsys.readouterr().out

    assert "PHASE 4/5 -- GROUNDED QA AND ABSTENTION" in out
    assert "POST-HOC DISCLOSURE" in out
    for arm in ("sparse", "dense", "hybrid", "gated"):
        assert f"ARM {arm}" in out
    assert "retrieval ceiling" in out
    assert "AMBIGUOUS VERDICTS: none" in out
    assert "No number above is comparable to Phase 2's extraction figures" \
        in out

    summary = json.loads(out_json.read_text(encoding="utf-8"))["summary"]
    dense = summary["arms"]["dense"]
    assert dense["gold_in_context"]["grounded_accuracy"]["hits"] == 1
    assert dense["unanswerable"]["abstention"]["hits"] == 1
    sparse = summary["arms"]["sparse"]
    assert sparse["gold_not_in_context"]["abstention"]["hits"] == 1
    assert len(summary["comparisons"]) == 12


def test_scoring_refuses_without_a_freeze(world, monkeypatch, capsys):
    monkeypatch.setattr(run_qa.qa, "ask", _ScriptedAsk())
    assert run_qa.main(["--controls"]) == 0
    assert run_qa.main([]) == 0
    assert score_qa.main([]) == 2
    assert "digest-frozen before any per-arm table" in \
        capsys.readouterr().out


def test_scoring_refuses_a_verdict_edited_after_the_freeze(
        world, monkeypatch, capsys):
    qa_dir = _run_and_adjudicate(world, monkeypatch)
    with open(qa_dir / adjudicate_qa.VERDICTS_NAME, "a",
              encoding="utf-8") as handle:
        handle.write(json.dumps(adj.verdict_record(
            "sneaky", "correct", False)) + "\n")
    assert score_qa.main([]) == 2
    assert "no longer matches its freeze" in capsys.readouterr().out


def test_scoring_refuses_a_drifted_answers_file(world, monkeypatch, capsys):
    qa_dir = _run_and_adjudicate(world, monkeypatch)
    answers = next(qa_dir.glob("answers-*.jsonl"))
    answers.write_text(
        answers.read_text(encoding="utf-8").replace(
            "in the gold chunk", "a different answer"),
        encoding="utf-8")
    assert score_qa.main([]) == 2
    assert "no longer matches its provenance" in capsys.readouterr().out
