export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * One extraction from one filing. Not an entity: with ~40 filings from 3-4
 * issuers, several rows share an issuer. Entity resolution is out of scope.
 *
 * Mirrors backend/models/schemas.py and backend/schema.sql. The nine eval
 * fields are marked by tier; the rest are product-only.
 */
export type Extraction = {
  id: string;
  report_id: string;

  // Surface tier
  company_name: string;
  ticker: string | null;
  // Located tier
  fiscal_year_end: string | null;
  employees: string | null;
  total_assets: number | null;
  // Disambiguated tier
  revenue_most_recent_fy: number | null;
  ceo_name: string | null;
  // Absence-prone tier. null means the filing does not state the value —
  // distinct from 0, which means it states zero.
  dividends_declared_per_share: number | null;
  goodwill_impairment: number | null;

  // Product-only, excluded from the eval
  sector: string | null;
  headquarters: string | null;
  description: string | null;
  founded: string | null;

  created_at: string;
};

export type RiskFactor = {
  id: string;
  extraction_id: string;
  risk_name: string | null;
  description: string | null;
  mitigation: string | null;
};

export type ManagementMember = {
  id: string;
  extraction_id: string;
  name: string | null;
  title: string | null;
  tenure: string | null;
  background: string | null;
};

export type ExtractionDetail = {
  extraction: Extraction | null;
  risks: RiskFactor[];
  management: ManagementMember[];
};

export type Report = {
  id: string;
  filename: string;
  status: "processing" | "completed" | "failed";
  upload_date: string;
  report_type: string | null;
  error_message: string | null;
  extraction: {
    id: string;
    company_name: string;
    ticker: string | null;
    report_id: string;
  } | null;
};

export async function listExtractions(): Promise<Extraction[]> {
  const res = await fetch(`${API_URL}/api/extractions`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load extractions (${res.status})`);
  return res.json();
}

export async function getExtraction(id: string): Promise<ExtractionDetail> {
  const res = await fetch(`${API_URL}/api/extractions/${id}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load extraction (${res.status})`);
  return res.json();
}

export async function deleteExtraction(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/extractions/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete extraction (${res.status})`);
}

export async function listReports(limit = 20): Promise<Report[]> {
  const res = await fetch(`${API_URL}/api/reports?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load reports (${res.status})`);
  return res.json();
}

export async function deleteReport(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/reports/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete report (${res.status})`);
}

export async function getReport(id: string): Promise<Report> {
  const res = await fetch(`${API_URL}/api/reports/${id}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load report (${res.status})`);
  return res.json();
}

export async function uploadPdf(
  file: File,
): Promise<{ status: string; report_id: string }> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_URL}/api/upload`, {
    method: "POST",
    body: formData,
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.detail ?? `Upload failed (${res.status})`);
  }
  return data;
}

/** Millions, matching the extraction prompt. null renders as an em dash; 0 is
 *  a real stated value and must not be shown as missing. */
export function fmtMillions(n: number | null): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toLocaleString(undefined, { maximumFractionDigits: 2 })}B`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}M`;
}
