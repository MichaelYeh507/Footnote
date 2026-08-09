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


@pytest.fixture
def queued(monkeypatch):
    """Capture what the route hands to the background task."""
    calls = []
    monkeypatch.setattr(main, "create_report", lambda name: {"id": "report-1"})
    monkeypatch.setattr(main, "process_report", lambda *a: calls.append(a))
    return calls


def test_upload_rejects_unsupported_format(app_client):
    response = app_client.post(
        "/api/upload", files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert ".htm" in detail and ".pdf" in detail, "the error must name what IS accepted"


@pytest.mark.parametrize(
    "filename,payload,content_type",
    [
        ("aapl-20250927.htm", b"<html><p>Item 1.</p></html>", "text/html"),
        ("filing.html", b"<html><p>Item 1.</p></html>", "text/html"),
        ("filing.pdf", b"%PDF-1.4 fake", "application/pdf"),
    ],
)
def test_upload_accepts_supported_formats(app_client, queued, filename, payload, content_type):
    """EDGAR serves 10-Ks as HTML; PDF stays for the retained synthetic corpus."""
    response = app_client.post(
        "/api/upload", files={"file": (filename, io.BytesIO(payload), content_type)}
    )
    assert response.status_code == 200
    assert len(queued) == 1


def test_upload_trusts_the_extension_not_the_content_type(app_client, queued):
    """Browsers and curl disagree on the MIME type for .htm; the extension is
    what the pipeline dispatches on, so it must be what the route validates."""
    response = app_client.post(
        "/api/upload",
        files={"file": ("filing.htm", io.BytesIO(b"<p>x</p>"), "application/octet-stream")},
    )
    assert response.status_code == 200


def test_upload_rejects_empty_file(app_client):
    response = app_client.post(
        "/api/upload", files={"file": ("empty.htm", io.BytesIO(b""), "text/html")}
    )
    assert response.status_code == 400


def test_upload_queues_background_work(app_client, queued):
    """The route must return immediately with an id to poll, not block on the
    OpenAI call."""
    response = app_client.post(
        "/api/upload",
        files={"file": ("filing.htm", io.BytesIO(b"<p>x</p>"), "text/html")},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "processing", "report_id": "report-1"}
    assert len(queued) == 1


def test_upload_passes_filename_to_the_pipeline(app_client, queued):
    """Dispatch happens by extension inside the pipeline, so the filename has to
    survive the handoff. Losing it silently routes every upload to one parser."""
    app_client.post(
        "/api/upload",
        files={"file": ("aapl-20250927.htm", io.BytesIO(b"<p>x</p>"), "text/html")},
    )
    assert "aapl-20250927.htm" in queued[0]
