"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { Company } from "../lib/api";

export function CompaniesGrid({ companies }: { companies: Company[] }) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("");
  const [rating, setRating] = useState("");

  const sectors = useMemo(
    () =>
      Array.from(
        new Set(companies.map((c) => c.sector).filter((s): s is string => !!s)),
      ).sort(),
    [companies],
  );
  const ratings = useMemo(
    () =>
      Array.from(
        new Set(companies.map((c) => c.rating).filter((r): r is string => !!r)),
      ).sort(),
    [companies],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return companies.filter((c) => {
      if (sector && c.sector !== sector) return false;
      if (rating && c.rating !== rating) return false;
      if (q) {
        const hay = `${c.name} ${c.ticker ?? ""} ${c.sector ?? ""}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [companies, query, sector, rating]);

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
        <select
          value={rating}
          onChange={(e) => setRating(e.target.value)}
          className="rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
        >
          <option value="">All ratings</option>
          {ratings.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <p className="rounded-lg border border-dashed border-zinc-300 p-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
          No companies match your filters.
        </p>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {filtered.map((c) => (
            <Link
              key={c.id}
              href={`/companies/${c.id}`}
              className="block rounded-xl border border-zinc-200 bg-white p-5 transition-all hover:border-zinc-400 hover:shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-600"
            >
              <div className="mb-2 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-zinc-900 dark:text-zinc-50">
                    {c.name}
                  </h3>
                  {c.ticker && (
                    <p className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                      {c.ticker}
                    </p>
                  )}
                </div>
                {c.rating && (
                  <span
                    className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${
                      c.rating === "BUY" || c.rating === "OVERWEIGHT"
                        ? "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300"
                        : c.rating === "SELL" || c.rating === "UNDERWEIGHT"
                          ? "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300"
                          : "bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300"
                    }`}
                  >
                    {c.rating}
                  </span>
                )}
              </div>
              {c.sector && (
                <p className="mb-3 text-xs text-zinc-500 dark:text-zinc-400">
                  {c.sector}
                </p>
              )}
              {c.description && (
                <p className="line-clamp-3 text-sm text-zinc-600 dark:text-zinc-400">
                  {c.description}
                </p>
              )}
              {(c.price_target || c.current_price) && (
                <div className="mt-3 flex gap-4 text-xs">
                  {c.current_price != null && (
                    <div>
                      <span className="text-zinc-500">Price: </span>
                      <span className="font-medium text-zinc-900 dark:text-zinc-100">
                        ${c.current_price}
                      </span>
                    </div>
                  )}
                  {c.price_target != null && (
                    <div>
                      <span className="text-zinc-500">Target: </span>
                      <span className="font-medium text-zinc-900 dark:text-zinc-100">
                        ${c.price_target}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
