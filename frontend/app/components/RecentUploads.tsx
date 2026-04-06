"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { deleteReport, type Report } from "../lib/api";

export function RecentUploads({ reports }: { reports: Report[] }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (reports.length === 0) return null;

  const onDelete = async (r: Report) => {
    const label = r.company?.name ?? r.filename;
    if (!confirm(`Remove "${label}" from recent uploads? This cannot be undone.`)) return;
    setDeletingId(r.id);
    setError(null);
    try {
      await deleteReport(r.id);
      startTransition(() => router.refresh());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <section className="mb-10">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Recent Uploads
      </h2>
      {error && (
        <div className="mb-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
          {error}
        </div>
      )}
      <div className="overflow-hidden rounded-xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
        <table className="w-full text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-xs uppercase tracking-wide text-zinc-500 dark:border-zinc-800 dark:bg-zinc-950 dark:text-zinc-400">
            <tr>
              <th className="px-4 py-2 text-left">File</th>
              <th className="px-4 py-2 text-left">Company</th>
              <th className="px-4 py-2 text-left">Status</th>
              <th className="px-4 py-2 text-left">Uploaded</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {reports.map((r) => {
              const isDeleting = deletingId === r.id || (pending && deletingId === r.id);
              return (
                <tr
                  key={r.id}
                  className="border-b border-zinc-100 last:border-0 dark:border-zinc-800"
                >
                  <td
                    className="max-w-[280px] truncate px-4 py-2 font-mono text-xs text-zinc-700 dark:text-zinc-300"
                    title={r.filename}
                  >
                    {r.filename}
                  </td>
                  <td className="px-4 py-2">
                    {r.company ? (
                      <Link
                        href={`/companies/${r.company.id}`}
                        className="text-blue-600 hover:underline dark:text-blue-400"
                      >
                        {r.company.name}
                        {r.company.ticker && (
                          <span className="ml-1 font-mono text-xs text-zinc-500">
                            {r.company.ticker}
                          </span>
                        )}
                      </Link>
                    ) : (
                      <span className="text-zinc-400">—</span>
                    )}
                  </td>
                  <td className="px-4 py-2">
                    <StatusBadge status={r.status} error={r.error_message} />
                  </td>
                  <td className="px-4 py-2 text-xs text-zinc-500">
                    {formatDate(r.upload_date)}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      type="button"
                      onClick={() => onDelete(r)}
                      disabled={isDeleting}
                      className="rounded px-2 py-1 text-xs text-zinc-500 transition-colors hover:bg-red-50 hover:text-red-700 disabled:opacity-50 dark:hover:bg-red-950 dark:hover:text-red-400"
                      aria-label={`Delete ${r.filename}`}
                    >
                      {isDeleting ? "…" : "Remove"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StatusBadge({
  status,
  error,
}: {
  status: Report["status"];
  error: string | null;
}) {
  const styles = {
    completed:
      "bg-green-100 text-green-800 dark:bg-green-950 dark:text-green-300",
    processing:
      "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
    failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  };
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${styles[status]}`}
      title={error ?? undefined}
    >
      {status}
    </span>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return d.toISOString().slice(0, 10);
}
