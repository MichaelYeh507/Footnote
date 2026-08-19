"""Reciprocal Rank Fusion: how the hybrid arm combines the other two.

The sparse arm scores with `ts_rank_cd` and the dense arm with cosine distance.
Those are on incomparable scales, so blending them by score needs a
corpus-dependent normalisation that has to be re-tuned whenever the corpus
changes -- and a normalisation re-tuned after seeing a result is a dial. RRF
uses ranks only: scale-free, one parameter, and that parameter is fixed here.

    score(d) = sum over arms of 1 / (k + rank_arm(d))

Ranks are 1-based. A document absent from an arm's list contributes nothing
from that arm -- not a penalty, and not a notional rank of depth+1, both of
which change every ranking.

PRE-REGISTERED 2026-08-19 in EVALUATION-SPEC.md, before either index existed
and before any query was written: k = 60 (the value published with the method,
Cormack/Clarke/Buettcher 2009), fusion depth 50 per arm, ties broken by
chunk_id ascending. Do not tune k. A k chosen after seeing recall@k is a dial,
and nothing in a published number would reveal that it had been turned.

*Tradeoff, stated in the plan and repeated here because this module is where it
lives:* discarding scores discards magnitude. An arm that is confidently right
is weighted exactly like one that is marginally right.
"""

# Pre-registered 2026-08-19. See the module docstring before touching either.
RRF_K = 60
FUSION_DEPTH = 50


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = RRF_K,
    depth: int = FUSION_DEPTH,
) -> list[tuple[str, float]]:
    """Fuse per-arm rankings of chunk ids into one ranking.

    `rankings` is one list of chunk ids per arm, each already in that arm's
    rank order. Returns (chunk_id, score) pairs, best first.

    Each arm contributes only its top `depth` entries: a document ranked 51st
    by one arm cannot be rescued by the other. Within an arm a repeated id
    counts once, at its best rank -- Postgres will not return a duplicate, but
    a defensive union of two queries could, and counting it twice would let one
    arm outvote the other.

    The sort is by descending score, then by chunk_id ascending. The tie-break
    is arbitrary on purpose: what matters is that it is deterministic and
    independent of every arm's score, so recall@1 is reproducible and no arm is
    favoured by it.
    """
    scores: dict[str, float] = {}
    for arm in rankings:
        seen: set[str] = set()
        rank = 0
        for chunk_id in arm:
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            rank += 1
            if rank > depth:
                break
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
