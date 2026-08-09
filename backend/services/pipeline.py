"""End-to-end document processing pipeline with per-stage error handling."""

import json
import logging
import os

from pydantic import ValidationError

from models.schemas import StructuredReport
from services.html_parser import extract_text_from_html
from services.openai_structurer import structure_text
from services.pdf_parser import extract_text_from_pdf
from services.supabase_client import mark_report_failed, save_structured_data

logger = logging.getLogger(__name__)

# The upload route validates against this same tuple. Kept here, next to the
# dispatch that consumes it, because two hand-maintained lists drift and the
# symptom is a file accepted at the route and failed in the background.
SUPPORTED_EXTENSIONS = (".pdf", ".htm", ".html")


class PipelineError(Exception):
    """Raised when any pipeline stage fails. `stage` identifies which step."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message


def process_report(report_id: str, filename: str, file_bytes: bytes) -> None:
    """Run the full pipeline for an already-created report row.

    On failure, updates the report row with status=failed and an error_message
    instead of raising. This function is designed to run in a background task
    where the caller has already returned a response.
    """
    try:
        raw_text = _extract(filename, file_bytes)
        structured = _structure(raw_text)
        save_structured_data(report_id, raw_text, structured)
    except PipelineError as e:
        logger.error("Pipeline failed for report %s: %s", report_id, e)
        mark_report_failed(report_id, str(e))
    except Exception as e:
        logger.exception("Unexpected pipeline error for report %s", report_id)
        mark_report_failed(report_id, f"[unexpected] {type(e).__name__}: {e}")


def _extract(filename: str, file_bytes: bytes) -> str:
    """Dispatch to a parser by file extension.

    Extension, not content type: browsers, curl, and the EDGAR archive disagree
    about the MIME type for .htm, and the extension is the one signal that
    survives an upload intact.
    """
    extension = os.path.splitext(filename or "")[1].lower()

    if extension == ".pdf":
        parser = extract_text_from_pdf
    elif extension in (".htm", ".html"):
        parser = extract_text_from_html
    else:
        raise PipelineError(
            "parse",
            f"Unsupported file type {extension or '(none)'!r}. "
            f"Accepted: {', '.join(SUPPORTED_EXTENSIONS)}",
        )

    try:
        text = parser(file_bytes)
    except Exception as e:
        raise PipelineError("parse", f"{type(e).__name__}: {e}") from e

    if not text.strip():
        hint = (
            "Scanned/image-only PDFs need OCR."
            if extension == ".pdf"
            else "The document may be a frameset or an EDGAR index page rather "
            "than the filing itself."
        )
        raise PipelineError("parse", f"No text found in {extension} document. {hint}")
    return text


def _structure(raw_text: str) -> dict:
    try:
        data = structure_text(raw_text)
    except json.JSONDecodeError as e:
        raise PipelineError(
            "openai", f"AI returned invalid JSON: {e.msg}"
        ) from e
    except Exception as e:
        raise PipelineError("openai", f"{type(e).__name__}: {e}") from e

    # Validate shape against the Pydantic schema so DB inserts don't explode later.
    try:
        StructuredReport.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []))
        msg = first.get("msg", "validation failed")
        raise PipelineError(
            "schema_validate", f"{loc}: {msg}" if loc else msg
        ) from e
    return data
