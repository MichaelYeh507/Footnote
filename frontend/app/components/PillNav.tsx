import Link from "next/link";

/**
 * The floating pill sidebar from the reference design. Three destinations,
 * all real — no decorative icons.
 */
export function PillNav({ active }: { active: "ask" | "extractions" | "upload" }) {
  const base =
    "flex h-9 w-9 items-center justify-center rounded-full transition-colors";
  const on = "bg-zinc-900 text-white";
  const off = "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800";
  return (
    <div className="absolute left-5 top-5 z-20 hidden flex-col items-center gap-4 md:flex">
      <div
        className="flex h-9 w-9 items-center justify-center rounded-full bg-zinc-900 text-lg font-semibold text-white"
        title="SEC 10-K corpus"
      >
        §
      </div>
      <nav className="flex flex-col items-center gap-1 rounded-full border border-zinc-200 bg-white px-1.5 py-2 shadow-sm">
        <Link
          href="/"
          className={`${base} ${active === "ask" ? on : off}`}
          title="Ask the filings"
        >
          <svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M21 11.5a8.38 8.38 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7a8.5 8.5 0 1 1 16.1-3.8z" />
            <path d="M9.7 9.7a2.3 2.3 0 0 1 4.5.7c0 1.5-2.2 2-2.2 2" />
            <line x1="12" y1="16" x2="12" y2="16" />
          </svg>
        </Link>
        <Link
          href="/extractions"
          className={`${base} ${active === "extractions" ? on : off}`}
          title="Extractions"
        >
          <svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <rect x="3" y="3" width="7" height="7" rx="1.5" />
            <rect x="14" y="3" width="7" height="7" rx="1.5" />
            <rect x="3" y="14" width="7" height="7" rx="1.5" />
            <rect x="14" y="14" width="7" height="7" rx="1.5" />
          </svg>
        </Link>
        <Link
          href="/upload"
          className={`${base} ${active === "upload" ? on : off}`}
          title="Upload a filing"
        >
          <svg
            width="17"
            height="17"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 16V4" />
            <path d="m6 10 6-6 6 6" />
            <path d="M4 20h16" />
          </svg>
        </Link>
      </nav>
    </div>
  );
}
