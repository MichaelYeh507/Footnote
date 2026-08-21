"""The Phase 3b gate: deciding, per query, whether the sparse arm has a handle.

PRE-REGISTERED 2026-08-20 in `EVALUATION-SPEC.md`, appendix *PHASE 3b, a fourth
arm*, published before this file was written -- and **post-hoc**, which is
stated here because a module docstring is where someone reading the code will
look. The first three arms were pre-registered blind. This one is a response to
a failure mode visible in their published results, evaluated on the same 65
queries. Its output is a hypothesis consistent with the data that suggested it,
never an independent confirmation, and no care taken in this file changes that.

**The failure it targets.** Rank-only fusion cannot distinguish a confident arm
from a dead one. RRF sees ranks and nothing else, so an arm that has located
the answer and an arm that has returned fifty chunks it cannot order cast votes
of identical weight. Measured in Phase 3: on the conceptual stratum the sparse
arm reached the gold chunk for 1 of 25 queries, and in the six queries where
dense hit at k=5 and hybrid did not, the sparse arm had no rank at all for the
gold chunk while the gold chunk fell from dense rank 2-5 to hybrid rank 12-22.

**The rule.** `s1` is the sparse arm's own top `ts_rank_cd` score for a query.
If `s1 > tau(L)` the two arms fuse under the published RRF, bit-identical to
the hybrid arm. If `s1 <= tau(L)` the sparse arm contributes no votes and the
ranking is the dense arm's, unchanged. Binary, one parameter.

**Where tau comes from, and what never enters it.** `tau(L)` is the 95th
percentile, nearest-rank, of the top `ts_rank_cd` scores of 1,000 random
lexeme bags of `L` distinct lexemes drawn from this store's own vocabulary with
probability proportional to document frequency. The gold spans, the gold chunk
sets, every hit and miss, and every recall figure are absent from that
computation by construction: it is a property of the store and the `english`
dictionary, and it could have been run on 2026-08-19 to the same answer. This
is AMENDMENT 4's method -- the gold cap of 5 was fixed by measuring the corpus,
never by watching what it did to a number.

The 95th percentile is not a new choice. Every interval in `RESULTS.md` is
Wilson at 95%, so the gate fires when a query's sparse evidence is not
distinguishable, at the project's own alpha, from a query built out of noise.

**Three traps this module is written around, all silent:**

1. **The bags must not be re-stemmed.** They are lexemes read out of the
   index's own `tsvector` through `ts_stat`, so they are already stemmed.
   `plainto_tsquery` or `to_tsquery` would run the `english` dictionary over
   them a second time and re-parse compound tokens -- `zero-trust` is one
   lexeme in this store and three after a re-parse. `bag_to_tsquery_text`
   therefore produces text to be cast `::tsquery` directly, and nothing here
   ever calls a parser. This is trap 1 of `services/retrieval.py`'s docstring
   in the shape it takes on the way *in*.
2. **The boundary belongs to the gate.** `s1 <= tau` fires. Equality is the
   gated case, so a query whose evidence exactly equals the null's 95th
   percentile is treated as having no handle.
3. **The percentile does not interpolate.** Nearest-rank, so `tau` is a value
   some null draw actually produced rather than a number between two of them.

`L` is the count of *distinct* lexemes: `plainto_tsquery` emits a repeated stem
when the question repeats a word, and sizing the null against a duplicate would
measure a term the arm only matches once.
"""

import math

from services import fusion
from services.retrieval import _TSQUERY_LEXEME

# Reused, not re-implemented. A second regex here would agree with the arm's
# own until the day it did not, and that day would be a silently different `L`
# and therefore a silently different null.
LEXEME_PATTERN = _TSQUERY_LEXEME

# Pre-registered 2026-08-20. See the module docstring before changing either.
NULL_PERCENTILE = 95
NULL_BAGS_PER_SIZE = 1000
NULL_SEED = 20260820

VOCABULARY_SQL = "select word, ndoc from ts_stat($$select tsv from chunks$$)"


def lexemes_of(tsquery_text) -> list[str]:
    """The distinct lexemes of a tsquery, in order of first appearance.

    `None` and empty text give `[]`: a query of nothing but stopwords yields no
    lexemes, the arm returns nothing, and the gate sees `s1 = 0`. That is an
    ordinary outcome and not an error.
    """
    if not tsquery_text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in LEXEME_PATTERN.findall(str(tsquery_text)):
        lexeme = match.replace("''", "'")
        if lexeme not in seen:
            seen.add(lexeme)
            out.append(lexeme)
    return out


def lexeme_count(tsquery_text) -> int:
    """`L` for a query: how many distinct lexemes its OR-tsquery holds."""
    return len(lexemes_of(tsquery_text))


def gate_fires(s1: float, tau: float) -> bool:
    """True when the sparse arm is judged to have no handle on this query.

    `s1 <= tau` fires. The equality case is deliberate and pinned by test:
    evidence exactly at the null's 95th percentile is not evidence.
    """
    return float(s1) <= float(tau)


def nearest_rank_percentile(values, percentile: int = NULL_PERCENTILE) -> float:
    """The nearest-rank percentile: a value that was actually observed.

    Index `ceil(p/100 * n)`, 1-based. The interpolating definition would return
    a number that appeared in no draw, which is a poor thing to publish as a
    threshold measured from a store.
    """
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError(
            "cannot take a percentile of no values. An empty null draw would "
            "give a threshold of 0.0, which gates nothing and would look like "
            "a measurement.")
    rank = math.ceil(percentile / 100.0 * len(ordered))
    return ordered[max(1, rank) - 1]


def weighted_sample_without_replacement(rng, words, weights, k: int):
    """`k` distinct lexemes, drawn one at a time with probability proportional
    to weight, repeats rejected.

    Without replacement because `L` counts distinct lexemes; a bag holding the
    same stem twice would size the null against a term the arm matches once.
    Weighted by document frequency so a null bag has the corpus's own
    commonness profile -- sampling uniformly would build the null out of rare
    terms, which is the direction that makes it artificially easy to clear.
    """
    if k > len(words):
        raise ValueError(
            f"asked for {k} distinct lexemes from a vocabulary of "
            f"{len(words)}.")
    chosen: list[str] = []
    seen: set[str] = set()
    while len(chosen) < k:
        word = rng.choices(words, weights=weights, k=1)[0]
        if word not in seen:
            seen.add(word)
            chosen.append(word)
    return chosen


def bag_to_tsquery_text(bag) -> str:
    """A bag of already-stemmed lexemes as tsquery text, for a direct cast.

    The output is `'a' | 'b' | 'c'`, to be cast `::tsquery`. It must **not** go
    through `plainto_tsquery` or `to_tsquery`: these lexemes came out of the
    index and a second pass through the `english` dictionary would re-parse
    them. See trap 1 in the module docstring.
    """
    bag = list(bag)
    if not bag:
        raise ValueError(
            "an empty bag has no tsquery. Casting empty text gives NULL, the "
            "arm returns nothing, and the draw would score 0.0 for a reason "
            "unrelated to the null.")
    return " | ".join("'" + lexeme.replace("'", "''") + "'" for lexeme in bag)


def store_vocabulary(cursor) -> list[tuple[str, int]]:
    """(lexeme, document frequency) over the whole store, from `ts_stat`.

    Read from the index rather than from the chunk text, so the vocabulary is
    exactly the set of terms the sparse arm can match. A vocabulary of surface
    forms would build a null the arm can never score.
    """
    cursor.execute(VOCABULARY_SQL)
    return [(row[0], row[1]) for row in cursor.fetchall()]


def null_top_score(cursor, bag) -> float:
    """The top `ts_rank_cd` a null bag reaches, through the arm's own search.

    `sparse_search` at its pre-registered depth, so the null is scored by the
    same code path as a real query. 0.0 when the bag matches nothing.
    """
    from services import retrieval

    text = bag_to_tsquery_text(bag)
    cursor.execute("select %s::tsquery::text", (text,))
    tsquery = cursor.fetchone()[0]
    rows = retrieval.sparse_search(cursor, tsquery)
    return float(rows[0][1]) if rows else 0.0


def sparse_top_score(record: dict) -> float:
    """`s1` for one stored ranking record: the sparse arm's best score.

    Read out of the recorded run rather than recomputed, which is what lets the
    fourth arm be built with no database and no API call -- and therefore
    without re-running, and so without any possibility of disturbing, the three
    arms it is compared against.

    An empty sparse list is 0.0 and not an error. A query whose lexemes matched
    nothing is precisely the case the gate exists to catch.
    """
    entries = record["arms"]["sparse"]
    return float(entries[0][1]) if entries else 0.0


def gate_decision(record: dict, taus: dict) -> dict:
    """One query's gate decision and its `gated` ranking.

    `taus` maps `L` to `tau(L)`. A missing `L` raises rather than defaulting:
    defaulting low would gate nothing at that size and defaulting high would
    gate everything, and neither is visible in a published recall figure.
    """
    size = lexeme_count(record.get("tsquery"))
    if size not in taus:
        raise KeyError(
            f"no tau measured for L={size} ({record.get('query_id')}). The "
            f"null is drawn per lexeme count and a missing one cannot be "
            f"substituted: too low gates nothing at that size, too high gates "
            f"everything, and no published number would show either.")
    tau = float(taus[size])
    s1 = sparse_top_score(record)
    sparse_ids = [entry[0] for entry in record["arms"]["sparse"]]
    dense_ids = [entry[0] for entry in record["arms"]["dense"]]
    return {
        "query_id": record.get("query_id"),
        "stratum": record.get("stratum"),
        "lexemes": size,
        "s1": s1,
        "tau": tau,
        "gated": gate_fires(s1, tau),
        "ranking": gated_ranking(sparse_ids, dense_ids, s1, tau),
    }


def gated_ranking(sparse_ids, dense_ids, s1: float, tau: float):
    """One query's `gated` ranking, as (chunk_id, score) pairs.

    Reuses the published RRF rather than introducing new fusion math: not gated
    is `rrf([sparse, dense])` -- bit-identical to the hybrid arm -- and gated is
    `rrf([dense])`, which is the dense order unchanged, since `1/(k + rank)` is
    strictly decreasing in rank and dense ranks do not tie. Scores therefore
    stay in RRF units in both branches, and `k`, the fusion depth and the
    `chunk_id` tie-break are the pre-registered ones in both.
    """
    if gate_fires(s1, tau):
        return fusion.reciprocal_rank_fusion([list(dense_ids)])
    return fusion.reciprocal_rank_fusion([list(sparse_ids), list(dense_ids)])
