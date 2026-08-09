import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SERVICE_KEY"),
)

# Columns on `extractions`, excluding id/report_id/created_at. Explicit rather
# than a dict-spread of whatever the model returned: an unexpected key used to
# reach Postgres as an unknown-column error swallowed into a "failed" row.
EXTRACTION_FIELDS = (
    "company_name",
    "ticker",
    "fiscal_year_end",
    "employees",
    "total_assets",
    "revenue_most_recent_fy",
    "ceo_name",
    "dividends_declared_per_share",
    "goodwill_impairment",
    "sector",
    "headquarters",
    "description",
    "founded",
)
RISK_FIELDS = ("risk_name", "description", "mitigation")
MANAGEMENT_FIELDS = ("name", "title", "tenure", "background")


def _pick(source: dict, fields: tuple[str, ...]) -> dict:
    """Keep only known columns. Absent keys stay absent so the column default
    applies; an explicit null is preserved, since null means the model asserted
    the field is absent."""
    return {k: source[k] for k in fields if k in source}


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

    extraction = supabase.table("extractions").insert({
        "report_id": report_id,
        **_pick(data, EXTRACTION_FIELDS),
    }).execute()
    extraction_id = extraction.data[0]["id"]

    for risk in data.get("risks") or []:
        supabase.table("risks").insert(
            {"extraction_id": extraction_id, **_pick(risk, RISK_FIELDS)}
        ).execute()

    for member in data.get("management") or []:
        supabase.table("management").insert(
            {"extraction_id": extraction_id, **_pick(member, MANAGEMENT_FIELDS)}
        ).execute()

    return extraction_id


def delete_report(report_id: str) -> dict:
    """Delete a report row. ON DELETE CASCADE removes extractions + children."""
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
    """Fetch a single report with joined extraction info (for polling)."""
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
    extractions = (
        supabase.table("extractions")
        .select("id, company_name, ticker, report_id")
        .eq("report_id", report_id)
        .execute()
        .data
    )
    report["extraction"] = extractions[0] if extractions else None
    return report


def get_all_extractions():
    return supabase.table("extractions").select("*").execute().data


def delete_extraction(extraction_id: str) -> dict:
    """Delete an extraction and its parent report. Cascades handle children."""
    rows = (
        supabase.table("extractions")
        .select("report_id")
        .eq("id", extraction_id)
        .execute()
        .data
    )
    if not rows:
        return {"deleted": False, "reason": "not_found"}
    report_id = rows[0]["report_id"]

    supabase.table("extractions").delete().eq("id", extraction_id).execute()
    if report_id:
        supabase.table("reports").delete().eq("id", report_id).execute()
    return {"deleted": True, "extraction_id": extraction_id, "report_id": report_id}


def get_recent_reports(limit: int = 20):
    """List recent reports with basic joined extraction info."""
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
    extractions = (
        supabase.table("extractions")
        .select("id, company_name, ticker, report_id")
        .in_("report_id", report_ids)
        .execute()
        .data
    )
    by_report = {e["report_id"]: e for e in extractions}
    for report in reports:
        report["extraction"] = by_report.get(report["id"])
    return reports


def get_extraction_detail(extraction_id: str):
    extraction = (
        supabase.table("extractions").select("*").eq("id", extraction_id).execute().data
    )
    risks = (
        supabase.table("risks").select("*").eq("extraction_id", extraction_id).execute().data
    )
    management = (
        supabase.table("management")
        .select("*")
        .eq("extraction_id", extraction_id)
        .execute()
        .data
    )

    return {
        "extraction": extraction[0] if extraction else None,
        "risks": risks,
        "management": management,
    }
