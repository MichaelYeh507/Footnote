"""The extraction prompt is a measurement instrument, so it gets guarded.

Every number this project publishes is produced by this prompt. Two failure
modes matter enough to encode:

* Drift between the prompt's JSON block and the Pydantic model. The model
  validates what the prompt asked for, so a field added to one and not the
  other fails silently at extraction time, on real documents, after the API
  call has already been paid for.
* Leakage from the calibration set. The field notes were written after reading
  8 specific filings. If issuer-specific wording ever gets baked into the
  prompt and those issuers appear in the eval corpus, the accuracy number is
  measuring memorization. A name in the prompt is the cheap, visible symptom.
"""

import json
import re

from models.schemas import StructuredReport
from services.openai_structurer import MODEL, SYSTEM_PROMPT, TEMPERATURE

EVAL_FIELDS = (
    "company_name",
    "ticker",
    "fiscal_year_end",
    "employees",
    "total_assets",
    "revenue_most_recent_fy",
    "ceo_name",
    "dividends_declared_per_share",
    "goodwill_impairment",
)

# Issuers whose filings were read while calibrating the field notes. They are
# excluded from the eval corpus; naming any of them in the prompt would be the
# leak this guards against.
CALIBRATION_ISSUERS = (
    "apple", "costco", "chipotle", "jpmorgan", "jpmorgan chase",
    "johnson & johnson", "exxon", "exxonmobil", "caterpillar", "nextera",
    "aapl", "cost", "cmg", "jpm", "jnj", "xom", "cat", "nee",
)


def _prompt_json_block() -> dict:
    """The schema block is the last {...} in the prompt."""
    start = SYSTEM_PROMPT.index("{\n  \"company_name\"")
    return json.loads(
        re.sub(r":\s*number or null", ': "number or null"', SYSTEM_PROMPT[start:])
    )


def test_prompt_documents_every_eval_field():
    for field in EVAL_FIELDS:
        assert field in SYSTEM_PROMPT, f"{field} is not mentioned in the prompt"


def test_prompt_json_block_matches_the_pydantic_model():
    """Drift here fails at extraction time on a real document, not in CI."""
    assert set(_prompt_json_block()) == set(StructuredReport.model_fields)


def test_prompt_states_the_three_outcome_rule():
    """null / 0 / a number are three answers, not two. The false-extraction rate
    is only meaningful if the model is told they differ."""
    lowered = SYSTEM_PROMPT.lower()
    assert "null" in lowered
    assert re.search(r"never use 0 to mean", lowered), "the 0-as-not-found trap is unaddressed"
    assert "the filing states there was none" in lowered


def test_prompt_addresses_units_varying_within_a_document():
    """A single per-document unit rule does not hold; filings mix units between
    tables. Calibrated 2026-08-09."""
    lowered = SYSTEM_PROMPT.lower()
    assert "millions" in lowered
    assert "different units in different tables" in lowered
    assert "thousands" in lowered, "the scale-down case needs a worked example"


def test_prompt_warns_about_the_principal_executive_offices_distractor():
    """Present in every filing checked; one letter from the real anchor."""
    assert "principal executive offices" in SYSTEM_PROMPT
    assert "street address" in SYSTEM_PROMPT


def test_prompt_gives_a_rule_for_choosing_the_revenue_line():
    """~7 distinct labels across 8 filings, one of which folds in non-operating
    income. Without a rule this field measures the prompt, not the model."""
    assert "revenues and other income" in SYSTEM_PROMPT, "the trap label is unaddressed"
    assert "non-operating" in SYSTEM_PROMPT


def test_prompt_does_not_name_any_calibration_issuer():
    """Guards the dev/test split. See module docstring."""
    lowered = re.sub(r"[^a-z& ]", " ", SYSTEM_PROMPT.lower())
    words = set(lowered.split())
    named = [
        issuer for issuer in CALIBRATION_ISSUERS
        if (issuer in words if " " not in issuer else issuer in lowered)
    ]
    assert not named, f"calibration issuers named in the prompt: {named}"


def test_prompt_does_not_resurrect_removed_fields():
    """Migration 002 dropped these. A prompt that still asks for them produces
    keys the allowlist silently discards.

    Word-boundary matched: 'rating' is a substring of 'operating', which the
    revenue-line rule legitimately uses.
    """
    removed = ("likelihood", "impact", "rating", "price_target", "market_cap",
               "enterprise_value", "current_price")
    resurrected = [f for f in removed if re.search(rf"\b{re.escape(f)}\b", SYSTEM_PROMPT, re.I)]
    assert not resurrected, f"prompt still asks for dropped fields: {resurrected}"
    assert not set(removed) & set(_prompt_json_block())


def test_model_is_recorded_alongside_the_prompt():
    """Results are only reproducible against a named model. The resume claimed
    GPT-4o; the pipeline runs mini."""
    assert MODEL == "gpt-4o-mini"


def test_sampling_is_pinned_to_zero():
    """The extractor is a measurement instrument. Sampling temperature is a free
    parameter that changes results without changing code, so it is pinned and
    asserted. Stability was measured before pinning -- see corpus/stability.json."""
    assert TEMPERATURE == 0.0
