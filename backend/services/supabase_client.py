import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY"),
)


def create_report(filename: str) -> dict:
    """Create a new report record with 'processing' status."""
    result = supabase.table("reports").insert({
        "filename": filename,
        "status": "processing",
    }).execute()
    return result.data[0]


def save_structured_data(report_id: str, raw_text: str, data: dict) -> str:
    """Save the full structured JSON + insert into normalized tables."""
    supabase.table("reports").update({
        "raw_text": raw_text,
        "structured_json": data,
        "report_type": data.get("report_type"),
        "status": "completed",
    }).eq("id", report_id).execute()

    company = supabase.table("companies").insert({
        "report_id": report_id,
        "name": data.get("company_name"),
        "ticker": data.get("ticker"),
        "sector": data.get("sector"),
        "headquarters": data.get("headquarters"),
        "ceo": data.get("ceo"),
        "description": data.get("description"),
        "founded": data.get("founded"),
        "employees": data.get("employees"),
        "market_cap": data.get("market_cap"),
        "enterprise_value": data.get("enterprise_value"),
        "rating": data.get("rating"),
        "price_target": data.get("price_target"),
        "current_price": data.get("current_price"),
    }).execute()
    company_id = company.data[0]["id"]

    for fin in data.get("financials", []):
        supabase.table("financials").insert({"company_id": company_id, **fin}).execute()

    for risk in data.get("risks", []):
        supabase.table("risks").insert({"company_id": company_id, **risk}).execute()

    for mgmt in data.get("management", []):
        supabase.table("management").insert({"company_id": company_id, **mgmt}).execute()

    for val in data.get("valuations", []):
        supabase.table("valuations").insert({"company_id": company_id, **val}).execute()

    return company_id


def delete_report(report_id: str) -> dict:
    """Delete a report row. ON DELETE CASCADE removes companies + children."""
    existing = supabase.table("reports").select("id").eq("id", report_id).execute().data
    if not existing:
        return {"deleted": False}
    supabase.table("reports").delete().eq("id", report_id).execute()
    return {"deleted": True, "report_id": report_id}


def mark_report_failed(report_id: str, error_message: str) -> None:
    supabase.table("reports").update({
        "status": "failed",
        "error_message": error_message,
    }).eq("id", report_id).execute()


def get_report(report_id: str) -> dict | None:
    """Fetch a single report with joined company info (for polling)."""
    rows = (
        supabase.table("reports")
        .select("id, filename, status, upload_date, report_type, error_message")
        .eq("id", report_id)
        .execute()
        .data
    )
    if not rows:
        return None
    report = rows[0]
    companies = (
        supabase.table("companies")
        .select("id, name, ticker, report_id")
        .eq("report_id", report_id)
        .execute()
        .data
    )
    report["company"] = companies[0] if companies else None
    return report


def get_all_companies():
    return supabase.table("companies").select("*").execute().data


def delete_company(company_id: str) -> dict:
    """Delete a company and its parent report. Cascades handle children."""
    company = supabase.table("companies").select("report_id").eq("id", company_id).execute().data
    if not company:
        return {"deleted": False, "reason": "not_found"}
    report_id = company[0]["report_id"]

    supabase.table("companies").delete().eq("id", company_id).execute()
    if report_id:
        supabase.table("reports").delete().eq("id", report_id).execute()
    return {"deleted": True, "company_id": company_id, "report_id": report_id}


def get_recent_reports(limit: int = 20):
    """List recent reports with basic joined company info."""
    reports = (
        supabase.table("reports")
        .select("id, filename, status, upload_date, report_type, error_message")
        .order("upload_date", desc=True)
        .limit(limit)
        .execute()
        .data
    )
    if not reports:
        return []
    report_ids = [r["id"] for r in reports]
    companies = (
        supabase.table("companies")
        .select("id, name, ticker, report_id")
        .in_("report_id", report_ids)
        .execute()
        .data
    )
    by_report = {c["report_id"]: c for c in companies}
    for r in reports:
        r["company"] = by_report.get(r["id"])
    return reports


def get_company_detail(company_id: str):
    company = supabase.table("companies").select("*").eq("id", company_id).execute().data
    financials = supabase.table("financials").select("*").eq("company_id", company_id).execute().data
    risks = supabase.table("risks").select("*").eq("company_id", company_id).execute().data
    management = supabase.table("management").select("*").eq("company_id", company_id).execute().data
    valuations = supabase.table("valuations").select("*").eq("company_id", company_id).execute().data

    # Pull investment_thesis from the report's structured_json
    investment_thesis = None
    if company:
        report_id = company[0].get("report_id")
        if report_id:
            report = supabase.table("reports").select("structured_json").eq("id", report_id).execute().data
            if report and report[0].get("structured_json"):
                investment_thesis = report[0]["structured_json"].get("investment_thesis")

    return {
        "company": company[0] if company else None,
        "financials": financials,
        "risks": risks,
        "management": management,
        "valuations": valuations,
        "investment_thesis": investment_thesis,
    }
