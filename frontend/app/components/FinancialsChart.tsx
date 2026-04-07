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
import type { FinancialYear } from "../lib/api";

export function FinancialsChart({
  financials,
}: {
  financials: FinancialYear[];
}) {
  const data = financials.map((f) => ({
    year: f.fiscal_year.replace("FY", "").replace("A", "").replace("E", "e"),
    Revenue: f.revenue,
    EBITDA: f.ebitda,
    "Net Income": f.net_income,
  }));

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
          <Bar dataKey="Revenue" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          <Bar dataKey="EBITDA" fill="#10b981" radius={[4, 4, 0, 0]} />
          <Bar dataKey="Net Income" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
