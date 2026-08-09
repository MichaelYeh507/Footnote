"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { deleteExtraction } from "../lib/api";

export function DeleteExtractionButton({
  extractionId,
  companyName,
}: {
  extractionId: string;
  companyName: string;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onClick = async () => {
    if (!confirm(`Delete the extraction for ${companyName}? This cannot be undone.`))
      return;
    setLoading(true);
    setError(null);
    try {
      await deleteExtraction(extractionId);
      router.push("/");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
      setLoading(false);
    }
  };

  return (
    <>
      <button
        type="button"
        onClick={onClick}
        disabled={loading}
        className="rounded-md border border-red-300 bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:bg-zinc-900 dark:text-red-400 dark:hover:bg-red-950"
      >
        {loading ? "Deleting…" : "Delete"}
      </button>
      {error && (
        <span className="ml-2 text-xs text-red-600 dark:text-red-400">
          {error}
        </span>
      )}
    </>
  );
}
