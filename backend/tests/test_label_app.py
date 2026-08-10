"""Contracts for the local labeling web app.

Three things under test. The isolation guarantee, carried over from the CLI --
the app must be structurally unable to read model output, and it is a bigger
surface than the CLI was, so the guard matters more. Sanitization, because the
app renders third-party HTML fetched from EDGAR and a filing that could run
script in the labeling page could drive the labeling API. And highlight
injection, because it edits the filing's markup: a highlighter that writes
inside a tag corrupts the document the labeler is reading from, which would
produce wrong labels rather than an obvious crash.
"""

import builtins
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from evaluation.label_view import (
    FIELD_GUIDANCE,
    highlight,
    sanitize_filing_html,
)

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "corpus"
SERVER = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "label_server.py"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import scripts.label_server as server
    monkeypatch.setattr(server, "LABELS", tmp_path / "labels.jsonl")
    return TestClient(server.app)


# --- sanitization ----------------------------------------------------------

@pytest.mark.parametrize("payload", [
    b"<html><body><script>fetch('/api/label')</script><p>text</p></body></html>",
    b"<html><body><iframe src='http://evil'></iframe><p>text</p></body></html>",
    b"<html><body><object data='x'></object><p>text</p></body></html>",
])
def test_active_content_is_stripped(payload):
    cleaned = sanitize_filing_html(payload)
    for tag in ("<script", "<iframe", "<object", "<embed"):
        assert tag not in cleaned.lower()
    assert "text" in cleaned


def test_inline_event_handlers_are_stripped():
    cleaned = sanitize_filing_html(
        b"<div onclick=\"fetch('/api/label')\" onmouseover='x()'>Total assets</div>")
    assert "onclick" not in cleaned.lower()
    assert "onmouseover" not in cleaned.lower()
    assert "Total assets" in cleaned


def test_javascript_urls_are_stripped():
    cleaned = sanitize_filing_html(b"<a href='javascript:fetch(1)'>link</a>")
    assert "javascript:" not in cleaned.lower()


def test_images_become_a_visible_marker_not_a_broken_icon():
    """Only the primary document is fetched, so every image 404s. Dropping them
    silently would hide from the labeler that anything was there."""
    cleaned = sanitize_filing_html(b"<p>before<img src='logo_g1.jpg' alt='logo'>after</p>")
    assert "<img" not in cleaned.lower()
    assert "image omitted" in cleaned
    assert "before" in cleaned and "after" in cleaned


def test_tables_and_text_survive_sanitization():
    """The whole point of the app is that tables render. Stripping them would
    be worse than the terminal it replaces."""
    cleaned = sanitize_filing_html(
        b"<table><tr><td>Total assets</td><td>16,332</td></tr></table>")
    assert "<table" in cleaned.lower() and "<td" in cleaned.lower()
    assert "16,332" in cleaned


# --- highlighting ----------------------------------------------------------

def test_highlight_wraps_matches_in_text():
    html, count = highlight("<p>The Total assets line</p>", "total_assets")
    assert count >= 1
    assert "<mark" in html and "Total assets" in html


def test_highlight_ids_are_sequential_for_navigation():
    html, count = highlight(
        "<p>Total assets here</p><p>and Total assets again</p>", "total_assets")
    assert count >= 2
    assert 'id="hit-0"' in html and 'id="hit-1"' in html


def test_highlight_never_writes_inside_a_tag():
    """The load-bearing one. A match inside an attribute value would corrupt
    the markup and silently change the document the labeler reads."""
    html, _ = highlight(
        '<div title="Total assets summary"><p>Total assets</p></div>',
        "total_assets")
    assert 'title="Total assets summary"' in html, "attribute was rewritten"
    assert "<mark" in html


def test_highlight_does_not_match_inside_script_or_style_text():
    html, _ = highlight("<style>.total-assets{color:red}</style><p>Total assets</p>",
                        "total_assets")
    assert html.count("<mark") == 1


def test_highlight_with_no_match_returns_document_unchanged():
    source = "<p>nothing relevant</p>"
    html, count = highlight(source, "goodwill_impairment")
    assert count == 0 and html == source


def test_every_queue_field_has_labeler_guidance():
    """Guidance is what moved this out of chat and onto the screen. A field
    without it sends the labeler back to asking."""
    from evaluation.labeling import QUEUE_FIELDS
    for field in QUEUE_FIELDS:
        assert FIELD_GUIDANCE.get(field, "").strip(), f"no guidance for {field}"


# --- API -------------------------------------------------------------------

def test_queue_endpoint_reports_progress(client):
    body = client.get("/api/queue").json()
    assert body["total"] == 351
    assert body["labeled"] == 0
    assert body["item"]["ticker"] == "AMCR"
    assert body["item"]["field"] == "company_name"


def test_posting_a_valid_label_persists_it(client, tmp_path):
    response = client.post("/api/label", json={
        "accession": "0001748790-24-000022", "ticker": "AMCR",
        "period": "2024-06-30", "field": "company_name",
        "answer_kind": "value", "value": "Amcor plc",
        "locator": {"section": "cover page", "anchor": "Exact name of registrant"},
        "ambiguous": False, "note": "",
    })
    assert response.status_code == 200
    written = (tmp_path / "labels.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(written)["value"] == "Amcor plc"
    assert client.get("/api/queue").json()["labeled"] == 1


def test_a_label_violating_the_protocol_is_rejected_and_not_written(client, tmp_path):
    """Rule 1 enforced at the API, not only in the browser. A UI check that the
    server does not repeat is a UI check that can be bypassed."""
    response = client.post("/api/label", json={
        "accession": "A", "ticker": "T", "period": "P", "field": "total_assets",
        "answer_kind": "value", "value": 1.0,
        "locator": {"section": "", "anchor": ""},
    })
    assert response.status_code == 400
    assert "anchor" in response.json()["detail"]
    assert not (tmp_path / "labels.jsonl").exists()


def test_not_addressed_without_search_terms_is_rejected(client):
    response = client.post("/api/label", json={
        "accession": "A", "ticker": "T", "period": "P",
        "field": "goodwill_impairment", "answer_kind": "not_addressed",
        "locator": {"searched": []},
    })
    assert response.status_code == 400
    assert "searched" in response.json()["detail"]


@pytest.mark.parametrize("accession", [
    "not-a-real-accession",
    "..%2f..%2f.env",
    "../../.env",
    "....//....//main.py",
])
def test_unknown_accession_is_rejected_by_the_manifest_not_the_filesystem(
        client, monkeypatch, accession):
    """Asserting "returns 404" is not enough, and a perturbation proved it.

    With the manifest lookup removed, an unknown accession still 404s -- the
    constructed path just does not exist -- so a status-only assertion passed
    against a server that builds file paths straight from the URL. The real
    property is that an unrecognised accession never reaches the filesystem,
    so that is what is checked: no read is attempted, and the refusal comes
    from the manifest.
    """
    probes = []
    real_exists = pathlib.Path.exists
    real_read_bytes = pathlib.Path.read_bytes

    def watch(fn):
        def wrapper(self, *a, **k):
            probes.append(str(self))
            return fn(self, *a, **k)
        return wrapper

    # exists() is the probe that actually happens -- the handler checks it
    # before reading. Instrumenting only read_bytes would miss the perturbation.
    monkeypatch.setattr(pathlib.Path, "exists", watch(real_exists))
    monkeypatch.setattr(pathlib.Path, "read_bytes", watch(real_read_bytes))

    response = client.get(f"/api/filing/{accession}")

    assert response.status_code == 404
    # Accessions containing slashes never match the route at all, which is a
    # stronger rejection than the manifest lookup; both are acceptable.
    assert response.json()["detail"] in ("unknown accession", "Not Found")
    in_corpus = [p for p in probes if "filings" in p.replace("\\", "/")]
    assert not in_corpus, f"unknown accession probed the corpus: {in_corpus}"


# --- isolation -------------------------------------------------------------

FORBIDDEN = ("openai", "evaluation.extraction_run", "services.openai_structurer",
             "services.supabase_client")


def test_server_imports_nothing_that_can_reach_model_output():
    import ast
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for forbidden in FORBIDDEN:
        offenders = [n for n in imported
                     if n == forbidden or n.startswith(forbidden + ".")]
        assert not offenders, f"label server imports {offenders}"


def test_no_string_in_the_server_names_the_predictions_file():
    import ast
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstrings]
    assert not [s for s in literals if "prediction" in s.lower()]


def test_a_full_labeling_session_never_opens_the_predictions_file(client, monkeypatch):
    """Behavioural guard across the real request path -- queue, filing render,
    label write. Source inspection cannot prove this."""
    opened = []
    real_open = builtins.open
    real_read_bytes = pathlib.Path.read_bytes
    real_read_text = pathlib.Path.read_text

    def watch(fn):
        def wrapper(target, *args, **kwargs):
            opened.append(str(target))
            return fn(target, *args, **kwargs)
        return wrapper

    monkeypatch.setattr(builtins, "open", watch(real_open))
    monkeypatch.setattr(pathlib.Path, "read_bytes", watch(real_read_bytes))
    monkeypatch.setattr(pathlib.Path, "read_text", watch(real_read_text))

    client.get("/")
    body = client.get("/api/queue").json()
    client.get(f"/api/filing/{body['item']['accession']}?field=company_name")
    client.post("/api/label", json={
        "accession": body["item"]["accession"], "ticker": "AMCR",
        "period": "2024-06-30", "field": "company_name",
        "answer_kind": "value", "value": "Amcor plc",
        "locator": {"section": "cover", "anchor": "Exact name of registrant"},
    })

    leaked = [p for p in opened if "prediction" in p.lower()]
    assert not leaked, f"labeling session opened model output: {leaked}"
