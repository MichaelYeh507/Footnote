"""Route-level tests: status codes and response shapes, Supabase mocked.

These pin the contract the frontend consumes. Route paths changed in migration
002 (/api/companies -> /api/extractions), and a silent 404 on a renamed route
looks identical to an empty database from the UI.
"""

import io

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def app_client():
    return TestClient(main.app)


def test_root_reports_service_identity(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "document-pipeline"}


def test_list_extractions_returns_rows(app_client, monkeypatch):
    monkeypatch.setattr(
        main, "get_all_extractions", lambda: [{"id": "e1", "company_name": "Acme"}]
    )
    response = app_client.get("/api/extractions")
    assert response.status_code == 200
    assert response.json()[0]["company_name"] == "Acme"


def test_extraction_detail_shape(app_client, monkeypatch):
    monkeypatch.setattr(
        main,
        "get_extraction_detail",
        lambda _id: {"extraction": {"id": _id}, "risks": [], "management": []},
    )
    response = app_client.get("/api/extractions/e1")
    assert response.status_code == 200
    assert set(response.json()) == {"extraction", "risks", "management"}


def test_delete_missing_extraction_is_404(app_client, monkeypatch):
    monkeypatch.setattr(main, "delete_extraction", lambda _id: {"deleted": False})
    assert app_client.delete("/api/extractions/nope").status_code == 404


def test_delete_extraction_success(app_client, monkeypatch):
    monkeypatch.setattr(
        main, "delete_extraction", lambda _id: {"deleted": True, "extraction_id": _id}
    )
    response = app_client.delete("/api/extractions/e1")
    assert response.status_code == 200
    assert response.json()["deleted"] is True


def test_old_companies_route_is_gone(app_client):
    """Guards against the frontend silently pointing at a dead path."""
    assert app_client.get("/api/companies").status_code == 404


def test_missing_report_is_404(app_client, monkeypatch):
    monkeypatch.setattr(main, "get_report", lambda _id: None)
    assert app_client.get("/api/reports/nope").status_code == 404


def test_list_reports_passes_limit_through(app_client, monkeypatch):
    seen = {}

    def fake(limit):
        seen["limit"] = limit
        return []

    monkeypatch.setattr(main, "get_recent_reports", fake)
    assert app_client.get("/api/reports?limit=7").status_code == 200
    assert seen["limit"] == 7


def test_upload_rejects_non_pdf(app_client):
    response = app_client.post(
        "/api/upload", files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]


def test_upload_rejects_empty_file(app_client):
    response = app_client.post(
        "/api/upload", files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")}
    )
    assert response.status_code == 400


def test_upload_queues_background_work(app_client, monkeypatch):
    """The route must return immediately with an id to poll, not block on the
    OpenAI call."""
    calls = []
    monkeypatch.setattr(main, "create_report", lambda name: {"id": "report-1"})
    monkeypatch.setattr(main, "process_report", lambda *a: calls.append(a))

    response = app_client.post(
        "/api/upload",
        files={"file": ("filing.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "processing", "report_id": "report-1"}
    assert len(calls) == 1
