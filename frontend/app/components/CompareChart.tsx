"use client";

import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";
import type { CompanyDetail, FinancialYear } from "../lib/api";

type Metric = keyof Pick<FinancialYear, "revenue" | "ebitda" | "net_income" | "eps" | "free_cash_flow">;

export function CompareChart({
  details,
  metric,
  colors,
}: {
  details: CompanyDetail[];
  metric: Metric;
  colors: string[];
}) {
  // Collect all unique fiscal years across all companies
  const allYears = Array.from(
    new Set(details.flatMap((d) => d.financials.map((f) => f.fiscal_year))),
  ).sort();

  // Build data: one row per year, one key per company
  const data = allYears.map((year) => {
    const row: Record<string, string | number | null> = { year: year.replace("FY", "").replace("A", "").replace("E", "e") };
    for (const d of details) {
      const label = d.company!.ticker ?? d.company!.name;
      const fin = d.financials.find((f) => f.fiscal_year === year);
      row[label] = fin ? fin[metric] : null;
    }
    return row;
  });

  const companyLabels = details.map((d) => d.company!.ticker ?? d.company!.name);

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
          <XAxis
            dataKey="year"
            tick={{ fontSize: 12, fill: "#71717a" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "#71717a" }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v: number) =>
              v >= 1000 ? `$${(v / 1000).toFixed(1)}B` : `$${v}M`
            }
            width={64}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "#18181b",
              border: "1px solid #3f3f46",
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: "#a1a1aa" }}
            itemStyle={{ color: "#e4e4e7" }}
            formatter={(value) =>
              value != null ? `$${Number(value).toLocaleString()}M` : "—"
            }
          />
          <Legend
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 12, color: "#a1a1aa" }}
          />
          {companyLabels.map((label, i) => (
            <Bar
              key={label}
              dataKey={label}
              fill={colors[i % colors.length]}
              radius={[4, 4, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
