"""Turning ranked lists into the numbers section 5 pre-registered.

**Written before any arm ran.** That ordering is the point, and it is the same
argument that put `evaluation/query_set.py` before the first query: scoring code
written while the rankings are on screen gets shaped by them one judgement call
at a time -- which chunk really counts, which query was unfair, whether an empty
ranking is a miss or an exclusion -- and none of it would be visible to a reader
of the result.

Nothing here defines what a hit is. That is `evaluation/retrieval_gold.py`,
pre-registered 2026-08-19, and this module calls it. Two implementations of the
hit test is the one shape of defect that could move every number in this file
without failing anything.

**What is reported, per section 5:**

  * recall@1 and recall@5, per arm, per stratum, each with a Wilson interval.
    Stated precisely because the name is loose: every answerable query has at
    least one gold chunk, so the quantity is a **hit rate at k**. It is called
    recall@k because that is the term the literature uses.
  * The comparison between two arms as **the discordant pairs (b, c) with a
    Wilson interval on b/(b+c)** -- McNemar's sign test. Three overlapping
    Wilson intervals invite "no difference" when the paired data may say
    otherwise, and the arms see the same queries.
  * The count of DUPLICATE-SPAN queries among the answerable set, which
    AMENDMENT 5 requires alongside the results.

**The 15 unanswerable queries are excluded explicitly, never by accident.**
They carry no gold, recall is undefined for them, and `hit_at_k` raises rather
than scoring an empty gold set as a miss. Scoring them as misses would drag
every arm down by the same 15/65 and read as a result. Abstention is a property
of the QA layer and is measured against it in Phases 4 and 5.

**Wilson comes from `evaluation/wilson.py`**, the same implementation the
extraction numbers used. A second statistical implementation would need its own
hand-verified tests, and the two would agree until the day they did not.
"""

from evaluation import retrieval_gold as gold
from evaluation.scoring import MIN_REPORTABLE_N
from evaluation.wilson import wilson_interval

# The three arms, in the order they are reported: the two baselines, then the
# thing being argued for.
ARMS = ("sparse", "dense", "hybrid")

# Pre-registered in section 5. Not a parameter of the retriever -- these are
# the cutoffs the results are reported at, fixed with everything else.
K_VALUES = (1, 5)

# Every arm compared against every other, in the direction that puts the arm
# expected to win first. Direction only decides which of (b, c) is which; the
# pair is reported both ways round regardless.
COMPARISONS = (("hybrid", "sparse"), ("hybrid", "dense"), ("dense", "sparse"))

ANSWERABLE_STRATA = ("exact_entity", "conceptual")
UNANSWERABLE = "unanswerable"

# PHASE 3b, pre-registered 2026-08-20 and POST-HOC: designed after the three
# arms above had run and been published, on the same 65 queries.
#
# It is deliberately **not** a member of `ARMS`. The three pre-registered arms
# are the set every published Phase 3 number is computed over, and `RESULTS.md`
# tells a reader to re-score that run to reproduce them. Adding a fourth name to
# `ARMS` would make the original rankings file refuse to score, so the
# document's own reproduction instructions would stop working -- and the repair
# would look like a scoring change to numbers that must never move.
#
# Instead the fourth arm is detected from the rankings and appended. Absent, the
# scorer behaves exactly as it did before this constant existed.
GATED_ARM = "gated"


def arms_present(rankings: dict) -> tuple:
    """The three pre-registered arms, plus `gated` when the rankings carry it.

    Refuses a partial gated file. A fourth arm covering some queries and not
    others is scored over a smaller denominator than the other three, and the
    same hits over a smaller denominator is a higher recall -- silent, and in
    the direction that flatters the new arm.
    """
    with_gate = sorted(qid for qid, record in rankings.items()
                       if GATED_ARM in record.get("arms", {}))
    if not with_gate:
        return ARMS
    without = sorted(set(rankings) - set(with_gate))
    if without:
        raise ValueError(
            f"{len(with_gate)} rankings carry a {GATED_ARM!r} arm and "
            f"{len(without)} do not: {without[:5]}. A partial fourth arm is "
            f"scored over a smaller denominator than the other three, which is "
            f"a higher recall for the same hits.")
    return ARMS + (GATED_ARM,)


def comparisons_for(arms: tuple) -> tuple:
    """Every pair to report, in the direction that puts the arm expected to win
    first. Direction only decides which of (b, c) is which.

    The three pre-registered pairs are returned unchanged and in order, so a
    published comparison keeps its place in the output when a fourth arm joins.
    """
    if GATED_ARM not in arms:
        return COMPARISONS
    return COMPARISONS + tuple((GATED_ARM, other) for other in ARMS)


def merge_gated(rankings: dict, gated: dict) -> dict:
    """A copy of `rankings` with each query's `gated` list added.

    A copy, not an edit: the rankings file is the authority for three published
    arms, and a merge that mutated it in place would edit them in memory on the
    way to a report that says they are unchanged. Only the one key is added --
    every other arm's list is carried across unrebuilt.
    """
    unknown = sorted(set(gated) - set(rankings))
    if unknown:
        raise ValueError(
            f"the gated file holds {unknown[:5]}, which the rankings do not. "
            f"The fourth arm is computed from the first two; a query in one and "
            f"not the other means the two files are from different runs.")
    missing = sorted(set(rankings) - set(gated))
    if missing:
        raise ValueError(
            f"the gated file has no entry for {missing[:5]}. Every query the "
            f"three arms ran must have a fourth-arm ranking, or the fourth arm "
            f"is scored over a smaller denominator.")
    merged = {}
    for qid, record in rankings.items():
        arms = dict(record["arms"])
        arms[GATED_ARM] = gated[qid]
        merged[qid] = dict(record, arms=arms)
    return merged


def split_by_answerability(queries: list[dict]) -> dict:
    """The answerable queries, the excluded ones, and the stratum counts.

    Both refusals are about the query set rather than about any arm:

      an `unanswerable` query carrying gold would enter the recall denominator
      silently, which is the one thing that stratum exists not to do;

      an answerable query with no gold would reach `hit_at_k`, which raises --
      correctly, but halfway through a run and without naming the query.
    """
    answerable, excluded, strata = [], [], {}
    for query in queries:
        qid = query["query_id"]
        stratum = query.get("stratum")
        has_gold = bool(query.get("gold"))
        if stratum == UNANSWERABLE:
            if has_gold:
                raise ValueError(
                    f"{qid} is filed as {UNANSWERABLE} but carries gold. It "
                    f"would enter the recall denominator, and abstention is "
                    f"measured by the QA layer, never against a ranked list.")
            excluded.append(qid)
            continue
        if not has_gold:
            raise ValueError(
                f"{qid} is answerable ({stratum}) but carries no gold. Recall "
                f"is undefined without it, and scoring it a miss would fold a "
                f"query-set defect into the retrieval number.")
        answerable.append(query)
        strata[stratum] = strata.get(stratum, 0) + 1
    return {"answerable": answerable, "excluded": excluded, "strata": strata}


def locations(query: dict) -> list[tuple[str, str]]:
    return [(g["accession"], g["span"]) for g in query.get("gold") or []]


def ranked_ids(ranking: dict, arm: str) -> list[str]:
    """The chunk ids one arm returned, in rank order.

    The stored form is `[[chunk_id, score], ...]`: the scores are kept in the
    file because they are what a reader would need to check a ranking by hand,
    and dropped here because recall@k is a function of positions only.
    """
    try:
        entries = ranking["arms"][arm]
    except KeyError:
        raise ValueError(
            f"{ranking.get('query_id')!r} has no {arm!r} arm. All three arms "
            f"run over every query; a missing one is a truncated run, not an "
            f"arm that returned nothing.") from None
    return [entry[0] for entry in entries]


def score_query(records: list[dict], query: dict, ranking: dict,
                arms: tuple = ARMS) -> dict:
    """One query's gold set and its hit/miss for every arm at every k.

    Validates the gold against the store first, under the pre-registered rule
    -- a span matching zero chunks, or more than five, is a broken query and
    not a retrieval failure. The freeze checked this before the run; it is
    checked again here because the store is a file outside the repo and the two
    checks bracket the moment the numbers are produced.
    """
    qid = query["query_id"]
    places = locations(query)
    problems = gold.validate_gold(records, places)
    if problems:
        raise ValueError(f"{qid}: " + "; ".join(problems))
    gold_ids = gold.gold_chunk_ids_for(records, places)

    known = {record["chunk_id"] for record in records}
    scored = {}
    for arm in arms:
        ids = ranked_ids(ranking, arm)
        unknown = [chunk_id for chunk_id in ids if chunk_id not in known]
        if unknown:
            raise ValueError(
                f"{qid}: the {arm} arm returned {unknown[:3]}, which the chunk "
                f"store does not hold. Gold is derived from the store and the "
                f"rankings come from the database; an id in one and not the "
                f"other scores as a miss that looks like a retrieval failure.")
        scored[arm] = {k: gold.hit_at_k(ids, gold_ids, k) for k in K_VALUES}
    return {"query_id": qid, "stratum": query.get("stratum"),
            "gold": gold_ids, "arms": scored}


def recall_row(hits: int, n: int, confidence: float = 0.95) -> dict:
    """One reported rate: hits, denominator, rate, Wilson interval.

    `reportable` records whether the row clears the n = 25 floor section 3 sets
    for any claim. It is a flag rather than a suppression: a row that fell
    under the floor still gets printed, with the fact attached, because the
    denominator is the thing a reader needs to see.
    """
    # The interval first, so an empty denominator raises `wilson_interval`'s
    # explanation rather than a bare ZeroDivisionError from the line above it.
    interval = wilson_interval(hits, n, confidence)
    return {
        "hits": hits,
        "n": n,
        "rate": hits / n,
        "interval": interval,
        "reportable": n >= MIN_REPORTABLE_N,
    }


def mcnemar(hits_a: dict, hits_b: dict, confidence: float = 0.95) -> dict:
    """The paired comparison: discordant pairs (b, c) and Wilson on b/(b+c).

    `b` is the count of queries the first arm gets and the second misses; `c`
    is the reverse. The concordant queries -- both right, both wrong -- carry
    no information about which arm is better and are counted but not tested.

    `rate` and `interval` are **None when b + c is zero**, rather than raised
    or defaulted. Two arms that agree on every query is a real outcome, and
    `wilson_interval` refuses an empty denominator by design because an empty
    denominator is not a rate.
    """
    if set(hits_a) != set(hits_b):
        raise ValueError(
            "the two arms were not scored over the same queries, so this is "
            "not a paired comparison. "
            f"only in the first: {sorted(set(hits_a) - set(hits_b))[:3]}; "
            f"only in the second: {sorted(set(hits_b) - set(hits_a))[:3]}")
    b = sum(1 for qid in hits_a if hits_a[qid] and not hits_b[qid])
    c = sum(1 for qid in hits_a if hits_b[qid] and not hits_a[qid])
    discordant = b + c
    return {
        "b": b,
        "c": c,
        "discordant": discordant,
        "concordant": len(hits_a) - discordant,
        "n": len(hits_a),
        "rate": (b / discordant) if discordant else None,
        "interval": wilson_interval(b, discordant, confidence)
                    if discordant else None,
        "reportable": discordant >= MIN_REPORTABLE_N,
    }


def summarize(queries: list[dict], records: list[dict], rankings: dict,
              confidence: float = 0.95) -> dict:
    """Every reported number, from the frozen set, the store and the rankings.

    Refuses a rankings map that is not exactly the answerable set: a lost query
    shrinks the denominator, and the same hits over a smaller denominator is a
    higher recall.
    """
    split = split_by_answerability(queries)
    answerable = split["answerable"]

    wanted = {query["query_id"] for query in answerable}
    have = set(rankings)
    missing = sorted(wanted - have)
    extra = sorted(have - wanted - set(split["excluded"]))
    if missing:
        raise ValueError(
            f"no ranking for {missing}. A missing query shrinks the "
            f"denominator, and the same hits over a smaller denominator is a "
            f"higher recall.")
    if extra:
        raise ValueError(
            f"rankings hold {extra}, which is not in the frozen query set. "
            f"Every number here is over the 65 that were reviewed.")

    reported = arms_present(rankings)
    outcomes = [score_query(records, query, rankings[query["query_id"]],
                            reported)
                for query in answerable]

    groups = {"pooled": outcomes}
    for stratum in ANSWERABLE_STRATA:
        groups[stratum] = [o for o in outcomes if o["stratum"] == stratum]

    arms = {}
    for arm in reported:
        arms[arm] = {}
        for k in K_VALUES:
            arms[arm][k] = {
                name: recall_row(sum(1 for o in group if o["arms"][arm][k]),
                                 len(group), confidence)
                for name, group in groups.items() if group
            }

    comparisons = []
    for arm_a, arm_b in comparisons_for(reported):
        for k in K_VALUES:
            for name, group in groups.items():
                if not group:
                    continue
                result = mcnemar(
                    {o["query_id"]: o["arms"][arm_a][k] for o in group},
                    {o["query_id"]: o["arms"][arm_b][k] for o in group},
                    confidence)
                comparisons.append(dict(result, arm_a=arm_a, arm_b=arm_b,
                                        k=k, stratum=name))

    return {
        "queries": {
            "total": len(queries),
            "answerable": len(answerable),
            "excluded_unanswerable": len(split["excluded"]),
            "excluded_ids": split["excluded"],
        },
        "strata": split["strata"],
        "duplicate_span_advisories": duplicate_span_count(answerable, records),
        "confidence": confidence,
        "arms": arms,
        "comparisons": comparisons,
        "per_query": {o["query_id"]: o for o in outcomes},
    }


def duplicate_span_count(answerable: list[dict], records: list[dict]) -> int:
    """How many answerable queries carry a gold span that appears in another
    filing. AMENDMENT 5 requires this reported alongside the results.

    Counted from the store rather than copied from the freeze, so a store that
    moved between the freeze and the score shows up as a disagreement instead
    of being restated. Uses the same `advisory_notes` the freeze used, for the
    reason that runs through this module: one implementation of each rule.
    """
    flagged = 0
    for query in answerable:
        notes = gold.advisory_notes(locations(query), records)
        if any(note.startswith(gold.DUPLICATE_NOTE) for note in notes):
            flagged += 1
    return flagged
