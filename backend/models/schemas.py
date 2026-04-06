from pydantic import BaseModel
from typing import Optional, List


class FinancialYear(BaseModel):
    fiscal_year: str  # "FY2024A" or "FY2026E"
    is_estimate: bool = False
    revenue: Optional[float] = None
    gross_profit: Optional[float] = None
    gross_margin: Optional[float] = None
    ebitda: Optional[float] = None
    ebitda_margin: Optional[float] = None
    net_income: Optional[float] = None
    eps: Optional[float] = None
    free_cash_flow: Optional[float] = None
    fcf_margin: Optional[float] = None


class RiskFactor(BaseModel):
    risk_name: str
    description: Optional[str] = None
    likelihood: Optional[str] = None
    impact: Optional[str] = None
    mitigation: Optional[str] = None


class ManagementMember(BaseModel):
    name: str
    title: Optional[str] = None
    tenure: Optional[str] = None
    background: Optional[str] = None


class Valuation(BaseModel):
    metric_name: str
    company_value: Optional[str] = None
    peer_avg: Optional[str] = None


class StructuredReport(BaseModel):
    company_name: str
    ticker: Optional[str] = None
    sector: Optional[str] = None
    headquarters: Optional[str] = None
    ceo: Optional[str] = None
    description: Optional[str] = None
    founded: Optional[str] = None
    employees: Optional[str] = None
    market_cap: Optional[str] = None
    enterprise_value: Optional[str] = None
    report_type: Optional[str] = None
    rating: Optional[str] = None
    price_target: Optional[float] = None
    current_price: Optional[float] = None
    investment_thesis: Optional[str] = None
    financials: List[FinancialYear] = []
    risks: List[RiskFactor] = []
    management: List[ManagementMember] = []
    valuations: List[Valuation] = []
