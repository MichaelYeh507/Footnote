"""The QA surface's orchestration: live retrieval into the measured instrument.

Built 2026-08-23, AFTER Phase 4/5 was published -- this module is product, not
measurement, and four rules keep that boundary hard:

- **The instrument is reused, never restated.** The default `ask` is literally
  `services/qa.py`'s -- the prompt that is byte-checked against the published
  appendix -- and every response carries `INSTRUMENT_SHA256` so the UI can say
  "this answer came from the measured configuration" and mean it. Nothing here
  wraps, prefixes or post-processes what the model is asked.
- **Nothing here is recorded.** Demo questions and answers are written nowhere:
  no file, no table, no log line carrying content. The Phase 4/5 answers file
  is that phase's data, closed on 2026-08-22, and demo usage never becomes a
  reported number.
- **Presentation prose never replaces the verified fields.** The paragraph
  `compose_paragraph` writes (2026-08-24, owner-directed) is a SECOND,
  unmeasured model call over the already-verified answer, quote and
  citation metadata -- composed only when all three verified, guarded so it
  can carry no digit the material did not and must contain the frozen
  answer verbatim, attached by the endpoint after `answer_question`
  returns, and labeled as presentation in the UI. Any failure yields None
  and the terse verified display stands.
- **Where the demo cannot run the published rule, it refuses rather than
  improvising.** The gated arm's threshold `tau(L)` was measured for the
  lexeme counts the frozen query set happened to contain; a fresh question
  with any other count has no threshold, and this module raises with the
  measured sizes named instead of clamping, extrapolating, or silently
  falling back to another arm. The UI shows the refusal; the user picks an
  arm that is defined for their question.

The retrieval path is `services/retrieval.py`'s own functions at their
pre-registered parameters -- the same `or_tsquery`/`sparse_search`/
`dense_search`/`hybrid` the arms ran under, plus `evaluation/gate.py`'s
published gate rule for the fourth arm. The context depth is `CONTEXT_K = 5`,
the one registered cutoff (top-1 is broken by construction; deeper is an
unregistered metric). Quote verification is `evaluation/retrieval_gold.py`'s
published `normalize`/`contains_span`, called, never re-implemented; the
highlight offsets come from a walker that cross-checks itself against that
same `normalize` and returns nothing rather than ever marking a span the
published check would not have matched.
"""

import json
import os
import re

import openai

from evaluation import gate
from evaluation import qa_outcomes
from evaluation import retrieval_gold as gold
from services import qa, retrieval

ARMS = ("sparse", "dense", "hybrid", "gated")

# The registered context depth -- see evaluation/qa_contexts.CONTEXT_K, which
# the eval derived its calls from. Restated here as a literal so the demo and
# the eval drifting apart fails a test rather than a reader.
CONTEXT_K = 5

# A cap on question length, for cost and for the tsquery parser, not a
# measured value. The 65 frozen queries run 44-212 characters; 500 leaves
# room for a wordy question without accepting a pasted document.
MAX_QUESTION_CHARS = 500

EXCERPTS_SQL = """
select chunk_id, accession, ticker, period, item, text
from chunks
where chunk_id = any(%(ids)s)
"""


def load_taus(directory=None) -> dict[int, float]:
    """`L -> tau(L)` from the latest gate-threshold artifact.

    The artifact is `scripts/measure_gate_threshold.py`'s output, kept beside
    the rankings in the data directory, never in the repo. Latest by filename
    stamp, the same convention that script uses to find its rankings.
    """
    if directory is None:
        import corpus_paths
        directory = corpus_paths.retrieval_dir()
    found = sorted(directory.glob("gate-threshold-*.json"))
    if not found:
        raise FileNotFoundError(
            f"no gate-threshold-*.json in {directory}. The gated arm needs "
            f"tau(L) from scripts/measure_gate_threshold.py; without the "
            f"artifact the gate rule cannot be applied to any question.")
    payload = json.loads(found[-1].read_text(encoding="utf-8"))
    # The artifact's `sizes` block maps L to the query ids that had it; the
    # null distributions -- and tau -- live under `null`, keyed by the same L.
    return {int(size): float(entry["tau"])
            for size, entry in payload["null"].items()}


def choose_ranking(arm: str, tsquery, sparse, dense,
                   taus: dict | None = None) -> dict:
    """One question's ranking under one arm, plus the gate record if any.

    `sparse` and `dense` are the (chunk_id, score) lists the searches
    returned. Returns `{"ranking", "gate"}`; `gate` is None except for the
    gated arm, where it records fired/s1/tau/lexemes so the UI can show the
    decision instead of implying one.
    """
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; the arms are {ARMS}")
    sparse_ids = [cid for cid, _ in sparse]
    dense_ids = [cid for cid, _ in dense]
    if arm == "sparse":
        return {"ranking": list(sparse), "gate": None}
    if arm == "dense":
        return {"ranking": list(dense), "gate": None}
    if arm == "hybrid":
        return {"ranking": retrieval.hybrid(sparse_ids, dense_ids),
                "gate": None}

    size = gate.lexeme_count(tsquery)
    if size == 0:
        # No lexemes: the sparse arm returned nothing and tau(0) = 0.0 by the
        # published rule (measure_gate_threshold's own special case) -- the
        # gate fires by definition.
        tau = 0.0
    else:
        if taus is None or size not in taus:
            measured = sorted(taus) if taus else []
            raise LookupError(
                f"the gate's threshold tau(L) was measured for lexeme counts "
                f"{measured} and this question has {size}. The published "
                f"rule is defined only at measured sizes -- defaulting low "
                f"gates nothing, defaulting high gates everything -- so the "
                f"gated arm refuses this question. Ask it under another arm, "
                f"or extend the threshold artifact with "
                f"scripts/measure_gate_threshold.py.")
        tau = float(taus[size])
    s1 = float(sparse[0][1]) if sparse else 0.0
    return {
        "ranking": gate.gated_ranking(sparse_ids, dense_ids, s1, tau),
        "gate": {"fired": gate.gate_fires(s1, tau), "s1": s1, "tau": tau,
                 "lexemes": size},
    }


def top_excerpts(cursor, chunk_ids) -> list[dict]:
    """The chunk records for a ranking's top ids, in rank order.

    `= any(...)` returns rows in whatever order the executor pleases, and the
    excerpts are numbered in rank order -- the numbering the model cites -- so
    the reorder here is load-bearing, not cosmetic.
    """
    ids = list(chunk_ids)
    if not ids:
        return []
    cursor.execute(EXCERPTS_SQL, {"ids": ids})
    by_id = {row[0]: {"chunk_id": row[0], "accession": row[1],
                      "ticker": row[2], "period": row[3], "item": row[4],
                      "text": row[5]}
             for row in cursor.fetchall()}
    missing = [cid for cid in ids if cid not in by_id]
    if missing:
        raise ValueError(
            f"the ranking names {missing}, which the chunks table does not "
            f"hold. The arms and the excerpt fetch read the same table, so "
            f"this means the store changed between the two statements.")
    return [by_id[cid] for cid in ids]


# The folds retrieval_gold declares, restated for the offset walker below.
# Restating them is safe ONLY because `quote_location` verifies its own
# reconstruction against `gold.normalize` on every call and returns None on
# any disagreement -- drift degrades the highlight, never the check.
_FOLDS = {"‘": "'", "’": "'", "“": '"', "”": '"',
          "–": "-", "—": "-"}


def _normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """`gold.normalize(text)` rebuilt character by character, with each
    produced character mapped back to the raw index that produced it."""
    out: list[str] = []
    offsets: list[int] = []
    pending_space_at: int | None = None
    for index, char in enumerate(text):
        folded = _FOLDS.get(char, char)
        if folded.isspace():
            if out and pending_space_at is None:
                pending_space_at = index
            continue
        if pending_space_at is not None:
            out.append(" ")
            offsets.append(pending_space_at)
            pending_space_at = None
        for produced in folded.casefold():
            out.append(produced)
            offsets.append(index)
    return "".join(out), offsets


def quote_location(chunk_text: str, quote) -> tuple[int, int] | None:
    """The raw-text span the quote matched under the published normalize,
    or None.

    Presentation only: the *verdict* on a quote is `gold.contains_span`,
    called by the caller. This finds where to draw the highlight, and it
    refuses to answer (None) rather than mark a span whose reconstruction
    disagrees with the published `normalize` -- so a highlight can never
    show something the check did not match.
    """
    if not isinstance(quote, str) or not isinstance(chunk_text, str):
        return None
    needle = gold.normalize(quote)
    if not needle:
        return None
    haystack, offsets = _normalize_with_offsets(chunk_text)
    if haystack != gold.normalize(chunk_text):
        return None
    at = haystack.find(needle)
    if at == -1:
        return None
    start = offsets[at]
    end = offsets[at + len(needle) - 1] + 1
    return start, end


def answer_question(question: str, arm: str, *, cursor, client,
                    taus: dict | None = None, ask=qa.ask) -> dict:
    """One live question through one arm and the measured instrument.

    `cursor` is a psycopg cursor on the chunk store; `client` an OpenAI
    client for the query embedding (unused by the sparse arm). Returns the
    full record the UI renders; writes nothing anywhere.
    """
    text = str(question or "").strip()
    if not text:
        raise ValueError("question is empty")
    if len(text) > MAX_QUESTION_CHARS:
        raise ValueError(
            f"question is {len(text)} characters; the cap is "
            f"{MAX_QUESTION_CHARS}")
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}; the arms are {ARMS}")

    tsquery = retrieval.or_tsquery(cursor, text)
    sparse = retrieval.sparse_search(cursor, tsquery)
    if arm == "sparse":
        dense = []
    else:
        vector = retrieval.embed_query(client, text)
        dense = retrieval.dense_search(cursor, vector)

    chosen = choose_ranking(arm, tsquery, sparse, dense, taus)
    top_ids = retrieval.ranked_ids(chosen["ranking"])[:CONTEXT_K]
    excerpts = top_excerpts(cursor, top_ids)

    base = {
        "question": text,
        "arm": arm,
        "gate": chosen["gate"],
        "excerpts": [dict(excerpt, n=number)
                     for number, excerpt in enumerate(excerpts, start=1)],
        "model": qa.MODEL,
        "instrument_sha256": qa.INSTRUMENT_SHA256,
        "answer": None,
        "citation": None,
        "quote": None,
        "citation_valid": None,
        "quote_verified": None,
        "highlight": None,
        "malformed_reason": None,
        "raw": None,
        "usage": None,
        "attempts": None,
        "presentation": None,
    }

    if not excerpts:
        # Nothing retrieved (an all-stopword question under the sparse arm).
        # Calling the instrument with an empty context would ask it to decline
        # a question it was shown no excerpts for; the state says what
        # happened instead.
        return dict(base, state="no_passages")

    result = ask(text, excerpts)
    parsed = qa_outcomes.parse_response(result["raw"])
    base.update(raw=result["raw"], usage=result["usage"],
                attempts=result["attempts"])

    if not parsed["ok"]:
        return dict(base, state="malformed",
                    malformed_reason=parsed["reason"])
    if parsed["answer"] is None:
        return dict(base, state="abstained")

    citation = parsed["citation"]
    valid = citation is not None and 1 <= citation <= len(excerpts)
    verified = None
    highlight = None
    if valid:
        cited_text = excerpts[citation - 1]["text"]
        quote = parsed["quote"]
        if isinstance(quote, str) and gold.normalize(quote):
            verified = gold.contains_span(cited_text, quote)
            if verified:
                span = quote_location(cited_text, quote)
                if span is not None:
                    highlight = cited_text[span[0]:span[1]]
        else:
            verified = False
    return dict(base, state="answered", answer=parsed["answer"],
                citation=citation, quote=parsed["quote"],
                citation_valid=valid, quote_verified=verified,
                highlight=highlight)


# --- presentation prose (UNMEASURED; the endpoint attaches it) ----------

# The same model family as the instrument, but this call is presentation,
# not measurement: nothing it produces is scored or recorded, and it is
# not covered by INSTRUMENT_SHA256 -- the UI labels the paragraph as
# presentation and keeps the verified answer, quote and chips in view.
PRESENTATION_MODEL = qa.MODEL
PRESENTATION_MAX_TOKENS = 160

PRESENTATION_PROMPT = (
    "You restate an already-computed answer as one to three plain "
    "sentences, the way an assistant would state it. Use ONLY the material "
    "provided: the question, the answer, the verified quote from the "
    "filing, and the citation line. Include the answer text verbatim "
    "inside your sentences. Do not add any fact, figure, date, unit or "
    "name that does not appear in the material, and do not use outside "
    "knowledge. End your final sentence with the bracketed citation marker "
    "alone (for example: [1]); do not restate the rest of the citation "
    "line after it.")


def _presentation_material(record: dict) -> str:
    """What the composer is shown -- the verified pieces and nothing else.

    Deliberately NOT the excerpt text: prose built from unquoted context
    would put unverified content ahead of the verified quote. The guard
    below derives its allowed digit runs from this same string, so the two
    cannot drift apart.
    """
    citation = record["citation"]
    excerpt = record["excerpts"][citation - 1]
    item = excerpt["item"] or "—"
    return (
        f"Question: {record['question']}\n"
        f"Answer: {record['answer']}\n"
        f"Verified quote from the filing: {record['quote']}\n"
        f"Citation line: [{citation}] {excerpt['ticker']} 10-K, fiscal "
        f"period ending {excerpt['period']}, Item {item}")


def presentation_passes_guard(paragraph, record: dict) -> bool:
    """True only if the paragraph invents nothing the material did not carry.

    Two mechanical checks: every digit run in the paragraph (commas
    stripped) must appear as a run in the material, and the frozen answer
    must appear verbatim under the published `normalize`. Rejection is
    cheap -- the UI falls back to the terse verified display -- so the
    guard errs strict: a paragraph that reformats the answer's own digits
    is dropped rather than trusted.
    """
    try:
        if not isinstance(paragraph, str) or not paragraph.strip():
            return False
        needle = gold.normalize(record["answer"])
        if not needle or needle not in gold.normalize(paragraph):
            return False
        material = _presentation_material(record)
        allowed = set(re.findall(r"\d+", material.replace(",", "")))
        runs = re.findall(r"\d+", paragraph.replace(",", ""))
        return all(run in allowed for run in runs)
    except Exception:
        return False


def compose_paragraph(record: dict, client=None):
    """LLM-style prose over the verified fields, or None.

    Runs ONLY on the fully verified path -- state answered, citation
    valid, quote verbatim-verified -- and never raises: transport
    failures, shape surprises and guard rejections all yield None, and
    the UI falls back to the terse verified display. Called by the
    endpoint AFTER `answer_question` returns; the measured-path
    orchestration never composes prose. Writes nothing anywhere, like
    everything else here.
    """
    try:
        if record.get("state") != "answered":
            return None
        if record.get("citation_valid") is not True:
            return None
        if record.get("quote_verified") is not True:
            return None
        answer = record.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            return None
        material = _presentation_material(record)
        if client is None:
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=PRESENTATION_MODEL,
            temperature=0,
            max_tokens=PRESENTATION_MAX_TOKENS,
            messages=[
                {"role": "system", "content": PRESENTATION_PROMPT},
                {"role": "user", "content": material},
            ])
        paragraph = (response.choices[0].message.content or "").strip()
        if not presentation_passes_guard(paragraph, record):
            return None
        return paragraph
    except Exception:
        return None
