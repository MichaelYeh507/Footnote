import Link from "next/link";
import { notFound } from "next/navigation";
import { getExtraction, fmtMillions } from "../../lib/api";
import { DeleteExtractionButton } from "../../components/DeleteExtractionButton";

export default async function ExtractionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let detail;
  try {
    detail = await getExtraction(id);
  } catch {
    notFound();
  }

  if (!detail?.extraction) notFound();

  const { extraction, risks, management } = detail;

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <header className="border-b border-zinc-200 dark:border-zinc-800">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
          <Link
            href="/extractions"
            className="text-sm text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-50"
          >
            ← Back to extractions
          </Link>
          <DeleteExtractionButton
            extractionId={extraction.id}
            companyName={extraction.company_name}
          />
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-6 py-10">
        <div className="mb-8 rounded-xl border border-zinc-200 bg-white p-6 dark:border-zinc-800 dark:bg-zinc-900">
          <h1 className="text-3xl font-semibold text-zinc-900 dark:text-zinc-50">
            {extraction.company_name}
          </h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
            {extraction.ticker && (
              <span className="font-mono">{extraction.ticker}</span>
            )}
            {extraction.sector && <span>· {extraction.sector}</span>}
            {extraction.headquarters && <span>· {extraction.headquarters}</span>}
          </div>
          {extraction.description && (
            <p className="mt-4 text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
              {extraction.description}
            </p>
          )}
        </div>

        {/* Extracted fields, grouped by difficulty tier. Tiers are the unit the
            accuracy table reports on, so the UI groups them the same way. */}
        <Section title="Extracted fields">
          <div className="overflow-hidden rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
            <table className="w-full text-sm">
              <tbody>
                <TierHeader label="Surface" />
                <Field label="Company name" value={extraction.company_name} />
                <Field label="Ticker" value={extraction.ticker} />

                <TierHeader label="Located" />
                <Field label="Fiscal year end" value={extraction.fiscal_year_end} />
                <Field label="Employees" value={extraction.employees} />
                <Field
                  label="Total assets"
                  value={
                    extraction.total_assets == null
                      ? null
                      : fmtMillions(extraction.total_assets)
                  }
                />

                <TierHeader label="Disambiguated" />
                <Field
                  label="Revenue (most recent FY)"
                  value={
                    extraction.revenue_most_recent_fy == null
                      ? null
                      : fmtMillions(extraction.revenue_most_recent_fy)
                  }
                />
                <Field label="CEO" value={extraction.ceo_name} />

                <TierHeader label="Absence-prone" />
                <Field
                  label="Dividends declared per share"
                  value={
                    extraction.dividends_declared_per_share == null
                      ? null
                      : `$${extraction.dividends_declared_per_share.toFixed(2)}`
                  }
                  absenceIsMeaningful
                />
                <Field
                  label="Goodwill impairment"
                  value={
                    extraction.goodwill_impairment == null
                      ? null
                      : fmtMillions(extraction.goodwill_impairment)
                  }
                  absenceIsMeaningful
                />
              </tbody>
            </table>
          </div>
        </Section>

        {risks.length > 0 && (
          <Section title={`Risk factors (${risks.length})`}>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {risks.map((r) => (
                <div
                  key={r.id}
                  className="rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
                >
                  <h3 className="mb-2 font-medium text-zinc-900 dark:text-zinc-50">
                    {r.risk_name}
                  </h3>
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

        {management.length > 0 && (
          <Section title={`Management (${management.length})`}>
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
      </main>
    </div>
  );
}

function TierHeader({ label }: { label: string }) {
  return (
    <tr className="border-b border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950">
      <td
        colSpan={2}
        className="px-4 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400"
      >
        {label}
      </td>
    </tr>
  );
}

/**
 * A null on an absence-prone field is a real extraction outcome ("the filing
 * does not state this"), not a rendering gap. Showing it as an em dash would
 * make a measured result look like missing data.
 */
function Field({
  label,
  value,
  absenceIsMeaningful = false,
}: {
  label: string;
  value: string | null;
  absenceIsMeaningful?: boolean;
}) {
  return (
    <tr className="border-b border-zinc-100 last:border-0 dark:border-zinc-800">
      <td className="w-1/2 px-4 py-3 text-zinc-500 dark:text-zinc-400">
        {label}
      </td>
      <td className="px-4 py-3 font-medium text-zinc-900 dark:text-zinc-100">
        {value ?? (
          <span className="text-zinc-400 dark:text-zinc-500">
            {absenceIsMeaningful ? "not stated in filing" : "—"}
          </span>
        )}
      </td>
    </tr>
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
