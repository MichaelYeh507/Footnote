"""Extraction schema for SEC 10-K filings.

Field set and tiering come from HYBRID-RETRIEVAL-SEC-PLAN.md §3. Fields are
chosen for difficulty spread, not coverage: a schema where every field is easy
produces a flat accuracy table and demonstrates nothing.

Kept in sync with backend/schema.sql (see tests/test_schema_drift.py).
"""

from typing import List, Optional

from pydantic import BaseModel


class RiskFactor(BaseModel):
    """Item 1A. Excluded from the v0 eval: set-valued fields need partial credit
    rules that are their own design problem.

    `likelihood` and `impact` were removed in migration 002 — a filing states
    risks, it does not rate them. Those values were model inferences rendered in
    the UI as extracted fact, and they have no ground truth to evaluate against.
    """

    risk_name: str
    description: Optional[str] = None
    mitigation: Optional[str] = None


class ManagementMember(BaseModel):
    """Item 10. Excluded from the v0 eval, same reason as RiskFactor."""

    name: str
    title: Optional[str] = None
    tenure: Optional[str] = None
    background: Optional[str] = None


class StructuredReport(BaseModel):
    """One extraction from one filing.

    Nine eval fields by tier:
        Surface        company_name, ticker
        Located        fiscal_year_end, employees, total_assets
        Disambiguated  revenue_most_recent_fy, ceo_name
        Absence-prone  dividends_declared_per_share, goodwill_impairment

    All monetary values are in millions, matching the extraction prompt. None
    means the model asserted the field is absent from the document — distinct
    from 0.0, which means the document stated zero. Collapsing the two would
    corrupt the false-extraction rate.
    """

    # --- Surface: cover page, near-verbatim ---
    company_name: str
    ticker: Optional[str] = None

    # --- Located: findable section, needs normalization or unit scaling ---
    fiscal_year_end: Optional[str] = None
    employees: Optional[str] = None
    total_assets: Optional[float] = None

    # --- Disambiguated: the right value must be picked from several candidates ---
    # Income statements show three comparative years; the task is the column.
    revenue_most_recent_fy: Optional[float] = None
    # Signature pages list many officers; combined titles, co-CEOs, transitions.
    ceo_name: Optional[str] = None

    # --- Absence-prone: genuinely missing from many filings ---
    dividends_declared_per_share: Optional[float] = None
    goodwill_impairment: Optional[float] = None

    # --- Retained for the product, excluded from the v0 eval ---
    sector: Optional[str] = None
    headquarters: Optional[str] = None
    description: Optional[str] = None
    founded: Optional[str] = None
    report_type: Optional[str] = None

    risks: List[RiskFactor] = []
    management: List[ManagementMember] = []
