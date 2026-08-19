"""What counts as a hit, and how gold is located in the store.

PRE-REGISTERED 2026-08-19 in `EVALUATION-SPEC.md`, before either index existed
and before any query was written. Everything in this module moves recall@k, so
none of it may be adjusted after a retrieval number exists.

    Gold is a **document location** -- (accession, quoted span) -- never a
    chunk id. A retrieved chunk is a hit if and only if it comes from a gold
    accession and its text contains the gold span under `normalize` below.

Why a location. A chunk id is an artifact of a chunker version, and AMENDMENT 2
is the standing proof that this chunker can change. Freezing gold to chunk ids
would make the query set worthless after any re-chunk, and would additionally
score as a miss a chunker that split the passage differently while still
returning the text. *The honest cost:* because the gold set is derived from the
store at scoring time, recall numbers are not comparable across chunker
versions either. What this buys is that the **query set** survives, not that the
numbers do.

Why `item` is not part of the test. HON's Items 1 and 7 are not separately
labelled -- its document order is not canonical -- and that text is retrievable
and page-cited under `Item 1B`. Requiring item equality would score every query
whose gold sits there as a miss, on account of a chunker limitation this project
has already published as *not* a retrieval failure.

The normalization is the part most able to move a number quietly, so it is
mechanical, short, and fully enumerated: casefold, fold curly quotes to
straight and en/em dashes to hyphen, collapse whitespace runs to one space,
strip. Nothing else. Nothing is removed -- in particular no punctuation, since
`net sales, net` and `net sales net` are different passages in a financial
statement.
"""

import re

# The pre-registered folds, and only these. The counts are measured over the
# 11,621-chunk store and are why each is here rather than in a list of things
# that seemed sensible: em dash 21,839, right single quote 13,603, right double
# 6,265, left double 6,233, en dash 2,372.
#
# Deliberately NOT folded, though present: bullets (6,354) and section marks
# (114). Folding beyond the declared rules changes the measurement as surely as
# folding too little.
_FOLDS = {
    "‘": "'",   # left single quotation mark
    "’": "'",   # right single quotation mark
    "“": '"',   # left double quotation mark
    "”": '"',   # right double quotation mark
    "–": "-",   # en dash
    "—": "-",   # em dash
}
_FOLD_TABLE = str.maketrans(_FOLDS)

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """The declared normalization, applied identically to gold span and chunk.

    Order is fold, then casefold, then collapse, then strip. It does not matter
    for these particular rules -- none of the folds produce whitespace or
    cased characters -- and it is fixed anyway so that two readers of this
    module cannot implement it differently.
    """
    folded = text.translate(_FOLD_TABLE)
    return _WHITESPACE.sub(" ", folded.casefold()).strip()


def contains_span(chunk_text: str, span: str) -> bool:
    """Whether one chunk's text contains one gold span.

    Refuses an empty or whitespace-only span rather than returning True.
    `"" in anything` is True, so an empty gold span would make every chunk a
    hit and every arm score 1.0 -- a query-set defect that would read as a
    perfect retrieval result.
    """
    needle = normalize(span)
    if not needle:
        raise ValueError(
            "gold span is empty after normalization. An empty span matches "
            "every chunk, which would score every arm 1.0."
        )
    return needle in normalize(chunk_text)


def gold_chunk_ids(records: list[dict], accession: str, span: str) -> list[str]:
    """Every chunk of one filing whose text contains the span, in store order.

    Derived at scoring time rather than frozen: with 64 tokens of overlap a
    span near a boundary legitimately sits in two chunks, and recall@k asks
    whether *any* of them reached the reader.

    Scoped to the accession because filings share a great deal of standard
    language. Without the scope, a query about one issuer would be satisfiable
    by another issuer's identical forward-looking-statements notice.
    """
    return [r["chunk_id"] for r in records
            if r["accession"] == accession and contains_span(r["text"], span)]


def gold_chunk_ids_for(records: list[dict],
                       locations: list[tuple[str, str]]) -> list[str]:
    """The union of the gold sets for one query's (accession, span) locations.

    A query may name more than one location -- some questions are legitimately
    answered in two places. Returns store order, without duplicates, so the
    result is deterministic.
    """
    found: list[str] = []
    seen: set[str] = set()
    for accession, span in locations:
        for chunk_id in gold_chunk_ids(records, accession, span):
            if chunk_id not in seen:
                seen.add(chunk_id)
                found.append(chunk_id)
    return found


# AMENDMENT 4, 2026-08-19 -- decided before any query was written, before either
# index existed, and before any retrieval number existed.
#
# The rule above already refused a span matching zero chunks. Measuring that
# rule against the real store showed the same defect from the other end: a
# 4-word span can match 382 chunks, against a median filing of 250, and a query
# with a gold set that size is satisfied by recall@5 essentially by accident. It
# would enter the pooled number as a success, and the failure is silent in the
# direction that flatters the retriever.
#
# Measured, span taken from a real chunk, gold set within its own filing:
#
#   span length   n    median   mean    max    exactly 1
#    4 words      96      2     10.74   382    9/96  = 0.094
#    7 words      96      2      4.55   155    13/96 = 0.135
#   12 words      94      2      2.07    14    17/94 = 0.181
#   20 words      94      2      1.88    14    28/94 = 0.298
#
# The median of 2 is correct and expected: it is the 64-token overlap putting a
# boundary-crossing span in two chunks. A length floor does NOT fix the tail --
# the maximum is still 14 chunks at both 12 and 20 words -- so length is
# guidance and the cap is the guard.
MAX_GOLD_CHUNKS = 5
MIN_SPAN_WORDS = 12


def validate_gold(records: list[dict],
                  locations: list[tuple[str, str]]) -> list[str]:
    """Refusals for one query's gold. An empty list means the query is usable.

    Two refusals, both about the query rather than about any retriever:

      no match   a span matching zero chunks is a broken query, not a
                 retrieval failure
      too many   a span matching more than MAX_GOLD_CHUNKS is boilerplate, and
                 scoring against it measures the corpus's repetition rather
                 than the arm's ranking

    The cap bounds the **union** across a query's locations, because the union
    is what decides how easy recall@k is. A query naming several locations must
    therefore keep its total gold set inside the cap, which in practice means
    two or three.

    Runs against the store, never against retriever output, so it does not
    break the blind under which the query set is written.
    """
    found = gold_chunk_ids_for(records, locations)
    if not found:
        rendered = "; ".join(f"{a}: {s!r}" for a, s in locations)
        return [f"matches no chunk in the store ({rendered}). Quote the span "
                f"from the extracted text, not from a browser rendering."]
    if len(found) > MAX_GOLD_CHUNKS:
        return [f"matches {len(found)} chunks, above the cap of "
                f"{MAX_GOLD_CHUNKS}. The span is boilerplate, and recall@5 "
                f"against a gold set that size is close to free. Quote a "
                f"longer or more distinctive passage."]
    return []


def advisory_notes(locations: list[tuple[str, str]]) -> list[str]:
    """Non-blocking notes. Short spans hit the cap above more often.

    Guidance rather than a refusal, because the measurement showed length does
    not prevent the defect on its own -- a 20-word span still reached 14
    chunks. What decides is `validate_gold`.
    """
    notes = []
    for accession, span in locations:
        words = len(span.split())
        if words < MIN_SPAN_WORDS:
            notes.append(
                f"{accession}: span is {words} words, under the "
                f"{MIN_SPAN_WORDS}-word guideline. Short spans match "
                f"boilerplate more often."
            )
    return notes


def hit_at_k(ranked_ids: list[str], gold_ids: list[str], k: int) -> bool:
    """Whether at least one gold chunk appears in the arm's top k.

    Stated precisely, because the name is loose: every answerable query has at
    least one gold chunk, so this is a **hit rate at k**. It is reported as
    recall@k because that is the term the literature uses for this quantity.

    Refuses an empty gold set rather than scoring it a miss. A query with no
    gold is broken, and scoring it 0 would fold a query-set defect into the
    retrieval number -- which is exactly what the pre-registered validation
    guard exists to keep out. An empty *ranking*, by contrast, is an ordinary
    outcome: a sparse query can legitimately match nothing.
    """
    if not gold_ids:
        raise ValueError(
            "no gold chunks for this query. A span matching zero chunks is a "
            "broken query, not a retrieval failure; fix the query set rather "
            "than scoring it a miss."
        )
    return any(chunk_id in gold_ids for chunk_id in ranked_ids[:k])
