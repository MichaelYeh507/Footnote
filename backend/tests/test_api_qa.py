"""Route-level tests for POST /api/qa, everything live mocked.

Same posture as test_api_routes.py: these pin the contract the frontend
consumes. The orchestration itself is tested in test_qa_demo.py; here the
questions are the HTTP ones -- status codes, refusal messages reaching the
client, and which dependencies are (not) touched per arm.
"""

import pytest
from fastapi.testclient import TestClient

import database
import main
from services import qa_demo, retrieval


@pytest.fixture
def app_client():
    return TestClient(main.app, raise_server_exceptions=False)


class FakeCursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return FakeCursor()


@pytest.fixture
def live_mocked(monkeypatch):
    """A configured database, a connectable server, a buildable client."""
    monkeypatch.setattr(database, "url", lambda: "postgresql://u:p@h:5432/db")
    monkeypatch.setattr(main.psycopg, "connect",
                        lambda *a, **kw: FakeConnection())
    monkeypatch.setattr(retrieval, "embedding_client", lambda: object())


def test_qa_passes_through_the_orchestration_record(app_client, live_mocked,
                                                    monkeypatch):
    seen = {}

    def fake_answer(question, arm, *, cursor, client, taus=None, ask=None):
        seen.update(question=question, arm=arm, taus=taus)
        return {"state": "answered", "answer": "x", "arm": arm}

    monkeypatch.setattr(qa_demo, "answer_question", fake_answer)
    response = app_client.post(
        "/api/qa", json={"question": "What were net revenues?",
                         "arm": "dense"})
    assert response.status_code == 200
    assert response.json()["state"] == "answered"
    assert seen["question"] == "What were net revenues?"
    assert seen["arm"] == "dense"
    assert seen["taus"] is None  # only the gated arm loads thresholds


def test_qa_gated_loads_the_threshold_artifact(app_client, live_mocked,
                                               monkeypatch):
    monkeypatch.setattr(qa_demo, "load_taus", lambda: {7: 3.8})
    seen = {}

    def fake_answer(question, arm, *, cursor, client, taus=None, ask=None):
        seen.update(taus=taus)
        return {"state": "abstained", "arm": arm}

    monkeypatch.setattr(qa_demo, "answer_question", fake_answer)
    response = app_client.post(
        "/api/qa", json={"question": "q", "arm": "gated"})
    assert response.status_code == 200
    assert seen["taus"] == {7: 3.8}


def test_qa_unconfigured_database_is_503_with_guidance(app_client,
                                                       monkeypatch):
    def raise_unset():
        raise RuntimeError("DATABASE_URL is not set. ...")

    monkeypatch.setattr(database, "url", raise_unset)
    response = app_client.post(
        "/api/qa", json={"question": "q", "arm": "dense"})
    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"]


def test_qa_missing_threshold_artifact_is_503(app_client, live_mocked,
                                              monkeypatch):
    def raise_missing():
        raise FileNotFoundError("no gate-threshold-*.json in <dir>")

    monkeypatch.setattr(qa_demo, "load_taus", raise_missing)
    response = app_client.post(
        "/api/qa", json={"question": "q", "arm": "gated"})
    assert response.status_code == 503
    assert "gate-threshold" in response.json()["detail"]


def test_qa_validation_errors_are_422_with_the_message(app_client,
                                                       live_mocked,
                                                       monkeypatch):
    def raise_value(question, arm, **kwargs):
        raise ValueError("question is empty")

    monkeypatch.setattr(qa_demo, "answer_question", raise_value)
    response = app_client.post(
        "/api/qa", json={"question": "", "arm": "dense"})
    assert response.status_code == 422
    assert "empty" in response.json()["detail"]


def test_qa_gated_unmeasured_size_is_422_with_the_refusal(app_client,
                                                          live_mocked,
                                                          monkeypatch):
    monkeypatch.setattr(qa_demo, "load_taus", lambda: {7: 3.8})

    def raise_lookup(question, arm, **kwargs):
        raise LookupError("tau(L) was measured for [7] and this question "
                          "has 2")

    monkeypatch.setattr(qa_demo, "answer_question", raise_lookup)
    response = app_client.post(
        "/api/qa", json={"question": "Amcor CEO?", "arm": "gated"})
    assert response.status_code == 422
    assert "tau" in response.json()["detail"]


def test_qa_database_refused_connection_is_503(app_client, monkeypatch):
    monkeypatch.setattr(database, "url", lambda: "postgresql://u:p@h:5432/db")

    def refuse(*a, **kw):
        raise main.psycopg.OperationalError("connection refused")

    monkeypatch.setattr(main.psycopg, "connect", refuse)
    response = app_client.post(
        "/api/qa", json={"question": "q", "arm": "dense"})
    assert response.status_code == 503


def test_qa_sparse_arm_never_builds_an_embedding_client(app_client,
                                                        monkeypatch):
    monkeypatch.setattr(database, "url", lambda: "postgresql://u:p@h:5432/db")
    monkeypatch.setattr(main.psycopg, "connect",
                        lambda *a, **kw: FakeConnection())

    def explode():
        raise AssertionError("sparse must not require OPENAI_API_KEY")

    monkeypatch.setattr(retrieval, "embedding_client", explode)
    seen = {}

    def fake_answer(question, arm, *, cursor, client, taus=None, ask=None):
        seen.update(client=client)
        return {"state": "abstained", "arm": arm}

    monkeypatch.setattr(qa_demo, "answer_question", fake_answer)
    response = app_client.post(
        "/api/qa", json={"question": "goodwill impairment", "arm": "sparse"})
    assert response.status_code == 200
    assert seen["client"] is None


def test_qa_missing_body_fields_are_422(app_client):
    assert app_client.post("/api/qa", json={}).status_code == 422
    assert app_client.post(
        "/api/qa", json={"question": "q"}).status_code == 422


def test_qa_attaches_presentation_outside_the_orchestration(app_client,
                                                            live_mocked,
                                                            monkeypatch):
    monkeypatch.setattr(
        qa_demo, "answer_question",
        lambda *a, **kw: {"state": "answered", "answer": "x", "arm": "dense"})
    seen = {}

    def fake_compose(record):
        seen["record"] = record
        return "Prose over the verified answer [1]."

    monkeypatch.setattr(qa_demo, "compose_paragraph", fake_compose)
    response = app_client.post(
        "/api/qa", json={"question": "q", "arm": "dense"})
    assert response.status_code == 200
    assert response.json()["presentation"] == (
        "Prose over the verified answer [1].")
    assert seen["record"]["answer"] == "x"  # composed from the full record


def test_qa_declined_presentation_is_null_not_an_error(app_client,
                                                       live_mocked,
                                                       monkeypatch):
    monkeypatch.setattr(
        qa_demo, "answer_question",
        lambda *a, **kw: {"state": "answered", "answer": "x", "arm": "dense"})
    monkeypatch.setattr(qa_demo, "compose_paragraph", lambda record: None)
    response = app_client.post(
        "/api/qa", json={"question": "q", "arm": "dense"})
    assert response.status_code == 200
    assert response.json()["presentation"] is None
