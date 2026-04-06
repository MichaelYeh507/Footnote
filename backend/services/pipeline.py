"""End-to-end PDF processing pipeline with per-stage error handling."""

import json
import logging

from pydantic import ValidationError

from models.schemas import StructuredReport
from services.openai_structurer import structure_text
from services.pdf_parser import extract_text_from_pdf
from services.supabase_client import mark_report_failed, save_structured_data

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """Raised when any pipeline stage fails. `stage` identifies which step."""

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.message = message


def process_report(report_id: str, file_bytes: bytes) -> None:
    """Run the full pipeline for an already-created report row.

    On failure, updates the report row with status=failed and an error_message
    instead of raising. This function is designed to run in a background task
    where the caller has already returned a response.
    """
    try:
        raw_text = _extract(file_bytes)
        structured = _structure(raw_text)
        save_structured_data(report_id, raw_text, structured)
    except PipelineError as e:
        logger.error("Pipeline failed for report %s: %s", report_id, e)
        mark_report_failed(report_id, str(e))
    except Exception as e:
        logger.exception("Unexpected pipeline error for report %s", report_id)
        mark_report_failed(report_id, f"[unexpected] {type(e).__name__}: {e}")


def _extract(file_bytes: bytes) -> str:
    try:
        text = extract_text_from_pdf(file_bytes)
    except Exception as e:
        raise PipelineError("pdf_parse", f"{type(e).__name__}: {e}") from e
    if not text.strip():
        raise PipelineError(
            "pdf_parse",
            "No text found in PDF. Scanned/image-only PDFs need OCR.",
        )
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
