"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import { getReport, uploadPdf, type Report } from "../lib/api";

type Phase =
  | { kind: "idle" }
  | { kind: "uploading" }
  | { kind: "processing"; reportId: string; elapsed: number }
  | { kind: "error"; message: string };

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 180_000; // 3 minutes

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const cancelledRef = useRef(false);

  useEffect(() => {
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const handleFile = (f: File | null) => {
    setPhase({ kind: "idle" });
    if (f && !f.name.toLowerCase().endsWith(".pdf")) {
      setPhase({ kind: "error", message: "Only PDF files are accepted." });
      setFile(null);
      return;
    }
    setFile(f);
  };

  const onDrop = useCallback((e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setDragging(false);
    handleFile(e.dataTransfer.files?.[0] ?? null);
  }, []);

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    handleFile(e.target.files?.[0] ?? null);
  };

  const pollUntilDone = async (reportId: string) => {
    const start = Date.now();
    while (!cancelledRef.current) {
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
      if (cancelledRef.current) return;

      let report: Report;
      try {
        report = await getReport(reportId);
      } catch (e) {
        setPhase({
          kind: "error",
          message: e instanceof Error ? e.message : "Polling failed",
        });
        return;
      }

      if (report.status === "completed" && report.company) {
        router.push(`/companies/${report.company.id}`);
        return;
      }
      if (report.status === "failed") {
        setPhase({
          kind: "error",
          message: report.error_message ?? "Processing failed",
        });
        return;
      }

      const elapsed = Math.floor((Date.now() - start) / 1000);
      setPhase({ kind: "processing", reportId, elapsed });

      if (Date.now() - start > POLL_TIMEOUT_MS) {
        setPhase({
          kind: "error",
          message:
            "Processing is taking longer than expected. Check the dashboard shortly.",
        });
        return;
      }
    }
  };

  const onSubmit = async () => {
    if (!file) return;
    setPhase({ kind: "uploading" });
    try {
      const { report_id } = await uploadPdf(file);
      setPhase({ kind: "processing", reportId: report_id, elapsed: 0 });
      await pollUntilDone(report_id);
    } catch (e) {
      setPhase({
        kind: "error",
        message: e instanceof Error ? e.message : "Upload failed",
      });
    }
  };

  const busy = phase.kind === "uploading" || phase.kind === "processing";

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
        </div>
      </header>

      <main className="mx-auto flex w-full max-w-2xl flex-1 flex-col px-6 py-16">
        <h1 className="mb-2 text-3xl font-semibold text-zinc-900 dark:text-zinc-50">
          Upload Report
        </h1>
        <p className="mb-8 text-zinc-500 dark:text-zinc-400">
          Drop a PDF investment research report. We&apos;ll extract and
          structure the data automatically.
        </p>

        <label
          htmlFor="file-input"
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed p-12 transition-colors ${
            dragging
              ? "border-blue-500 bg-blue-50 dark:bg-blue-950/30"
              : "border-zinc-300 bg-white hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:hover:border-zinc-500"
          }`}
        >
          <svg
            className="mb-4 h-12 w-12 text-zinc-400"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M7 16a4 4 0 01-.88-7.9A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
            />
          </svg>
          {file ? (
            <>
              <p className="font-medium text-zinc-900 dark:text-zinc-50">
                {file.name}
              </p>
              <p className="mt-1 text-xs text-zinc-500">
                {(file.size / 1024).toFixed(1)} KB · Click to change
              </p>
            </>
          ) : (
            <>
              <p className="font-medium text-zinc-700 dark:text-zinc-200">
                Drop PDF here or click to browse
              </p>
              <p className="mt-1 text-xs text-zinc-500">PDF files only</p>
            </>
          )}
          <input
            id="file-input"
            type="file"
            accept=".pdf,application/pdf"
            onChange={onChange}
            className="hidden"
            disabled={busy}
          />
        </label>

        {phase.kind === "error" && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
            <div className="font-medium">Processing failed</div>
            <div className="mt-1 break-words">{phase.message}</div>
          </div>
        )}

        {phase.kind === "processing" && (
          <div className="mt-4 rounded-lg border border-blue-200 bg-blue-50 p-4 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
            <div className="flex items-center gap-2">
              <Spinner />
              <span className="font-medium">
                Processing… {phase.elapsed}s elapsed
              </span>
            </div>
            <div className="mt-1 text-xs opacity-75">
              Extracting text → sending to OpenAI → saving to Supabase
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={onSubmit}
          disabled={!file || busy}
          className="mt-6 rounded-lg bg-zinc-900 px-6 py-3 font-medium text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-zinc-50 dark:text-zinc-900 dark:hover:bg-zinc-200"
        >
          {phase.kind === "uploading"
            ? "Uploading…"
            : phase.kind === "processing"
              ? "Processing…"
              : "Process PDF"}
        </button>
      </main>
    </div>
  );
}

function Spinner() {
  return (
    <svg
      className="h-4 w-4 animate-spin"
      viewBox="0 0 24 24"
      fill="none"
    >
      <circle
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
        opacity="0.25"
      />
      <path
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.4 0 0 5.4 0 12h4zm2 5.3A8 8 0 014 12H0c0 3 1.1 5.8 3 7.9l3-2.6z"
      />
    </svg>
  );
}
