"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getCompany, type CompanyDetail } from "../lib/api";
import { CompareChart } from "../components/CompareChart";

type LoadState =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; details: CompanyDetail[] };

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444"];

export default function ComparePage() {
  const searchParams = useSearchParams();
  const ids = searchParams.get("ids")?.split(",").filter(Boolean) ?? [];
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    if (ids.length < 2) {
      setState({ kind: "error", message: "Select at least 2 companies to compare." });
      return;
    }
    let cancelled = false;
    Promise.all(ids.map((id) => getCompany(id)))
      .then((details) => {
        if (!cancelled) setState({ kind: "ready", details });
      })
      .catch((e) => {
        if (!cancelled)
          setState({
            kind: "error",
            message: e instanceof Error ? e.message : "Failed to load companies",
          });
      });
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams.get("ids")]);

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <header className="border-b border-zinc-200 dark:border-zinc-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <Link
            href="/"
            className="text-sm text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-50"
          >
            ← Back to dashboard
          </Link>
          <h1 className="text-lg font-semibold text-zinc-900 dark:text-zinc-50">
            Compare Companies
          </h1>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        {state.kind === "loading" && (
          <div className="flex items-center justify-center py-20 text-zinc-500">
            Loading…
          </div>
        )}

        {state.kind === "error" && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
            {state.message}
          </div>
        )}

        {state.kind === "ready" && (
          <>
            {/* Company headers */}
            <div className="mb-8 grid gap-4" style={{ gridTemplateColumns: `repeat(${state.details.length}, 1fr)` }}>
              {state.details.map((d, i) => {
                const c = d.company!;
                const upside =
                  c.price_target != null && c.current_price != null && c.current_price > 0
                    ? ((c.price_target - c.current_price) / c.current_price) * 100
                    : null;
                return (
                  <div
                    key={c.id}
                    className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900"
                  >
                    <div className="mb-1 flex items-center gap-2">
                      <div
                        className="h-3 w-3 rounded-full"
                        style={{ backgroundColor: COLORS[i] }}
                      />
                      <Link
                        href={`/companies/${c.id}`}
                        className="font-semibold text-zinc-900 hover:underline dark:text-zinc-50"
                      >
                        {c.name}
                      </Link>
                    </div>
                    <div className="mb-3 flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                      {c.ticker && <span className="font-mono">{c.ticker}</span>}
                      {c.sector && <span>· {c.sector}</span>}
                    </div>
                    {c.rating && (
                      <span
                        className={`inline-block rounded-md px-2 py-0.5 text-xs font-semibold ${
                          ["BUY", "OVERWEIGHT", "STRONG BUY", "OUTPERFORM"].includes(c.rating.toUpperCase())
                            ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                            : ["SELL", "UNDERWEIGHT", "UNDERPERFORM"].includes(c.rating.toUpperCase())
                              ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                              : "bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300"
                        }`}
                      >
                        {c.rating}
                      </span>
                    )}
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      {c.current_price != null && (
                        <div>
                          <div className="text-zinc-500">Price</div>
                          <div className="font-medium text-zinc-900 dark:text-zinc-100">
                            ${c.current_price.toFixed(2)}
                          </div>
                        </div>
                      )}
                      {c.price_target != null && (
                        <div>
                          <div className="text-zinc-500">Target</div>
                          <div className="font-medium text-zinc-900 dark:text-zinc-100">
                            ${c.price_target.toFixed(2)}
                          </div>
                        </div>
                      )}
                      {upside != null && (
                        <div>
                          <div className="text-zinc-500">Upside</div>
                          <div
                            className={`font-medium ${
                              upside >= 0
                                ? "text-green-600 dark:text-green-400"
                                : "text-red-600 dark:text-red-400"
                            }`}
                          >
                            {upside >= 0 ? "+" : ""}{upside.toFixed(1)}%
                          </div>
                        </div>
                      )}
                      {c.market_cap && (
                        <div>
                          <div className="text-zinc-500">Mkt Cap</div>
                          <div className="font-medium text-zinc-900 dark:text-zinc-100">
                            {c.market_cap}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Revenue chart */}
            <section className="mb-8">
              <h2 className="mb-4 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                Revenue Comparison
              </h2>
              <div className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                <CompareChart details={state.details} metric="revenue" colors={COLORS} />
              </div>
            </section>

            {/* EBITDA chart */}
            <section className="mb-8">
              <h2 className="mb-4 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                EBITDA Comparison
              </h2>
              <div className="rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
                <CompareChart details={state.details} metric="ebitda" colors={COLORS} />
              </div>
            </section>

            {/* Side-by-side metrics table */}
            <section className="mb-8">
              <h2 className="mb-4 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                Key Metrics
              </h2>
              <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
                <table className="w-full text-sm">
                  <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
                    <tr>
                      <th className="px-4 py-3 text-left">Metric</th>
                      {state.details.map((d) => (
                        <th key={d.company!.id} className="px-4 py-3 text-right">
                          {d.company!.ticker ?? d.company!.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <MetricRow label="Rating" details={state.details} render={(d) => d.company!.rating ?? "—"} />
                    <MetricRow label="Current Price" details={state.details} render={(d) => d.company!.current_price != null ? `$${d.company!.current_price.toFixed(2)}` : "—"} />
                    <MetricRow label="Price Target" details={state.details} render={(d) => d.company!.price_target != null ? `$${d.company!.price_target.toFixed(2)}` : "—"} />
                    <MetricRow label="Market Cap" details={state.details} render={(d) => d.company!.market_cap ?? "—"} />
                    <MetricRow label="Enterprise Value" details={state.details} render={(d) => d.company!.enterprise_value ?? "—"} />
                    <MetricRow label="Sector" details={state.details} render={(d) => d.company!.sector ?? "—"} />
                    <MetricRow label="Employees" details={state.details} render={(d) => d.company!.employees ?? "—"} />
                    <MetricRow
                      label="Gross Margin (Latest)"
                      details={state.details}
                      render={(d) => {
                        const latest = latestActual(d);
                        return latest?.gross_margin != null ? `${latest.gross_margin.toFixed(1)}%` : "—";
                      }}
                    />
                    <MetricRow
                      label="EBITDA Margin (Latest)"
                      details={state.details}
                      render={(d) => {
                        const latest = latestActual(d);
                        return latest?.ebitda_margin != null ? `${latest.ebitda_margin.toFixed(1)}%` : "—";
                      }}
                    />
                    <MetricRow
                      label="EPS (Latest)"
                      details={state.details}
                      render={(d) => {
                        const latest = latestActual(d);
                        return latest?.eps != null ? `$${latest.eps.toFixed(2)}` : "—";
                      }}
                    />
                  </tbody>
                </table>
              </div>
            </section>

            {/* Financials detail tables */}
            <section className="mb-8">
              <h2 className="mb-4 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
                Financials by Year
              </h2>
              <div className="grid gap-6" style={{ gridTemplateColumns: `repeat(${state.details.length}, 1fr)` }}>
                {state.details.map((d, i) => {
                  const sorted = [...d.financials].sort((a, b) => a.fiscal_year.localeCompare(b.fiscal_year));
                  return (
                    <div key={d.company!.id}>
                      <div className="mb-2 flex items-center gap-2">
                        <div className="h-3 w-3 rounded-full" style={{ backgroundColor: COLORS[i] }} />
                        <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                          {d.company!.ticker ?? d.company!.name}
                        </span>
                      </div>
                      <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
                        <table className="w-full text-xs">
                          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
                            <tr>
                              <th className="px-3 py-2 text-left">Year</th>
                              <th className="px-3 py-2 text-right">Rev</th>
                              <th className="px-3 py-2 text-right">EBITDA</th>
                              <th className="px-3 py-2 text-right">EPS</th>
                            </tr>
                          </thead>
                          <tbody>
                            {sorted.map((f) => (
                              <tr key={f.id} className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
                                <td className="px-3 py-2 font-mono text-zinc-900 dark:text-zinc-100">
                                  {f.fiscal_year}{f.is_estimate ? " (E)" : ""}
                                </td>
                                <td className="px-3 py-2 text-right text-zinc-900 dark:text-zinc-100">
                                  {f.revenue != null ? `$${f.revenue.toLocaleString()}M` : "—"}
                                </td>
                                <td className="px-3 py-2 text-right text-zinc-900 dark:text-zinc-100">
                                  {f.ebitda != null ? `$${f.ebitda.toLocaleString()}M` : "—"}
                                </td>
                                <td className="px-3 py-2 text-right text-zinc-900 dark:text-zinc-100">
                                  {f.eps != null ? `$${f.eps.toFixed(2)}` : "—"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

function MetricRow({
  label,
  details,
  render,
}: {
  label: string;
  details: CompanyDetail[];
  render: (d: CompanyDetail) => string;
}) {
  return (
    <tr className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
      <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-100">{label}</td>
      {details.map((d) => (
        <td key={d.company!.id} className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
          {render(d)}
        </td>
      ))}
    </tr>
  );
}

function latestActual(d: CompanyDetail) {
  const actuals = d.financials
    .filter((f) => !f.is_estimate)
    .sort((a, b) => b.fiscal_year.localeCompare(a.fiscal_year));
  return actuals[0] ?? null;
}
