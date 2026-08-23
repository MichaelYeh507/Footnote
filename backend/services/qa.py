"""The Phase 4/5 QA instrument: one model, one prompt, one call shape.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*,
published before any QA output existed. The system prompt below is the
appendix's, byte for byte -- `tests/test_qa_instrument.py` extracts the
published block from the spec and asserts equality, so the instrument that
runs is provably the instrument that was registered. Changing a word of it
after the first eval-set call is changing the instrument mid-measurement,
and the appendix's no-relax clause forbids it.

**Why `gpt-4o-mini` and temperature 0.** The same model and posture as the
Phase 2 extractor (`services/openai_structurer.py`): the phase asks whether
grounding changes *that model's* behaviour, and a different model would
confound the answer with a model change. As there, temperature 0 is noise
reduction rather than a determinism guarantee; the stability repeats in
`scripts/run_qa.py` are what stand behind it.

**Retries are transport-only.** A response that arrives and parses is never
re-requested, whatever it says -- re-asking until the answer improves is the
exact dial the one-run clause exists to remove. A response that arrives and
does not parse is scored `malformed` by `evaluation/qa_outcomes.py`, never
retried into compliance.

This module is also the seam the demo reuses: `(question, excerpts) -> raw
response`, with nothing about arms, rankings or scoring in it.
"""

import hashlib
import os
import time

import openai
from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"
TEMPERATURE = 0.0

# Transport-level failures worth retrying; anything else propagates. Rate
# limits are transport here -- the answer was never produced, so retrying one
# cannot re-roll an output.
TRANSPORT_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)
MAX_TRANSPORT_RETRIES = 3

SYSTEM_PROMPT = """You are answering questions about SEC Form 10-K filings. You are given a
question and five numbered excerpts from those filings. Each excerpt is
labeled with its ticker, fiscal period end, and 10-K item number.

Return ONLY valid JSON in exactly this shape. No markdown, no explanation:

{"answer": "string or null", "citation": 1, "quote": "string or null"}

"answer" and "quote" are always JSON strings or null. Write numbers inside
quotes, like "914". "citation" is a bare number from 1 to 5, or null.

RULES

1. Answer ONLY from the excerpts. Never use outside knowledge, and never
   infer, estimate, or calculate a value the excerpts do not state.
2. If the excerpts do not state the answer, return exactly
   {"answer": null, "citation": null, "quote": null}. Declining is the
   correct output whenever the excerpts do not answer the question.
3. null, 0, and a number are three different answers:
     null       the excerpts do not address the question
     0          an excerpt states the amount is zero, or that none occurred
     a number   an excerpt states that number
4. If you answer: "citation" is the number (1-5) of the single excerpt your
   answer rests on, and "quote" is an exact, contiguous span copied character
   for character from that excerpt, stating the answer. Never shorten, merge,
   reword or re-punctuate it: if the supporting sentence says more than you
   need, copy the whole sentence unchanged. A quote that does not appear word
   for word in the cited excerpt counts as no quote at all.
5. Answer the question as asked, including its fiscal year or period if it
   names one. The excerpt labels state each filing's fiscal period end; if
   no excerpt matches the period the question asks about, apply rule 2.
6. Answer in one sentence or less: the fact asked for, with its unit and
   scale as the excerpt states them."""

# sha256 over MODEL | TEMPERATURE | SYSTEM_PROMPT, the Phase 2 pattern
# (`RESULTS.md` pins the extraction instrument the same way). A literal, not a
# computation, so that changing any element of the instrument fails a test
# instead of silently re-deriving its own fingerprint. Recorded in every
# provenance file this phase writes; the controls run and the eval run must
# name the same value.
#
# This is the AMENDED prompt's fingerprint (2026-08-21, one dated amendment,
# two instruction fixes the known-positive control forced: rule 4 rewritten
# after the model paraphrased while claiming to quote, and the field types
# stated after it flapped between "914" and bare 914 across temperature-0
# repeats -- see the appendix's amendment). The earlier fingerprints,
# 1f5dba8e... and 86760a1b..., survive in the kept failed-controls records.
INSTRUMENT_SHA256 = (
    "d40f2ca571301586860d2e74ca5e2f4739fb3265c992b73dc751d9459f1ea068")


def instrument_fingerprint() -> str:
    """The digest INSTRUMENT_SHA256 pins. Kept separate so a test can catch
    the pin and the ingredients drifting apart."""
    material = f"{MODEL}|{TEMPERATURE}|{SYSTEM_PROMPT}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def render_user_message(question: str, excerpts: list[dict]) -> str:
    """The fixed template from the appendix: the question, then the excerpts
    in rank order, each labeled with ticker, fiscal period end and item.

    An empty item is printed as an em dash -- the store leaves `item` blank
    for text before the first detected heading, and the label must still say
    something a reader can point at.
    """
    blocks = [f"Question: {question}"]
    for number, excerpt in enumerate(excerpts, start=1):
        item = excerpt["item"] or "—"
        blocks.append(
            f"[{number}] {excerpt['ticker']} 10-K, fiscal period ending "
            f"{excerpt['period']}, Item {item}:\n{excerpt['text']}")
    return "\n\n".join(blocks)


def ask(question: str, excerpts: list[dict], client=None,
        sleep=time.sleep) -> dict:
    """One call: the raw response string plus what the record needs.

    Returns `{"raw", "attempts", "usage"}`. `raw` is the content exactly as
    returned (empty string for a null content field); parsing and every
    outcome judgement belong to `evaluation/qa_outcomes.py`, so the runner
    stores what happened and the scorer decides what it means.
    """
    if client is None:
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    message = render_user_message(question, excerpts)
    attempts = 0
    while True:
        attempts += 1
        try:
            response = client.chat.completions.create(
                model=MODEL,
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": message},
                ],
            )
            break
        except TRANSPORT_ERRORS:
            if attempts > MAX_TRANSPORT_RETRIES:
                raise
            sleep(2 * attempts)
    usage = getattr(response, "usage", None)
    return {
        "raw": response.choices[0].message.content or "",
        "attempts": attempts,
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        },
    }
