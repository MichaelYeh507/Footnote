"""Smoke tests proving the harness runs and app modules import under test env."""

from models.schemas import StructuredReport


def test_env_is_isolated_from_real_credentials():
    import os

    assert os.environ["OPENAI_API_KEY"].startswith("sk-test-")
    assert "test-project" in os.environ["SUPABASE_URL"]


def test_app_imports_without_real_credentials():
    from main import app

    assert app.title == "Document Pipeline API"


def test_structured_report_requires_company_name():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StructuredReport.model_validate({})

    assert StructuredReport.model_validate({"company_name": "Acme"}).ticker is None
