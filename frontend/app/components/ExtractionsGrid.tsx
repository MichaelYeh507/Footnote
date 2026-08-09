"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { Extraction } from "../lib/api";
import { fmtMillions } from "../lib/api";

export function ExtractionsGrid({ extractions }: { extractions: Extraction[] }) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("");

  const sectors = useMemo(
    () =>
      Array.from(
        new Set(
          extractions.map((e) => e.sector).filter((s): s is string => !!s),
        ),
      ).sort(),
    [extractions],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return extractions.filter((e) => {
      if (sector && e.sector !== sector) return false;
      if (q) {
        const hay =
          `${e.company_name} ${e.ticker ?? ""} ${e.sector ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [extractions, query, sector]);

  return (
    <>
      <div className="mb-6 flex flex-col gap-3 sm:flex-row">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name, ticker, sector…"
          className="flex-1 rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        />
        <select
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        >
          <option value="">All sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
          No extractions match your filters.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((e) => (
            <Link
              key={e.id}
              href={`/extractions/${e.id}`}
              className="block rounded-xl border border-zinc-200 bg-white p-5 transition-all hover:border-zinc-400 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-600"
            >
              <div className="mb-2 min-w-0">
                <h3 className="truncate font-semibold text-zinc-900 dark:text-zinc-50">
                  {e.company_name}
                </h3>
                <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                  {e.ticker && <span className="font-mono">{e.ticker}</span>}
                  {e.fiscal_year_end && <span>FY end {e.fiscal_year_end}</span>}
                </div>
              </div>
              {e.sector && (
                <p className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">
                  {e.sector}
                </p>
              )}
              {e.description && (
                <p className="line-clamp-3 text-sm text-zinc-600 dark:text-zinc-400">
                  {e.description}
                </p>
              )}
              <div className="mt-3 flex gap-4 text-xs">
                <div>
                  <span className="text-zinc-500">Revenue: </span>
                  <span className="font-medium text-zinc-900 dark:text-zinc-100">
                    {fmtMillions(e.revenue_most_recent_fy)}
                  </span>
                </div>
                <div>
                  <span className="text-zinc-500">Assets: </span>
                  <span className="font-medium text-zinc-900 dark:text-zinc-100">
                    {fmtMillions(e.total_assets)}
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
