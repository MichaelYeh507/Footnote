"""A numeric field's value must be a number by the time it is persisted.

Found on both DVN labels, which held the string `0.44 + 0.35 + 0.44 + 0.22`.
RESOLUTION 1 asks the labeler to sum stated quarterly declarations, the sum was
suggested in that written form, and the value box kept it verbatim as text.

That is the most expensive shape of defect this project has: the matcher cannot
parse it, so the instance scores the extractor **wrong** and is indistinguishable
from a genuine miss. Nothing in the record says the label was malformed rather
than the model mistaken.

The sum is computed rather than refused. Rejecting it would leave the labeler
doing mental arithmetic on the one field whose difficulty *is* arithmetic, which
is how the string got typed in the first place.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation.labeling import (  # noqa: E402
    NUMERIC_FIELDS, label_record, parse_sum, validate_label,
)


def record(field, value, kind="value"):
    return label_record(
        {"accession": "x", "ticker": "T", "period": "2024-12-31", "field": field},
        answer_kind=kind, value=value,
        locator={"anchor": "Dividends paid on common stock", "searched": []})


# ----------------------------------------------------------------- the sum

def test_the_real_dvn_expression_is_summed():
    assert parse_sum("0.44 + 0.35 + 0.44 + 0.22") == pytest.approx(1.45)


def test_a_constant_rate_repeated_is_summed():
    assert parse_sum("0.24 + 0.24 + 0.24 + 0.24") == pytest.approx(0.96)


def test_spacing_does_not_matter():
    assert parse_sum("0.75+0.75+0.75+0.75") == pytest.approx(3.0)


@pytest.mark.parametrize("junk", [
    "0.44", "1,450", "n/a", "", "   ", "0.44 - 0.35", "0.44 * 4",
    "__import__('os')", "0.44 + x", "4 x 0.75",
])
def test_only_addition_of_plain_numbers_is_recognised(junk):
    """Not an expression evaluator. No names, no other operators, nothing runs."""
    assert parse_sum(junk) is None


def test_a_bare_number_is_left_to_the_normal_path():
    assert parse_sum("3.00") is None


# ------------------------------------------------------ persisted as a number

def test_a_summed_expression_is_stored_as_a_number():
    row = validate_label(record("dividends_declared_per_share",
                                "0.44 + 0.35 + 0.44 + 0.22"))
    assert row["value"] == pytest.approx(1.45)
    assert not isinstance(row["value"], str)


def test_a_numeric_string_is_coerced():
    row = validate_label(record("total_assets", "16524"))
    assert row["value"] == pytest.approx(16524.0)


def test_commas_survive_coercion():
    row = validate_label(record("revenue_most_recent_fy", "3,256.902"))
    assert row["value"] == pytest.approx(3256.902)


def test_text_in_a_numeric_field_is_rejected():
    with pytest.raises(ValueError, match="numeric field"):
        validate_label(record("employees", "approximately 41,000 people"))


def test_zero_survives():
    """0 is a real answer for goodwill_impairment and must not be treated as
    missing by the coercion path."""
    row = validate_label(record("goodwill_impairment", "0"))
    assert row["value"] == 0


@pytest.mark.parametrize("field", ["company_name", "ticker", "ceo_name",
                                   "fiscal_year_end"])
def test_name_and_date_fields_are_left_alone(field):
    """These are strings by definition and must not be coerced."""
    row = validate_label(record(field, "Peter Konieczny"))
    assert row["value"] == "Peter Konieczny"


def test_every_numeric_field_is_covered():
    """A field added to the schema without a rule here would slip through."""
    assert set(NUMERIC_FIELDS) == {
        "employees", "total_assets", "revenue_most_recent_fy",
        "dividends_declared_per_share", "goodwill_impairment"}
