"""Tests for the persistence layer, with Supabase mocked.

The failure these exist to catch: writes to dropped tables or renamed columns do
not surface loudly. process_report() catches the exception and calls
mark_report_failed(), so a completely broken pipeline looks like a "failed" row
in the UI rather than a crash. Nothing in the suite noticed migration 002 until
these tests existed.
"""

from unittest.mock import MagicMock

import pytest

import services.supabase_client as client


class FakeTable:
    """Records every call so assertions can inspect what would hit Postgres."""

    def __init__(self, recorder, name):
        self._rec = recorder
        self._name = name

    def insert(self, payload):
        self._rec.inserts.append((self._name, payload))
        self._result = [{"id": f"{self._name}-id"}]
        return self

    def update(self, payload):
        self._rec.updates.append((self._name, payload))
        self._result = []
        return self

    def delete(self):
        self._rec.deletes.append(self._name)
        self._result = []
        return self

    def select(self, *_args, **_kwargs):
        self._result = self._rec.select_results.get(self._name, [])
        return self

    def eq(self, *_args):
        return self

    def in_(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return MagicMock(data=self._result)


class Recorder:
    def __init__(self, select_results=None):
        self.inserts = []
        self.updates = []
        self.deletes = []
        self.select_results = select_results or {}

    def table(self, name):
        return FakeTable(self, name)

    def tables_written(self):
        return {name for name, _ in self.inserts} | {name for name, _ in self.updates}


@pytest.fixture
def recorder(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(client, "supabase", rec)
    return rec


LIVE_TABLES = {"reports", "extractions", "risks", "management"}

FULL_EXTRACTION = {
    "company_name": "Acme Corp",
    "ticker": "ACME",
    "fiscal_year_end": "December 31, 2024",
    "employees": "approximately 12,000",
    "total_assets": 8421.0,
    "revenue_most_recent_fy": 5310.2,
    "ceo_name": "Jane Doe",
    "dividends_declared_per_share": None,
    "goodwill_impairment": 0.0,
    "sector": "Industrials",
    "headquarters": "Cleveland, OH",
    "description": "A manufacturer.",
    "founded": "1954",
    "report_type": "10-K",
    "risks": [{"risk_name": "Supply chain", "description": "d", "mitigation": "m"}],
    "management": [{"name": "Jane Doe", "title": "CEO", "tenure": "5y", "background": "b"}],
}


def test_writes_only_to_tables_that_exist(recorder):
    client.save_structured_data("report-1", "raw text", FULL_EXTRACTION)
    assert recorder.tables_written() <= LIVE_TABLES, (
        f"wrote to non-existent tables: {sorted(recorder.tables_written() - LIVE_TABLES)}"
    )


def test_extraction_insert_uses_current_column_names(recorder):
    client.save_structured_data("report-1", "raw text", FULL_EXTRACTION)
    payload = next(p for name, p in recorder.inserts if name == "extractions")

    assert payload["company_name"] == "Acme Corp"
    assert payload["ceo_name"] == "Jane Doe"
    assert payload["report_id"] == "report-1"
    # Renamed or dropped in migration 002.
    for gone in ("name", "ceo", "rating", "price_target", "current_price"):
        assert gone not in payload


def test_unknown_keys_are_dropped_before_insert(recorder):
    """A model that returns an extra key must not produce an unknown-column
    error that gets swallowed into a failed row."""
    client.save_structured_data(
        "report-1", "raw", {**FULL_EXTRACTION, "price_target": 99, "made_up": "x"}
    )
    payload = next(p for name, p in recorder.inserts if name == "extractions")
    assert "price_target" not in payload
    assert "made_up" not in payload


def test_explicit_null_is_preserved_but_missing_key_is_omitted(recorder):
    """null means the model asserted absence — it must reach the database.
    A key the model never returned should be left to the column default."""
    data = {k: v for k, v in FULL_EXTRACTION.items() if k != "goodwill_impairment"}
    client.save_structured_data("report-1", "raw", data)
    payload = next(p for name, p in recorder.inserts if name == "extractions")

    assert "dividends_declared_per_share" in payload
    assert payload["dividends_declared_per_share"] is None
    assert "goodwill_impairment" not in payload


def test_children_are_linked_by_extraction_id(recorder):
    client.save_structured_data("report-1", "raw", FULL_EXTRACTION)
    for table in ("risks", "management"):
        payload = next(p for name, p in recorder.inserts if name == table)
        assert payload["extraction_id"] == "extractions-id"
        assert "company_id" not in payload


def test_risk_inference_fields_are_never_written(recorder):
    """likelihood/impact were removed as ungrounded model inference."""
    data = {
        **FULL_EXTRACTION,
        "risks": [{"risk_name": "R", "likelihood": "High", "impact": "Very High"}],
    }
    client.save_structured_data("report-1", "raw", data)
    payload = next(p for name, p in recorder.inserts if name == "risks")
    assert "likelihood" not in payload
    assert "impact" not in payload


def test_missing_child_lists_do_not_crash(recorder):
    client.save_structured_data("report-1", "raw", {"company_name": "Acme"})
    assert not [p for name, p in recorder.inserts if name in ("risks", "management")]


def test_null_child_lists_do_not_crash(recorder):
    """The model may return an explicit null instead of omitting the key."""
    client.save_structured_data(
        "report-1", "raw", {"company_name": "Acme", "risks": None, "management": None}
    )
    assert not [p for name, p in recorder.inserts if name in ("risks", "management")]


def test_report_row_records_raw_text_and_completion(recorder):
    client.save_structured_data("report-1", "raw text here", FULL_EXTRACTION)
    payload = next(p for name, p in recorder.updates if name == "reports")
    assert payload["raw_text"] == "raw text here"
    assert payload["status"] == "completed"
    assert payload["structured_json"] == FULL_EXTRACTION


def test_delete_extraction_reports_not_found(monkeypatch):
    monkeypatch.setattr(client, "supabase", Recorder(select_results={"extractions": []}))
    assert client.delete_extraction("missing")["deleted"] is False
