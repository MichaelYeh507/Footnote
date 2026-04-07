export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Company = {
  id: string;
  report_id: string;
  name: string;
  ticker: string | null;
  sector: string | null;
  headquarters: string | null;
  ceo: string | null;
  description: string | null;
  founded: string | null;
  employees: string | null;
  market_cap: string | null;
  enterprise_value: string | null;
  rating: string | null;
  price_target: number | null;
  current_price: number | null;
  created_at: string;
};

export type FinancialYear = {
  id: string;
  company_id: string;
  fiscal_year: string;
  is_estimate: boolean;
  revenue: number | null;
  gross_profit: number | null;
  gross_margin: number | null;
  ebitda: number | null;
  ebitda_margin: number | null;
  net_income: number | null;
  eps: number | null;
  free_cash_flow: number | null;
  fcf_margin: number | null;
  total_debt: number | null;
  net_debt: number | null;
};

export type RiskFactor = {
  id: string;
  company_id: string;
  risk_name: string | null;
  description: string | null;
  likelihood: string | null;
  impact: string | null;
  mitigation: string | null;
};

export type ManagementMember = {
  id: string;
  company_id: string;
  name: string | null;
  title: string | null;
  tenure: string | null;
  background: string | null;
};

export type Valuation = {
  id: string;
  company_id: string;
  metric_name: string | null;
  company_value: string | null;
  peer_avg: string | null;
  premium_discount: string | null;
};

export type CompanyDetail = {
  company: Company | null;
  financials: FinancialYear[];
  risks: RiskFactor[];
  management: ManagementMember[];
  valuations: Valuation[];
  investment_thesis: string | null;
};

export type Report = {
  id: string;
  filename: string;
  status: "processing" | "completed" | "failed";
  upload_date: string;
  report_type: string | null;
  error_message: string | null;
  company: {
    id: string;
    name: string;
    ticker: string | null;
    report_id: string;
  } | null;
};

export async function listCompanies(): Promise<Company[]> {
  const res = await fetch(`${API_URL}/api/companies`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load companies (${res.status})`);
  return res.json();
}

export async function getCompany(id: string): Promise<CompanyDetail> {
  const res = await fetch(`${API_URL}/api/companies/${id}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to load company (${res.status})`);
  return res.json();
}

export async function deleteCompany(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/companies/${id}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Failed to delete company (${res.status})`);
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
