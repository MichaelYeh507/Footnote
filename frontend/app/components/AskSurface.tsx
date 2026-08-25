"use client";

import { useEffect, useRef, useState } from "react";
import {
  ARMS,
  DEFAULT_ARM,
  QaError,
  askQuestion,
  type Arm,
  type QaExcerpt,
  type QaResponse,
} from "../lib/qa";

type Phase =
  | { kind: "idle" }
  | { kind: "loading"; question: string }
  | { kind: "result"; res: QaResponse; nonce: number }
  | { kind: "refused"; detail: string }
  | { kind: "failed"; detail: string };

/** The display face; small functional labels stay on the sans. */
const SERIF = "font-[family-name:var(--font-serif)]";

/**
 * Streamed reveal for the response text, paced like an LLM answering:
 * word-sized bursts (occasionally two) on a slightly irregular 90–200ms
 * cadence, ~8 words a second. A reveal cadence over the already-returned
 * text, not a transport claim. Instant under prefers-reduced-motion,
 * matching the backdrop's rule.
 */
function useTyped(text: string) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const reduced =
      window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    if (reduced || !text) {
      setCount(text.length);
      return;
    }
    setCount(0);
    // Cumulative char offsets at each word boundary (trailing space kept
    // with its word so the reveal never ends mid-gap).
    const stops: number[] = [];
    const words = /\S+\s*/g;
    let match;
    while ((match = words.exec(text))) {
      stops.push(match.index + match[0].length);
    }
    let at = 0;
    let timer = 0;
    const step = () => {
      at += Math.random() < 0.15 ? 2 : 1;
      if (at >= stops.length) {
        setCount(text.length);
        return;
      }
      setCount(stops[at - 1]);
      timer = window.setTimeout(step, 90 + Math.random() * 110);
    };
    timer = window.setTimeout(step, 150);
    return () => clearTimeout(timer);
  }, [text]);
  return { shown: text.slice(0, count), done: count >= text.length };
}

/** The thin bar at the end of text while it is still typing. */
function Caret() {
  return (
    <span className="ml-1 inline-block h-[0.9em] w-0.5 translate-y-[0.12em] rounded-full bg-zinc-400 align-baseline" />
  );
}

export function AskSurface() {
  const [question, setQuestion] = useState("");
  const [arm, setArm] = useState<Arm>(DEFAULT_ARM);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [showNumbers, setShowNumbers] = useState(false);
  const [armOpen, setArmOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const busy = phase.kind === "loading";
  const atHero = phase.kind === "idle";

  async function submit(text: string, chosen: Arm = arm) {
    const q = text.trim();
    if (!q || busy) return;
    setPhase({ kind: "loading", question: q });
    try {
      const res = await askQuestion(q, chosen);
      setPhase({ kind: "result", res, nonce: Date.now() });
    } catch (e) {
      if (e instanceof QaError && e.status === 422) {
        setPhase({ kind: "refused", detail: e.message });
      } else {
        setPhase({
          kind: "failed",
          detail: e instanceof Error ? e.message : "Unknown error",
        });
      }
    }
  }

  function reset() {
    setQuestion("");
    setPhase({ kind: "idle" });
    inputRef.current?.focus();
  }

  return (
    <div className="relative z-10 flex flex-1 flex-col px-6 md:px-24">
      <div
        className={`flex flex-1 flex-col ${
          atHero ? "justify-center" : "justify-start pt-14 md:pt-16"
        }`}
      >
      {/* The ask form: a bare line of text, no box. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit(question);
        }}
        className="mx-auto w-full max-w-4xl"
      >
        <input
          ref={inputRef}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask the filings"
          maxLength={500}
          disabled={busy}
          autoFocus
          className={`${SERIF} w-full bg-transparent text-center font-light tracking-tight text-zinc-700 caret-blue-600 outline-none transition-all duration-500 placeholder:text-zinc-400/70 disabled:opacity-60 ${
            atHero ? "text-4xl md:text-6xl" : "text-lg md:text-xl"
          }`}
        />

        {/* The one control: which arm answers. Denominators live in the
            popover, not on the hero. */}
        <div className="relative mt-5 flex justify-center">
          <button
            type="button"
            onClick={() => setArmOpen((v) => !v)}
            className="text-xs text-zinc-400 transition-colors hover:text-zinc-600"
          >
            {arm}
            {arm === "gated" ? " ‡" : ""} ▾
          </button>
          {armOpen && (
            <div className="absolute top-7 z-30 w-64 rounded-xl border border-zinc-200 bg-white p-2 shadow-lg">
              {ARMS.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  title={a.interval}
                  onClick={() => {
                    setArm(a.id);
                    setArmOpen(false);
                  }}
                  className={`flex w-full items-baseline justify-between rounded-lg px-3 py-2 text-left text-xs transition-colors ${
                    arm === a.id
                      ? "bg-zinc-100 text-zinc-900"
                      : "text-zinc-600 hover:bg-zinc-50"
                  }`}
                >
                  <span>
                    {a.label}
                    {a.postHoc ? " ‡" : ""}
                  </span>
                  <span className="text-zinc-400">{a.recall}</span>
                </button>
              ))}
              <p className="px-3 pb-1 pt-2 text-[10px] leading-relaxed text-zinc-400">
                recall@5 over the 50 answerable queries · ‡ post-hoc arm
              </p>
            </div>
          )}
        </div>
      </form>

      {/* Loading — the owner prefers the plain pipeline line here. */}
      {phase.kind === "loading" && (
        <p className="mx-auto mt-14 animate-pulse text-sm text-zinc-400">
          retrieving passages → asking gpt-4o-mini…
        </p>
      )}

      {/* Refused (the gated arm's tau(L) refusal, or another validation stop) */}
      {phase.kind === "refused" && (
        <div className="mx-auto mt-12 w-full max-w-2xl rounded-2xl border border-zinc-200 bg-white/70 p-6">
          <p className={`${SERIF} text-lg font-light text-zinc-700`}>
            This arm can&apos;t take this question.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-zinc-500">
            {phase.detail}
          </p>
          <button
            type="button"
            onClick={reset}
            className="mt-4 text-sm text-zinc-500 underline-offset-4 hover:underline"
          >
            ask something else
          </button>
        </div>
      )}

      {/* Failed (backend down, model call failed) */}
      {phase.kind === "failed" && (
        <div className="mx-auto mt-12 w-full max-w-2xl rounded-2xl border border-red-200 bg-red-50/70 p-6">
          <p className="text-sm text-red-800">{phase.detail}</p>
          <button
            type="button"
            onClick={reset}
            className="mt-4 text-sm text-red-700 underline-offset-4 hover:underline"
          >
            start over
          </button>
        </div>
      )}

      {phase.kind === "result" && (
        <Result key={phase.nonce} res={phase.res} onReset={reset} />
      )}
      </div>

      {/* Footer: one quiet link; everything it explains lives inside. */}
      <footer className="mx-auto w-full max-w-4xl pb-5 pt-6 text-center">
        <button
          type="button"
          onClick={() => setShowNumbers((v) => !v)}
          className="text-[11px] text-zinc-400 underline-offset-4 hover:text-zinc-600 hover:underline"
        >
          measured numbers
        </button>
        {showNumbers && <MeasuredNumbers />}
      </footer>
    </div>
  );
}

/** The published Phase 4/5 table, restated with denominators. Source:
 *  RESULTS.md — never recomputed here. */
function MeasuredNumbers() {
  const rows = [
    ["sparse", "15/15", "5/10 †", "5/50", "10/50"],
    ["dense", "15/15", "4/22 †", "4/50", "22/50"],
    ["hybrid", "15/15", "6/18 †", "6/50", "18/50"],
    ["gated ‡", "15/15", "5/25", "5/50", "25/50"],
  ];
  return (
    <div className="mx-auto mt-3 max-w-2xl overflow-x-auto rounded-2xl border border-zinc-200 bg-white/80 p-4 text-left">
      <table className="w-full text-[11px] text-zinc-600">
        <thead>
          <tr className="text-zinc-400">
            <th className="pb-2 pr-3 text-left font-normal">arm</th>
            <th className="pb-2 pr-3 text-left font-normal">
              abstained on 15 unanswerable
            </th>
            <th className="pb-2 pr-3 text-left font-normal">
              grounded-correct given gold in top-5
            </th>
            <th className="pb-2 pr-3 text-left font-normal">
              end-to-end (50 answerable)
            </th>
            <th className="pb-2 text-left font-normal">recall@5 ceiling</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r[0]} className="border-t border-zinc-100">
              <td className="py-1.5 pr-3">{r[0]}</td>
              <td className="py-1.5 pr-3">{r[1]}</td>
              <td className="py-1.5 pr-3">{r[2]}</td>
              <td className="py-1.5 pr-3">{r[3]}</td>
              <td className="py-1.5">{r[4]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-3 text-[10px] leading-relaxed text-zinc-400">
        Every arm declined all 15 unanswerable questions — 60 of 60
        opportunities to invent, declined. † below the n ≥ 25 reporting gate;
        counts, not rates. ‡ the gated arm is post-hoc: designed after the
        first three arms&apos; results, on the same 65 queries. No direction is
        established between any arms on grounded accuracy (all six paired
        intervals straddle 0.5). Full tables with intervals: RESULTS.md.
      </p>
      <p className="mt-2 border-t border-zinc-100 pt-2 text-[10px] leading-relaxed text-zinc-400">
        Grounded in 44 SEC 10-K filings · 22 issuers · two fiscal years ·
        gpt-4o-mini at temperature 0, the measured configuration · live
        answers are demonstrations, not measurements.
      </p>
    </div>
  );
}

function Result({ res, onReset }: { res: QaResponse; onReset: () => void }) {
  const excerptRefs = useRef<(HTMLDivElement | null)[]>([]);
  const cited =
    res.state === "answered" && res.citation_valid && res.citation !== null
      ? res.citation
      : null;

  // The one line that types; everything beneath fades in once it lands.
  const primaryText =
    res.state === "answered"
      ? (res.presentation ?? res.answer ?? "")
      : res.state === "abstained"
        ? "The corpus does not support an answer to this question."
        : "";
  const { shown: typedText, done: typedDone } = useTyped(primaryText);
  const AFTER = `transition-opacity duration-500 ${
    typedDone ? "opacity-100" : "opacity-0"
  }`;

  // Collapsed by default (owner-decided 2026-08-25): provenance stays one
  // click away, never removed. The cited link opens it before scrolling.
  const [showExcerpts, setShowExcerpts] = useState(false);

  function scrollToCited() {
    if (cited === null) return;
    setShowExcerpts(true);
    requestAnimationFrame(() =>
      requestAnimationFrame(() =>
        excerptRefs.current[cited - 1]?.scrollIntoView({
          behavior: "smooth",
          block: "center",
        }),
      ),
    );
  }

  return (
    <div className="mx-auto mt-12 w-full max-w-3xl">
      {/* The verdict block */}
      {res.state === "answered" && (
        <div>
          {res.presentation ? (
            <>
              <p
                className={`${SERIF} max-w-2xl text-lg font-light leading-relaxed text-zinc-800 md:text-xl`}
              >
                {typedText}
                {!typedDone && <Caret />}
              </p>
              <p
                className={`mt-2 text-[10px] tracking-wide text-zinc-400 ${AFTER}`}
              >
                presentation prose over the verified answer — the verbatim
                quote is highlighted in the cited excerpt below
              </p>
            </>
          ) : (
            <p
              className={`${SERIF} text-2xl font-light leading-snug text-zinc-800 md:text-3xl`}
            >
              {typedText}
              {!typedDone && <Caret />}
            </p>
          )}
          <div
            className={`mt-5 flex flex-wrap items-baseline gap-x-6 gap-y-1.5 text-xs ${AFTER}`}
          >
            {cited !== null ? (
              <button
                type="button"
                onClick={scrollToCited}
                className="text-zinc-500 underline decoration-zinc-300 underline-offset-4 transition-colors hover:text-zinc-800 hover:decoration-zinc-500"
              >
                cited: [{cited}] {res.excerpts[cited - 1]?.ticker} 10-K · FY
                end {res.excerpts[cited - 1]?.period}
              </button>
            ) : (
              <span className="text-amber-700">
                ⚠ cited excerpt [{String(res.citation)}] does not exist (1–
                {res.excerpts.length})
              </span>
            )}
            {res.quote_verified === true && (
              <span className="text-emerald-700">
                ✓ verbatim quote verified in the cited excerpt
              </span>
            )}
            {res.quote_verified === false && (
              <span className="text-amber-700">
                ⚠ the model&apos;s quote was not found verbatim in the cited
                excerpt
              </span>
            )}
          </div>
          {res.quote_verified === false && res.quote && (
            <blockquote
              className={`${SERIF} mt-3 border-l-2 border-amber-300 pl-3 text-sm italic text-zinc-500 ${AFTER}`}
            >
              claimed quote: “{res.quote}”
            </blockquote>
          )}
        </div>
      )}

      {res.state === "abstained" && (
        <div>
          <p
            className={`${SERIF} text-2xl font-light leading-snug text-zinc-700 md:text-3xl`}
          >
            {typedText}
            {!typedDone && <Caret />}
          </p>
          <p
            className={`mt-4 text-xs uppercase tracking-widest text-zinc-400 ${AFTER}`}
          >
            abstained — by design
          </p>
          <p
            className={`mt-3 max-w-2xl text-sm leading-relaxed text-zinc-500 ${AFTER}`}
          >
            The model reviewed the five retrieved passages below and returned
            the registered abstention rather than inventing an answer. In
            measurement, this configuration declined all 60 of 60 unanswerable
            questions (15/15 in each of the four arms).
          </p>
        </div>
      )}

      {res.state === "malformed" && (
        <div className="rounded-2xl border border-amber-300 bg-amber-50/70 p-6">
          <p className={`${SERIF} text-lg font-light text-zinc-800`}>
            The model&apos;s response did not match the registered schema.
          </p>
          <p className="mt-2 text-sm text-zinc-600">
            Reason: {res.malformed_reason}. Shown as-is rather than repaired —
            re-asking until output improves is the dial the measurement
            removed.
          </p>
          {res.raw && (
            <details className="mt-3 text-xs text-zinc-500">
              <summary className="cursor-pointer">raw response</summary>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-white/70 p-3">
                {res.raw}
              </pre>
            </details>
          )}
        </div>
      )}

      {res.state === "no_passages" && (
        <div className="rounded-2xl border border-zinc-200 bg-white/70 p-6">
          <p className={`${SERIF} text-lg font-light text-zinc-700`}>
            Retrieval found no passages for this question.
          </p>
          <p className="mt-2 text-sm leading-relaxed text-zinc-500">
            No term of the question matches the sparse index, so there was
            nothing to hand the model and it was not called.
          </p>
        </div>
      )}

      {/* The gate's decision, when the gated arm ran. */}
      {res.gate && (
        <p className={`mt-4 text-xs text-zinc-400 ${AFTER}`}>
          {res.gate.fired ? (
            <>
              gate fired: s1 {res.gate.s1.toFixed(2)} ≤ τ{" "}
              {res.gate.tau.toFixed(2)} at L={res.gate.lexemes} — the sparse
              arm contributed no votes; this ranking is the dense arm&apos;s
            </>
          ) : (
            <>
              gate did not fire: s1 {res.gate.s1.toFixed(2)} &gt; τ{" "}
              {res.gate.tau.toFixed(2)} at L={res.gate.lexemes} — published
              hybrid fusion of both arms
            </>
          )}
        </p>
      )}

      {/* Provenance: the five passages, rank order, cited one marked. */}
      {res.excerpts.length > 0 && (
        <div className={`mt-8 ${AFTER}`}>
          <button
            type="button"
            onClick={() => setShowExcerpts((v) => !v)}
            className="mb-3 text-xs uppercase tracking-widest text-zinc-400 transition-colors hover:text-zinc-600"
          >
            {res.state === "abstained"
              ? "what it declined over"
              : "what the model saw"}{" "}
            · top {res.excerpts.length} · {res.arm} arm{" "}
            {showExcerpts ? "▾" : "▸"}
          </button>
          {showExcerpts && (
            <div className="flex flex-col gap-3">
              {res.excerpts.map((ex) => (
                <Excerpt
                  key={ex.n}
                  excerpt={ex}
                  cited={cited === ex.n}
                  highlight={cited === ex.n ? res.highlight : null}
                  ref={(el) => {
                    excerptRefs.current[ex.n - 1] = el;
                  }}
                />
              ))}
            </div>
          )}
        </div>
      )}

      <div
        className={`mt-8 flex items-center justify-between text-[11px] text-zinc-400 ${AFTER}`}
      >
        <span>
          instrument {res.instrument_sha256.slice(0, 8)} · {res.model} · temp 0
          {res.usage?.prompt_tokens != null &&
            ` · ${res.usage.prompt_tokens.toLocaleString()} prompt + ${
              res.usage.completion_tokens ?? 0
            } completion tokens`}
        </span>
        <button
          type="button"
          onClick={onReset}
          className="text-zinc-500 underline-offset-4 hover:underline"
        >
          ask another
        </button>
      </div>
    </div>
  );
}

function Excerpt({
  excerpt,
  cited,
  highlight,
  ref,
}: {
  excerpt: QaExcerpt;
  cited: boolean;
  highlight: string | null;
  ref: (el: HTMLDivElement | null) => void;
}) {
  const [expanded, setExpanded] = useState(cited);

  const text = excerpt.text;
  let body: React.ReactNode = text;
  if (highlight) {
    const at = text.indexOf(highlight);
    if (at !== -1) {
      body = (
        <>
          {text.slice(0, at)}
          <mark className="rounded-sm bg-amber-200/80 px-0.5 text-zinc-900">
            {text.slice(at, at + highlight.length)}
          </mark>
          {text.slice(at + highlight.length)}
        </>
      );
    }
  }

  return (
    <div
      ref={ref}
      className={`rounded-2xl border p-5 transition-colors ${
        cited
          ? "border-zinc-300 bg-white/85 shadow-sm"
          : "border-zinc-200/70 bg-white/55"
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="text-[11px] uppercase tracking-widest text-zinc-400">
          [{excerpt.n}] {excerpt.ticker} 10-K · FY end {excerpt.period} ·
          Item {excerpt.item || "—"}
          {cited && <span className="ml-3 text-blue-700">cited</span>}
        </p>
        <p className="text-[10px] text-zinc-400/80">{excerpt.accession}</p>
      </div>
      <div
        className={`${SERIF} mt-3 whitespace-pre-wrap text-[15px] font-light leading-relaxed text-zinc-700 ${
          expanded ? "" : "line-clamp-6"
        }`}
      >
        {body}
      </div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="mt-2 text-[11px] text-zinc-400 underline-offset-4 hover:text-zinc-600 hover:underline"
      >
        {expanded ? "collapse" : "show full passage"}
      </button>
    </div>
  );
}
