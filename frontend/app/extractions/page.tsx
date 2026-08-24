import Link from "next/link";
import {
  listExtractions,
  listReports,
  type Extraction,
  type Report,
} from "../lib/api";
import { ExtractionsGrid } from "../components/ExtractionsGrid";
import { RecentUploads } from "../components/RecentUploads";

export default async function ExtractionsPage() {
  let extractions: Extraction[] = [];
  let reports: Report[] = [];
  let error: string | null = null;
  try {
    [extractions, reports] = await Promise.all([
      listExtractions(),
      listReports(10),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Unknown error";
  }

  return (
    <div className="flex flex-1 flex-col bg-zinc-50 dark:bg-black">
      <header className="border-b border-zinc-200 dark:border-zinc-800">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div>
            <h1 className="text-xl font-semibold text-zinc-900 dark:text-zinc-50">
              Extractions
            </h1>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Structured extraction over filings
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="rounded-lg border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
            >
              Ask the filings
            </Link>
            <Link
              href="/upload"
              className="rounded-lg bg-zinc-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
            >
              Upload PDF
            </Link>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-10">
        {error && (
          <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
            Failed to load data: {error}
            <div className="mt-1 text-xs opacity-75">
              Make sure the backend is running at{" "}
              <code>http://localhost:8000</code>.
            </div>
          </div>
        )}

        <RecentUploads reports={reports} />

        <h2 className="mb-4 text-2xl font-semibold text-zinc-900 dark:text-zinc-50">
          Extractions
        </h2>

        {extractions.length === 0 && !error ? (
          <div className="rounded-lg border border-dashed border-zinc-300 p-12 text-center dark:border-zinc-700">
            <p className="text-zinc-500 dark:text-zinc-400">
              No extractions yet. Upload a PDF to get started.
            </p>
            <Link
              href="/upload"
              className="mt-4 inline-block text-sm font-medium text-blue-600 hover:underline dark:text-blue-400"
            >
              Upload your first filing →
            </Link>
          </div>
        ) : (
          <ExtractionsGrid extractions={extractions} />
        )}
      </main>
    </div>
  );
}
