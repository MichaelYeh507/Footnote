"""Tests for pipeline stage dispatch and failure handling.

The pipeline swallows every exception and writes status=failed to the database,
by design, because it runs in a background task after the response has gone out.
That means a totally broken pipeline renders in the UI as an ordinary failed row.
These tests are the only place that distinction is visible.
"""

import pytest

from services import pipeline
from services.pipeline import PipelineError


@pytest.fixture
def spy(monkeypatch):
    """Replace both parsers with recorders so dispatch is observable."""
    calls = []
    monkeypatch.setattr(
        pipeline, "extract_text_from_pdf",
        lambda b: calls.append(("pdf", b)) or "pdf text",
    )
    monkeypatch.setattr(
        pipeline, "extract_text_from_html",
        lambda b: calls.append(("html", b)) or "html text",
    )
    return calls


@pytest.mark.parametrize("filename", ["filing.htm", "filing.html", "AAPL-20250927.HTM"])
def test_html_filenames_dispatch_to_html_parser(spy, filename):
    assert pipeline._extract(filename, b"<p>x</p>") == "html text"
    assert spy == [("html", b"<p>x</p>")]


@pytest.mark.parametrize("filename", ["filing.pdf", "FILING.PDF"])
def test_pdf_filenames_dispatch_to_pdf_parser(spy, filename):
    """PDF support stays: the retained synthetic corpus is the clean-vs-real control."""
    assert pipeline._extract(filename, b"%PDF-1.4") == "pdf text"
    assert spy == [("pdf", b"%PDF-1.4")]


def test_unsupported_extension_is_a_named_stage_failure(spy):
    with pytest.raises(PipelineError) as exc:
        pipeline._extract("notes.txt", b"hello")
    assert exc.value.stage == "parse"
    assert not spy, "no parser should have been called"


def test_no_extension_is_rejected_not_guessed(spy):
    with pytest.raises(PipelineError):
        pipeline._extract("filing", b"hello")
    assert not spy


def test_empty_extracted_text_fails_before_the_model(monkeypatch):
    """An empty prompt to OpenAI costs money and returns confident nonsense."""
    monkeypatch.setattr(pipeline, "extract_text_from_html", lambda b: "   ")
    with pytest.raises(PipelineError) as exc:
        pipeline._extract("filing.htm", b"<html></html>")
    assert exc.value.stage == "parse"


def test_parser_exception_is_wrapped_not_leaked(monkeypatch):
    def boom(_):
        raise ValueError("bad gzip")

    monkeypatch.setattr(pipeline, "extract_text_from_html", boom)
    with pytest.raises(PipelineError) as exc:
        pipeline._extract("filing.htm", b"junk")
    assert exc.value.stage == "parse"
    assert "ValueError" in str(exc.value)


def test_process_report_records_failure_instead_of_raising(monkeypatch):
    """The contract the UI depends on: background failures become a failed row."""
    recorded = {}
    monkeypatch.setattr(
        pipeline, "mark_report_failed",
        lambda rid, msg: recorded.update(report_id=rid, message=msg),
    )
    monkeypatch.setattr(pipeline, "save_structured_data", lambda *a: None)

    pipeline.process_report("r1", "notes.txt", b"hello")

    assert recorded["report_id"] == "r1"
    assert "parse" in recorded["message"]


def test_supported_extensions_is_declared_not_scattered():
    """main.py validates uploads against the same set the pipeline dispatches on.
    Two hand-maintained lists drift, and the symptom is a file accepted at the
    route and failed in the background."""
    assert {".pdf", ".htm", ".html"} == set(pipeline.SUPPORTED_EXTENSIONS)
