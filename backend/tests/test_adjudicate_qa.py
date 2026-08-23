"""The adjudication server is blind, and provably so, three ways.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
appendix requires the interface to display the question, the gold span(s)
and the answer text and nothing else, enforced by construction. Following
the labeling app's precedent, that is checked on three axes:

  **The import graph.** The server must not import the modules that know
  about arms, contexts, retrieval or scoring; a blind tool that imports the
  unblinded world is one refactor from showing it.

  **The source's string literals.** No literal in the server names a hidden
  field or an arm, so a rendering path for one cannot be added quietly.

  **The wire.** A full request cycle -- state, verdict, state -- is walked
  and every response body is searched for planted markers of the hidden
  fields. Source inspection cannot prove what the wire carries; this does.
"""

import ast
import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from scripts import adjudicate_qa  # noqa: E402

SERVER = BACKEND / "scripts" / "adjudicate_qa.py"

# Markers planted in the answers fixture for fields the adjudicator must
# never see. Distinctive on purpose: a substring search must not false-match.
HIDDEN_QUOTE = "ZQUOTEMARKERZ"
HIDDEN_EXCERPT_ID = "ZEXCERPTIDZ"
HIDDEN_ARM = "sparse"
HIDDEN_CALL = "ZCALLIDZ"


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "filings").mkdir(parents=True)
    (data / "qa").mkdir()
    monkeypatch.setenv("RAG_FILINGS_DIR", str(data / "filings"))

    queries = [
        {"query_id": "q001", "stratum": "exact_entity",
         "query": "What does the filing say?",
         "gold": [{"accession": "acc-A", "span": "the gold span"}]},
        {"query_id": "q002", "stratum": "exact_entity",
         "query": "And this one?",
         "gold": [{"accession": "acc-B", "span": "another gold span"}]},
    ]
    monkeypatch.setattr(adjudicate_qa.review, "read_queries",
                        lambda: queries)

    lines = [
        {"call_id": HIDDEN_CALL, "query_id": "q001",
         "excerpt_ids": [HIDDEN_EXCERPT_ID], "arms": [HIDDEN_ARM],
         "raw": json.dumps({"answer": "the model answer", "citation": 1,
                            "quote": HIDDEN_QUOTE})},
        {"call_id": HIDDEN_CALL + "2", "query_id": "q002",
         "excerpt_ids": [HIDDEN_EXCERPT_ID], "arms": ["dense"],
         "raw": json.dumps({"answer": "a second answer", "citation": 1,
                            "quote": HIDDEN_QUOTE})},
    ]
    answers = data / "qa" / "answers-20990101-000000.jsonl"
    answers.write_text("".join(json.dumps(line) + "\n" for line in lines),
                       encoding="utf-8")
    # Existence marker only; the server never opens it, so bogus content
    # would fail loudly if it ever did.
    (data / "qa" / "qa-provenance-20990101-000000.json").write_text(
        "not json on purpose", encoding="utf-8")
    return data


@pytest.fixture
def client(world):
    queue = adjudicate_qa.build_queue()
    app = adjudicate_qa.build_app(
        queue,
        world / "qa" / adjudicate_qa.VERDICTS_NAME,
        world / "qa" / adjudicate_qa.FREEZE_NAME)
    return TestClient(app)


def test_completed_runs_are_found_by_existence_only(world):
    files = adjudicate_qa.completed_answer_files()
    assert [f.name for f in files] == ["answers-20990101-000000.jsonl"]
    # An answers file without its run record is not adjudicable.
    orphan = world / "qa" / "answers-20990102-000000.jsonl"
    orphan.write_text("", encoding="utf-8")
    assert [f.name for f in adjudicate_qa.completed_answer_files()] == [
        "answers-20990101-000000.jsonl"]


def test_build_queue_refuses_without_a_completed_run(world):
    for path in (world / "qa").iterdir():
        path.unlink()
    with pytest.raises(FileNotFoundError, match="no completed run"):
        adjudicate_qa.build_queue()


def test_state_serves_a_stripped_item(client):
    state = client.get("/api/state").json()
    assert state["total"] == 2 and state["done"] == 0
    assert set(state["next"]) == {"key", "query_id", "question",
                                  "gold_spans", "answer"}


def test_a_full_cycle_never_puts_a_hidden_field_on_the_wire(client):
    bodies = []
    state = client.get("/api/state")
    bodies.append(state.text)
    bodies.append(client.get("/").text)
    while state.json()["next"] is not None:
        item = state.json()["next"]
        state = client.post("/api/verdict", json={
            "key": item["key"], "verdict": "correct", "ambiguous": False})
        bodies.append(state.text)
    everything = "\n".join(bodies)
    for marker in (HIDDEN_QUOTE, HIDDEN_EXCERPT_ID, HIDDEN_ARM, HIDDEN_CALL,
                   "citation"):
        assert marker not in everything, marker


def test_verdicts_append_and_advance(client, world):
    item = client.get("/api/state").json()["next"]
    after = client.post("/api/verdict", json={
        "key": item["key"], "verdict": "incorrect", "ambiguous": True,
        "note": "close but the year is wrong"}).json()
    assert after["done"] == 1
    assert after["next"]["key"] != item["key"]
    verdicts = (world / "qa" / adjudicate_qa.VERDICTS_NAME).read_text(
        encoding="utf-8").splitlines()
    record = json.loads(verdicts[0])
    assert record["verdict"] == "incorrect" and record["ambiguous"] is True


def test_bad_verdicts_are_refused(client):
    item = client.get("/api/state").json()["next"]
    assert client.post("/api/verdict", json={
        "key": item["key"], "verdict": "maybe",
        "ambiguous": False}).status_code == 422
    assert client.post("/api/verdict", json={
        "key": "not-a-key", "verdict": "correct",
        "ambiguous": False}).status_code == 422


def test_undo_retracts_the_last_verdict_and_serves_it_again(client, world):
    first = client.get("/api/state").json()["next"]
    after = client.post("/api/verdict", json={
        "key": first["key"], "verdict": "incorrect",
        "ambiguous": False}).json()
    assert after["done"] == 1 and after["next"]["key"] != first["key"]
    undone = client.post("/api/undo").json()
    assert undone["done"] == 0
    assert undone["next"]["key"] == first["key"]
    lines = (world / "qa" / adjudicate_qa.VERDICTS_NAME).read_text(
        encoding="utf-8").splitlines()
    assert len(lines) == 2  # the click and its retraction, both kept
    assert json.loads(lines[-1])["verdict"] == "retracted"


def test_undo_with_nothing_to_undo_is_refused(client):
    assert client.post("/api/undo").status_code == 422


def test_undo_after_freeze_is_refused(client, world):
    item = client.get("/api/state").json()["next"]
    client.post("/api/verdict", json={
        "key": item["key"], "verdict": "correct", "ambiguous": False})
    (world / "qa" / adjudicate_qa.FREEZE_NAME).write_text(
        "{}", encoding="utf-8")
    response = client.post("/api/undo")
    assert response.status_code == 409
    assert "frozen" in response.text


def test_a_frozen_file_refuses_new_verdicts(client, world):
    (world / "qa" / adjudicate_qa.FREEZE_NAME).write_text(
        "{}", encoding="utf-8")
    item = client.get("/api/state").json()["next"]
    response = client.post("/api/verdict", json={
        "key": item["key"], "verdict": "correct", "ambiguous": False})
    assert response.status_code == 409
    assert "frozen" in response.text


def test_the_server_imports_none_of_the_unblinded_world():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            imported.add(module)
            imported.update(f"{module}.{alias.name}"
                            for alias in node.names)
    forbidden = {"evaluation.qa_contexts", "evaluation.qa_outcomes",
                 "evaluation.retrieval_scoring", "evaluation.retrieval_gold",
                 "services.qa", "services.chunk_store", "services.retrieval",
                 "scripts.run_qa", "scripts.score_retrieval"}
    offenders = {name for name in imported
                 if any(name == f or name.startswith(f + ".")
                        for f in forbidden)}
    assert not offenders, f"the adjudication server imports {offenders}"


def test_no_string_in_the_server_names_a_hidden_field():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    literals = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)]
    forbidden = ("citation", "excerpt", "chunk", "sparse", "dense",
                 "hybrid", "gated", "ranking", "retrieval")
    offenders = [s[:60] for s in literals
                 if any(f in s.lower() for f in forbidden)]
    assert not offenders, offenders
