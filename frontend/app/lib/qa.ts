import { API_URL } from "./api";

/** Mirrors backend services/qa_demo.py `answer_question`. */
export type Arm = "sparse" | "dense" | "hybrid" | "gated";

export type QaExcerpt = {
  n: number;
  chunk_id: string;
  accession: string;
  ticker: string;
  period: string;
  item: string;
  text: string;
};

export type QaGate = {
  fired: boolean;
  s1: number;
  tau: number;
  lexemes: number;
};

export type QaResponse = {
  question: string;
  arm: Arm;
  state: "answered" | "abstained" | "malformed" | "no_passages";
  answer: string | null;
  /**
   * LLM-style prose over the verified fields, from a second UNMEASURED
   * model call — composed only when the citation is valid and the quote
   * verbatim-verified, guarded against carrying any digit the verified
   * material did not, null whenever composition was skipped or declined.
   * The UI labels it as presentation; `answer` stays the frozen
   * instrument's own words.
   */
  presentation: string | null;
  citation: number | null;
  quote: string | null;
  citation_valid: boolean | null;
  quote_verified: boolean | null;
  /** The exact raw span of the cited excerpt the quote matched, for marking. */
  highlight: string | null;
  malformed_reason: string | null;
  raw: string | null;
  gate: QaGate | null;
  excerpts: QaExcerpt[];
  model: string;
  instrument_sha256: string;
  usage: { prompt_tokens: number | null; completion_tokens: number | null } | null;
  attempts: number | null;
};

export class QaError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

export async function askQuestion(
  question: string,
  arm: Arm,
): Promise<QaResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/qa`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, arm }),
    });
  } catch {
    throw new QaError(
      0,
      `The backend is not reachable at ${API_URL}. Start it with uvicorn and reload.`,
    );
  }
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    throw new QaError(res.status, `The backend returned no JSON (${res.status}).`);
  }
  if (!res.ok) {
    const detail =
      typeof data === "object" && data !== null && "detail" in data
        ? String((data as { detail: unknown }).detail)
        : `Request failed (${res.status})`;
    throw new QaError(res.status, detail);
  }
  return data as QaResponse;
}

/**
 * Published Phase 3/3b recall@5, restated for the arm picker. Source:
 * RESULTS.md — never recomputed here. Denominator is the 50 answerable
 * queries; the gated arm is post-hoc (designed after the first three arms'
 * results, on the same query set) and is labeled so wherever its number
 * appears.
 */
export const ARMS: {
  id: Arm;
  label: string;
  recall: string;
  interval: string;
  postHoc: boolean;
}[] = [
  {
    id: "sparse",
    label: "sparse",
    recall: "10/50",
    interval: "recall@5 0.200 [0.112, 0.330]",
    postHoc: false,
  },
  {
    id: "dense",
    label: "dense",
    recall: "22/50",
    interval: "recall@5 0.440 [0.312, 0.577]",
    postHoc: false,
  },
  {
    id: "hybrid",
    label: "hybrid",
    recall: "18/50",
    interval: "recall@5 0.360 [0.241, 0.499]",
    postHoc: false,
  },
  {
    id: "gated",
    label: "gated",
    recall: "25/50",
    interval: "recall@5 0.500 [0.366, 0.634] — post-hoc arm",
    postHoc: true,
  },
];

/**
 * Owner-decided 2026-08-23: dense. A free product choice — Phase 4/5
 * established no direction between arms on grounded accuracy — made citing
 * that fact: dense is the best blind pre-registered recall@5 (22/50) and,
 * unlike gated (25/50, post-hoc), is defined for every question (the gate's
 * tau(L) exists only for measured lexeme counts, so short questions would
 * get a refusal instead of an answer).
 */
export const DEFAULT_ARM: Arm = "dense";

/**
 * Example questions. Written fresh for the demo — none of these is from the
 * frozen 65-query evaluation set, and nothing asked here is measured or
 * recorded. The third is deliberately outside the corpus (Apple is a
 * calibration issuer, excluded by rule): the honest outcome is a decline.
 */
export const EXAMPLES: { question: string; gradient: string }[] = [
  {
    question:
      "How many stores did Domino's Pizza operate worldwide at fiscal year end?",
    gradient: "from-stone-500 to-stone-700",
  },
  {
    question: "How many hotel rooms does Wynn Las Vegas have?",
    gradient: "from-slate-500 to-slate-700",
  },
  {
    question: "What was Apple's total revenue in fiscal 2025?",
    gradient: "from-teal-700 to-teal-900",
  },
];
