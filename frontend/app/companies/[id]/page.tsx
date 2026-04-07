import Link from "next/link";
import { notFound } from "next/navigation";
import { getCompany } from "../../lib/api";
import { DeleteCompanyButton } from "../../components/DeleteCompanyButton";
import { FinancialsChart } from "../../components/FinancialsChart";

export default async function CompanyDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let detail;
  try {
    detail = await getCompany(id);
  } catch {
    notFound();
  }

  if (!detail?.company) notFound();

  const { company, financials, risks, management, valuations, investment_thesis } = detail;
  const sortedFinancials = [...financials].sort((a, b) =>
    a.fiscal_year.localeCompare(b.fiscal_year),
  );

  const upside =
    company.price_target != null && company.current_price != null && company.current_price > 0
      ? ((company.price_target - company.current_price) / company.current_price) * 100
      : null;

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
          <DeleteCompanyButton
            companyId={company.id}
            companyName={company.name}
          />
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        {/* Hero */}
        <div className="mb-8 rounded-xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          {/* Top row: name + rating badge */}
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold text-zinc-900 dark:text-zinc-50">
                {company.name}
              </h1>
              <div className="mt-1 flex items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
                {company.ticker && (
                  <span className="font-mono">{company.ticker}</span>
                )}
                {company.sector && <span>· {company.sector}</span>}
                {company.headquarters && <span>· {company.headquarters}</span>}
              </div>
            </div>
            {company.rating && <RatingBadge rating={company.rating} />}
          </div>

          {/* Price row */}
          {(company.current_price != null || company.price_target != null) && (
            <div className="mb-4 flex flex-wrap items-end gap-6 border-b border-zinc-200 pb-4 dark:border-zinc-800">
              {company.current_price != null && (
                <div>
                  <div className="text-xs uppercase tracking-wide text-zinc-500">
                    Current Price
                  </div>
                  <div className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
                    ${company.current_price.toFixed(2)}
                  </div>
                </div>
              )}
              {company.price_target != null && (
                <div>
                  <div className="text-xs uppercase tracking-wide text-zinc-500">
                    Price Target
                  </div>
                  <div className="text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
                    ${company.price_target.toFixed(2)}
                  </div>
                </div>
              )}
              {upside != null && (
                <div>
                  <div className="text-xs uppercase tracking-wide text-zinc-500">
                    Upside
                  </div>
                  <div
                    className={`text-2xl font-semibold ${
                      upside >= 0
                        ? "text-green-600 dark:text-green-400"
                        : "text-red-600 dark:text-red-400"
                    }`}
                  >
                    {upside >= 0 ? "+" : ""}
                    {upside.toFixed(1)}%
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Description */}
          {company.description && (
            <p className="mb-4 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
              {company.description}
            </p>
          )}

          {/* Stats grid */}
          <div className="grid grid-cols-2 gap-4 border-t border-zinc-200 pt-4 text-sm md:grid-cols-4 dark:border-zinc-800">
            <Stat label="CEO" value={company.ceo} />
            <Stat label="Founded" value={company.founded} />
            <Stat label="Employees" value={company.employees} />
            <Stat label="Market Cap" value={company.market_cap} />
            <Stat label="Enterprise Value" value={company.enterprise_value} />
          </div>
        </div>

        {/* Investment Thesis */}
        {investment_thesis && (
          <div className="mb-8 rounded-xl border border-blue-200 bg-blue-50 p-6 dark:border-blue-900 dark:bg-blue-950/40">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-blue-700 dark:text-blue-300">
              Investment Thesis
            </h2>
            <p className="text-sm leading-relaxed text-blue-900 dark:text-blue-100">
              {investment_thesis}
            </p>
          </div>
        )}

        {/* Financials Chart + Table */}
        {sortedFinancials.length > 0 && (
          <Section title="Financials">
            <div className="mb-4 rounded-xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
              <FinancialsChart financials={sortedFinancials} />
            </div>
            <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
                  <tr>
                    <th className="px-4 py-3 text-left">Year</th>
                    <th className="px-4 py-3 text-right">Revenue</th>
                    <th className="px-4 py-3 text-right">Gross Margin</th>
                    <th className="px-4 py-3 text-right">EBITDA</th>
                    <th className="px-4 py-3 text-right">Net Income</th>
                    <th className="px-4 py-3 text-right">EPS</th>
                    <th className="px-4 py-3 text-right">FCF</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedFinancials.map((f) => (
                    <tr
                      key={f.id}
                      className="border-b border-zinc-100 last:border-0 dark:border-zinc-800"
                    >
                      <td className="px-4 py-3 font-mono text-xs text-zinc-900 dark:text-zinc-100">
                        {f.fiscal_year}
                        {f.is_estimate && (
                          <span className="ml-1 text-zinc-400">(E)</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {fmt(f.revenue)}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {fmtPct(f.gross_margin)}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {fmt(f.ebitda)}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {fmt(f.net_income)}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {f.eps != null ? `$${f.eps.toFixed(2)}` : "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {fmt(f.free_cash_flow)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        )}

        {/* Risks */}
        {risks.length > 0 && (
          <Section title="Risk Factors">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {risks.map((r) => (
                <div
                  key={r.id}
                  className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <h3 className="font-medium text-zinc-900 dark:text-zinc-50">
                      {r.risk_name}
                    </h3>
                    <div className="flex shrink-0 gap-1">
                      {r.likelihood && (
                        <span className="rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300">
                          {r.likelihood}
                        </span>
                      )}
                      {r.impact && (
                        <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-300">
                          {r.impact}
                        </span>
                      )}
                    </div>
                  </div>
                  {r.description && (
                    <p className="text-sm text-zinc-600 dark:text-zinc-400">
                      {r.description}
                    </p>
                  )}
                  {r.mitigation && (
                    <p className="mt-2 text-xs text-zinc-500">
                      <strong>Mitigation:</strong> {r.mitigation}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* Management */}
        {management.length > 0 && (
          <Section title="Management Team">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {management.map((m) => (
                <div
                  key={m.id}
                  className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <h3 className="font-medium text-zinc-900 dark:text-zinc-50">
                    {m.name}
                  </h3>
                  <p className="text-sm text-zinc-500">
                    {m.title}
                    {m.tenure && ` · ${m.tenure}`}
                  </p>
                  {m.background && (
                    <p className="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
                      {m.background}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </Section>
        )}

        {/* Valuations */}
        {valuations.length > 0 && (
          <Section title="Valuation">
            <div className="overflow-x-auto rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
              <table className="w-full text-sm">
                <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
                  <tr>
                    <th className="px-4 py-3 text-left">Metric</th>
                    <th className="px-4 py-3 text-right">Company</th>
                    <th className="px-4 py-3 text-right">Peer Avg</th>
                    <th className="px-4 py-3 text-right">Premium/Discount</th>
                  </tr>
                </thead>
                <tbody>
                  {valuations.map((v) => (
                    <tr
                      key={v.id}
                      className="border-b border-zinc-100 last:border-0 dark:border-zinc-800"
                    >
                      <td className="px-4 py-3 text-zinc-900 dark:text-zinc-100">
                        {v.metric_name}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {v.company_value ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {v.peer_avg ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-zinc-900 dark:text-zinc-100">
                        {v.premium_discount ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        )}
      </main>
    </div>
  );
}

function RatingBadge({ rating }: { rating: string }) {
  const isPositive = ["BUY", "OVERWEIGHT", "STRONG BUY", "OUTPERFORM"].includes(
    rating.toUpperCase(),
  );
  const isNegative = ["SELL", "UNDERWEIGHT", "UNDERPERFORM"].includes(
    rating.toUpperCase(),
  );

  return (
    <div
      className={`flex flex-col items-center rounded-lg px-5 py-3 ${
        isPositive
          ? "bg-green-100 dark:bg-green-950"
          : isNegative
            ? "bg-red-100 dark:bg-red-950"
            : "bg-zinc-100 dark:bg-zinc-800"
      }`}
    >
      <span className="text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Rating
      </span>
      <span
        className={`text-lg font-bold ${
          isPositive
            ? "text-green-700 dark:text-green-300"
            : isNegative
              ? "text-red-700 dark:text-red-300"
              : "text-zinc-700 dark:text-zinc-300"
        }`}
      >
        {rating}
      </span>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-zinc-500">
        {label}
      </div>
      <div className="mt-0.5 text-zinc-900 dark:text-zinc-100">
        {value ?? "—"}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-8">
      <h2 className="mb-4 text-xl font-semibold text-zinc-900 dark:text-zinc-50">
        {title}
      </h2>
      {children}
    </section>
  );
}

function fmt(n: number | null): string {
  if (n == null) return "—";
  return `$${n.toLocaleString()}M`;
}

function fmtPct(n: number | null): string {
  if (n == null) return "—";
  return `${n.toFixed(1)}%`;
}
