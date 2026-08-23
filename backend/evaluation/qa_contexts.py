"""What the QA layer is fed, and the split every accuracy number reports under.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*,
published before any QA output existed. Everything in this module either
selects what the model sees or fixes a denominator, so none of it may change
after the first eval-set call.

**The run touches no database and no retrieval code path.** Contexts are read
from three pinned artifacts -- the Phase 3 rankings, the Phase 3b gated
rankings, and the chunk file the store was loaded from -- each verified by
sha256 against the digest the appendix publishes. A ranked list regenerated to
suit a result would not hash to the published value, which is the property the
pins buy.

**The conditioned split is arithmetic on published outcomes, not a new
measurement.** "Gold in context" means the query's top `CONTEXT_K` chunks,
under the arm being scored, contain a gold chunk under the pre-registered hit
test -- the same `retrieval_gold` functions, called through the same
`retrieval_scoring.score_query` that produced the published recall numbers.
The per-arm totals must therefore reproduce the published recall@5 numerators
exactly; `verify_split_control` refuses the run otherwise. The repair for a
failed control is fixing this module, never re-deriving a denominator.

**One call per distinct context.** The QA layer is a function of (question,
context). `gated`'s top 5 is bit-identical to dense's where the gate fired and
to hybrid's where it did not, so calling the API separately per arm would
inject sampling noise into paired comparisons that are concordant by
construction. `dedupe` maps the 260 arm-query pairs onto their distinct
(query, ordered top-5) contexts -- 195 over the pinned artifacts -- and
records which call every arm-query row reuses.
"""

import hashlib

from evaluation import retrieval_scoring as scoring

# The reporting cutoff the context depth inherits. k = 5 because top-1 is
# broken by construction (recall@1 is 0.100 for all four arms, a conditioned
# denominator of 5) and anything deeper is a new, unregistered retrieval
# metric -- the rankings run to depth 50, so a deeper context was one flag
# away, and the appendix declines it by name.
CONTEXT_K = 5

# All four arms, owner-decided 2026-08-21 before the appendix was written.
# Choosing one retriever on the same 65 queries this phase reports against
# would select it using those labels -- hardest on `gated`, which was designed
# on those very outcomes.
QA_ARMS = scoring.ARMS + (scoring.GATED_ARM,)

# The pinned artifacts, by the digests the appendix publishes. The rankings
# digest is also in `RESULTS.md`; the other two are published by the appendix
# itself. A mismatch is a refusal, never a warning: a context assembled from
# unpinned bytes is an unregistered experiment.
RANKINGS_SHA256 = (
    "202da364a4d0db0315f84a44ebc553b5055079e21aa03992e8e118e958c7319a")
GATED_RANKINGS_SHA256 = (
    "51f2786317352415278e0b4ad7f6c38426a5c234a53ad8214a56e3dcb051e724")
CHUNKS_SHA256 = (
    "b6719c05e974b9396bd2e3eac8ab5a00c01231f0eb968396cfea743d9e031467")
FROZEN_SET_SHA256 = (
    "a35b2634f47608fdee4d1dbd612e6d6d56f64d1e261ce85c4e6bb00d5cbde16a")

# The published recall@5 numerators, per arm, over the 50 answerable queries.
# These are the conditioned denominators, restated from `RESULTS.md` -- never
# recomputed there, and required to be *re-derivable* here before a single
# call is made.
SPLIT_CONTROL = {"sparse": 10, "dense": 22, "hybrid": 18, "gated": 25}


def file_sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_pinned(path, expected: str, what: str) -> str:
    """The artifact's digest, after refusing a mismatch by name."""
    actual = file_sha256(path)
    if actual != expected:
        raise ValueError(
            f"{what} at {path} does not match the digest the appendix "
            f"publishes.\n  expected {expected}\n  actual   {actual}\n"
            f"The pre-registration binds this phase to those exact bytes; a "
            f"different file is a different experiment.")
    return actual


def contexts(merged_rankings: dict) -> dict:
    """query_id -> arm -> the ordered top-CONTEXT_K chunk ids.

    Takes the rankings *after* `retrieval_scoring.merge_gated`, so the fourth
    arm arrived through the same completeness checks the scorer uses -- a
    partial gated file has already been refused by the time this runs.

    Refuses a ranking shorter than CONTEXT_K rather than padding it. Both
    pinned files hold at least 5 chunks for every query (checked when the
    appendix was written); fewer means these are not the pinned artifacts.
    """
    out = {}
    for qid, record in merged_rankings.items():
        per_arm = {}
        for arm in QA_ARMS:
            ids = scoring.ranked_ids(record, arm)[:CONTEXT_K]
            if len(ids) < CONTEXT_K:
                raise ValueError(
                    f"{qid}: the {arm} arm holds {len(ids)} chunks, fewer "
                    f"than the pre-registered context depth of {CONTEXT_K}. "
                    f"Both pinned artifacts hold at least {CONTEXT_K} "
                    f"everywhere, so this is not a pinned file.")
            per_arm[arm] = tuple(ids)
        out[qid] = per_arm
    return out


def call_id(qid: str, excerpt_ids: tuple) -> str:
    """A stable name for one distinct (query, ordered context) pair.

    The digest is over the ordered ids: two arms sharing the same five chunks
    in a different order are different contexts, because the model reads them
    in order.
    """
    digest = hashlib.sha256("|".join(excerpt_ids).encode("ascii")).hexdigest()
    return f"{qid}-{digest[:12]}"


def dedupe(ctxs: dict) -> tuple[dict, dict]:
    """(calls, assignment): the distinct contexts, and which one each
    arm-query row reuses.

    `calls` maps call_id -> {query_id, excerpt_ids, arms}, with `arms` the
    arms sharing that context, in QA_ARMS order so the file is deterministic.
    `assignment` maps (query_id, arm) -> call_id for every arm-query row.
    """
    calls: dict = {}
    assignment: dict = {}
    for qid in sorted(ctxs):
        for arm in QA_ARMS:
            ids = ctxs[qid][arm]
            cid = call_id(qid, ids)
            entry = calls.setdefault(
                cid, {"query_id": qid, "excerpt_ids": ids, "arms": []})
            if entry["excerpt_ids"] != ids:
                raise ValueError(
                    f"call id collision at {cid}: two different contexts "
                    f"hash to one name. Widen the digest prefix.")
            entry["arms"].append(arm)
            assignment[(qid, arm)] = cid
    return calls, assignment


def conditioned_split(queries: list[dict], records: list[dict],
                      merged_rankings: dict) -> dict:
    """arm -> {query_id: gold chunk in the top CONTEXT_K}, answerable only.

    Computed by `retrieval_scoring.score_query` -- the exact function behind
    the published recall numbers -- so there is one implementation of the hit
    test and one of the cutoff. The 15 unanswerable queries carry no gold and
    take no side of the split; their whole cell is defined by abstention.
    """
    split = scoring.split_by_answerability(queries)
    outcomes = [
        scoring.score_query(records, query,
                            merged_rankings[query["query_id"]], QA_ARMS)
        for query in split["answerable"]
    ]
    return {arm: {o["query_id"]: o["arms"][arm][CONTEXT_K] for o in outcomes}
            for arm in QA_ARMS}


def verify_split_control(split: dict) -> dict:
    """Refuse unless the split reproduces the published numerators exactly.

    This is the appendix's split control: the conditioned denominators are
    fixed by the pinned rankings and the published hit definition, and the
    machine checks that this module actually re-derives them -- 10, 22, 18,
    25 -- before any call is made. A mismatch means this module is broken;
    the repair is here, never in the denominator.
    """
    got = {arm: sum(1 for hit in split[arm].values() if hit)
           for arm in QA_ARMS}
    if got != SPLIT_CONTROL:
        raise ValueError(
            f"the conditioned split does not reproduce the published "
            f"recall@{CONTEXT_K} numerators.\n  expected {SPLIT_CONTROL}\n"
            f"  derived  {got}\nThe denominators are fixed by the pinned "
            f"artifacts; fix the split code, never the denominator.")
    return got


def excerpts_for(excerpt_ids: tuple, records_by_id: dict) -> list[dict]:
    """The rendered-order excerpt records for one context.

    Carries the fields the pre-registered template shows the model -- ticker,
    period, item -- plus three the scorer needs and the renderer never shows:
    the text, the chunk id a citation resolves to, and the accession the
    wrong-filing check compares against gold. The template in `services/qa.py`
    is what decides which of these the model sees.
    """
    missing = [cid for cid in excerpt_ids if cid not in records_by_id]
    if missing:
        raise ValueError(
            f"the rankings name {missing[:3]}, which the chunk file does not "
            f"hold. Both artifacts are pinned, so this means the wrong chunk "
            f"file is loaded, not a store drift.")
    return [{"chunk_id": cid,
             "accession": records_by_id[cid]["accession"],
             "ticker": records_by_id[cid]["ticker"],
             "period": records_by_id[cid]["period"],
             "item": records_by_id[cid]["item"],
             "text": records_by_id[cid]["text"]}
            for cid in excerpt_ids]
