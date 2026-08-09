"""Contract tests for the trimmed SEC extraction schema.

These encode the target shape from HYBRID-RETRIEVAL-SEC-PLAN.md §3. They are
written before models/schemas.py is updated, so they fail until the code catches
up — that is deliberate. A test that only ever ran green against the finished
code proves nothing about the migration.

The nine eval fields, by tier:
    Surface        company_name, ticker
    Located        fiscal_year_end, employees, total_assets
    Disambiguated  revenue_most_recent_fy, ceo_name
    Absence-prone  dividends_declared_per_share, goodwill_impairment
"""

import pytest
from pydantic import ValidationError

from models.schemas import ManagementMember, RiskFactor, StructuredReport

EVAL_FIELDS = {
    "company_name",
    "ticker",
    "fiscal_year_end",
    "employees",
    "total_assets",
    "revenue_most_recent_fy",
    "ceo_name",
    "dividends_declared_per_share",
    "goodwill_impairment",
}

# Cut in migration 002: no counterpart in an SEC filing, or model inference
# rendered as extracted fact.
REMOVED_FIELDS = {
    "rating",
    "price_target",
    "current_price",
    "investment_thesis",
    "market_cap",
    "enterprise_value",
    "financials",
    "valuations",
    "ceo",  # renamed to ceo_name
}


def test_all_nine_eval_fields_exist():
    missing = EVAL_FIELDS - set(StructuredReport.model_fields)
    assert not missing, f"eval fields absent from schema: {sorted(missing)}"


def test_removed_fields_are_gone():
    present = REMOVED_FIELDS & set(StructuredReport.model_fields)
    assert not present, f"fields should have been cut: {sorted(present)}"


def test_company_name_is_the_only_required_field():
    with pytest.raises(ValidationError):
        StructuredReport.model_validate({})

    report = StructuredReport.model_validate({"company_name": "Acme Corp"})
    for field in EVAL_FIELDS - {"company_name"}:
        assert getattr(report, field) is None


@pytest.mark.parametrize(
    "field", ["dividends_declared_per_share", "goodwill_impairment"]
)
def test_absence_prone_fields_accept_explicit_null(field):
    """Null means the model asserted absence, which is what the
    false-extraction rate measures. It must be representable, not an error."""
    report = StructuredReport.model_validate({"company_name": "Acme", field: None})
    assert getattr(report, field) is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("total_assets", 1234.5),
        ("revenue_most_recent_fy", 987.6),
        ("dividends_declared_per_share", 1.25),
        ("goodwill_impairment", 0.0),
    ],
)
def test_numeric_fields_are_numeric(field, value):
    report = StructuredReport.model_validate({"company_name": "Acme", field: value})
    assert getattr(report, field) == value


def test_zero_is_distinct_from_absent():
    """A stated zero and a missing line item are different outcomes; collapsing
    them would corrupt the false-extraction rate."""
    stated = StructuredReport.model_validate(
        {"company_name": "Acme", "goodwill_impairment": 0.0}
    )
    absent = StructuredReport.model_validate({"company_name": "Acme"})
    assert stated.goodwill_impairment == 0.0
    assert absent.goodwill_impairment is None
    assert stated.goodwill_impairment != absent.goodwill_impairment


def test_risks_carry_no_model_inferred_ratings():
    """A filing states risks; it does not rate them. likelihood/impact had no
    ground truth and were rendered in the UI as extracted fact."""
    fields = set(RiskFactor.model_fields)
    assert "likelihood" not in fields
    assert "impact" not in fields
    assert {"risk_name", "description", "mitigation"} <= fields


def test_management_shape_unchanged():
    assert {"name", "title", "tenure", "background"} <= set(
        ManagementMember.model_fields
    )


def test_product_only_fields_survive_outside_the_eval():
    """Retained for the product, excluded from the v0 eval."""
    assert {"sector", "headquarters", "description", "founded"} <= set(
        StructuredReport.model_fields
    )
