"""Turning one raw QA response into the outcome the appendix pre-registered.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*,
published before any QA output existed. Every judgement here is mechanical;
the one non-mechanical axis -- whether an answer's content is right -- belongs
to the blinded human adjudication and never to this module.

The taxonomy, mutually exclusive, in the order the checks run:

  MALFORMED         not valid JSON, or not the registered schema
  ABSTAINED         `answer` is null -- and nothing else is an abstention;
                    a prose refusal inside a non-null answer is an answered
                    item that asserts nothing, and it scores as UNSUPPORTED
  UNSUPPORTED       answered, but the citation is missing or out of range,
                    the quote is missing or empty, or the quote is not
                    contained in the cited excerpt's text
  SUPPORTED_NONGOLD answered, quote verified, but the cited chunk fails the
                    pre-registered hit test for this query
  SUPPORTED_GOLD    answered, quote verified, cited chunk passes the hit test

Quote containment and the hit test are `evaluation/retrieval_gold.py`'s
published `normalize`/`contains_span`, called -- never re-implemented. Two
implementations of the hit test is the one defect shape that could move every
number in this phase without failing anything.

**The DUPLICATE-SPAN case.** `wrong_filing` marks a SUPPORTED_NONGOLD item
whose cited chunk *contains a gold span* but sits in a non-gold accession --
the right passage from the wrong fiscal year. Gold stays accession-scoped,
exactly as in retrieval, so the item is not gold and not grounded-correct;
but the model was shown each excerpt's fiscal period, so unlike the
retriever's coin flip this is a real error of the QA layer, counted and
reported rather than absorbed or credited.

Schema conformance is over the three registered fields. An extra field
changes no outcome and is not malformed -- the taxonomy depends only on
`answer`, `citation` and `quote` -- but a registered field of the wrong type
is, because a taxonomy that coerced types would be deciding outcomes the
schema already decided.
"""

import json
from enum import Enum

from evaluation import retrieval_gold as gold

REQUIRED_FIELDS = ("answer", "citation", "quote")


class QAOutcome(str, Enum):
    MALFORMED = "malformed"
    ABSTAINED = "abstained"
    UNSUPPORTED = "unsupported"
    SUPPORTED_NONGOLD = "supported_nongold"
    SUPPORTED_GOLD = "supported_gold"


def parse_response(raw: str) -> dict:
    """The registered schema, or the named reason it is not.

    Returns `{"ok": True, "answer", "citation", "quote"}` or
    `{"ok": False, "reason"}`. Booleans are refused where integers are
    required: `True` is an `int` to Python and a nonsense citation to a
    reader, and coercing it would turn a malformed response into excerpt 1.
    """
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"ok": False, "reason": "not valid JSON"}
    if not isinstance(parsed, dict):
        return {"ok": False, "reason": "JSON but not an object"}
    missing = [field for field in REQUIRED_FIELDS if field not in parsed]
    if missing:
        return {"ok": False, "reason": f"missing fields: {missing}"}
    answer, citation, quote = (parsed[field] for field in REQUIRED_FIELDS)
    if answer is not None and not isinstance(answer, str):
        return {"ok": False, "reason": "answer is neither string nor null"}
    if citation is not None and (isinstance(citation, bool)
                                 or not isinstance(citation, int)):
        return {"ok": False, "reason": "citation is neither integer nor null"}
    if quote is not None and not isinstance(quote, str):
        return {"ok": False, "reason": "quote is neither string nor null"}
    return {"ok": True, "answer": answer, "citation": citation,
            "quote": quote}


def classify(parsed: dict, excerpts: list[dict], gold_ids: list[str],
             gold_locations: list[tuple]) -> dict:
    """One row's outcome, from a parsed response and what the model saw.

    `excerpts` is the rendered-order context (`qa_contexts.excerpts_for`),
    `gold_ids` the query's derived gold set (empty for the 15 unanswerable,
    for which SUPPORTED_GOLD is impossible by construction), and
    `gold_locations` its (accession, span) pairs for the wrong-filing check.

    Returns `{"outcome", "cited_chunk_id", "wrong_filing"}`. The citation is
    resolved against the rendered order, because that is the numbering the
    model was shown.
    """
    if not parsed["ok"]:
        return {"outcome": QAOutcome.MALFORMED, "cited_chunk_id": None,
                "wrong_filing": False}
    if parsed["answer"] is None:
        return {"outcome": QAOutcome.ABSTAINED, "cited_chunk_id": None,
                "wrong_filing": False}

    citation = parsed["citation"]
    if citation is None or not 1 <= citation <= len(excerpts):
        return {"outcome": QAOutcome.UNSUPPORTED, "cited_chunk_id": None,
                "wrong_filing": False}
    cited = excerpts[citation - 1]

    quote = parsed["quote"]
    # The empty guard before `contains_span`, which raises on an empty span
    # by design -- an empty quote is an unsupported answer here, not a
    # broken query there.
    if not isinstance(quote, str) or not gold.normalize(quote):
        return {"outcome": QAOutcome.UNSUPPORTED,
                "cited_chunk_id": cited["chunk_id"], "wrong_filing": False}
    if not gold.contains_span(cited["text"], quote):
        return {"outcome": QAOutcome.UNSUPPORTED,
                "cited_chunk_id": cited["chunk_id"], "wrong_filing": False}

    if cited["chunk_id"] in gold_ids:
        return {"outcome": QAOutcome.SUPPORTED_GOLD,
                "cited_chunk_id": cited["chunk_id"], "wrong_filing": False}

    # The appendix's definition, exactly: the cited chunk's text contains a
    # gold span, but its accession is not a gold accession. A chunk *inside*
    # a gold accession that merely fails the span test is the right filing
    # and the wrong chunk -- not this flag.
    gold_accessions = {accession for accession, _ in gold_locations}
    wrong_filing = (
        cited["accession"] not in gold_accessions
        and any(gold.contains_span(cited["text"], span)
                for _, span in gold_locations))
    return {"outcome": QAOutcome.SUPPORTED_NONGOLD,
            "cited_chunk_id": cited["chunk_id"],
            "wrong_filing": wrong_filing}
