"""The QA context module: pins, dedupe, and the split control.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
failures this file is written against:

  **A pin that drifts.** The artifact digests, the context depth, the arm
  list and the published numerators are registered constants. Each is
  asserted literally here, so editing one in the module fails a test instead
  of quietly re-registering the phase.

  **Dedupe inventing or losing a row.** One call per distinct context is only
  sound if every (query, arm) pair maps to exactly one call and identical
  contexts collapse while different *orders* do not -- the model reads
  excerpts in order, so order is part of the context.

  **The split disagreeing with the published numerators.** The conditioned
  denominators must be re-derived, not restated; `verify_split_control` is
  the machine check, and it must refuse loudly on a one-off error in either
  direction.
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_contexts  # noqa: E402
from evaluation import retrieval_scoring as scoring  # noqa: E402


def test_context_depth_is_the_pre_registered_cutoff():
    assert qa_contexts.CONTEXT_K == 5
    # k = 5 is only defensible because it is one of the two cutoffs the
    # retrieval appendix registered; a context depth outside K_VALUES would
    # be a new retrieval metric wearing a QA costume.
    assert qa_contexts.CONTEXT_K in scoring.K_VALUES


def test_all_four_arms_and_no_fifth():
    assert qa_contexts.QA_ARMS == ("sparse", "dense", "hybrid", "gated")


def test_pinned_digests_are_the_published_ones():
    assert qa_contexts.RANKINGS_SHA256 == (
        "202da364a4d0db0315f84a44ebc553b5055079e21aa03992e8e118e958c7319a")
    assert qa_contexts.GATED_RANKINGS_SHA256 == (
        "51f2786317352415278e0b4ad7f6c38426a5c234a53ad8214a56e3dcb051e724")
    assert qa_contexts.CHUNKS_SHA256 == (
        "b6719c05e974b9396bd2e3eac8ab5a00c01231f0eb968396cfea743d9e031467")
    assert qa_contexts.FROZEN_SET_SHA256 == (
        "a35b2634f47608fdee4d1dbd612e6d6d56f64d1e261ce85c4e6bb00d5cbde16a")


def test_split_control_is_the_published_numerators():
    assert qa_contexts.SPLIT_CONTROL == {
        "sparse": 10, "dense": 22, "hybrid": 18, "gated": 25}


def test_verify_pinned_accepts_matching_bytes(tmp_path):
    path = tmp_path / "artifact.jsonl"
    path.write_bytes(b"exact bytes\n")
    import hashlib
    digest = hashlib.sha256(b"exact bytes\n").hexdigest()
    assert qa_contexts.verify_pinned(path, digest, "artifact") == digest


def test_verify_pinned_refuses_and_names_both_digests(tmp_path):
    path = tmp_path / "artifact.jsonl"
    path.write_bytes(b"drifted bytes\n")
    import hashlib
    expected = hashlib.sha256(b"exact bytes\n").hexdigest()
    with pytest.raises(ValueError) as caught:
        qa_contexts.verify_pinned(path, expected, "artifact")
    message = str(caught.value)
    assert expected in message
    assert hashlib.sha256(b"drifted bytes\n").hexdigest() in message


def _ranking(qid, per_arm):
    return {"query_id": qid,
            "arms": {arm: [[cid, 0.5] for cid in ids]
                     for arm, ids in per_arm.items()}}


def _six(prefix):
    return [f"{prefix}{n}" for n in range(6)]


def test_contexts_truncate_to_k_in_rank_order():
    merged = {"q001": _ranking("q001", {
        "sparse": _six("s"), "dense": _six("d"),
        "hybrid": _six("h"), "gated": _six("g")})}
    ctxs = qa_contexts.contexts(merged)
    assert ctxs["q001"]["dense"] == ("d0", "d1", "d2", "d3", "d4")
    assert len(ctxs["q001"]) == 4


def test_contexts_refuse_a_short_arm():
    merged = {"q001": _ranking("q001", {
        "sparse": ["s0", "s1"], "dense": _six("d"),
        "hybrid": _six("h"), "gated": _six("g")})}
    with pytest.raises(ValueError, match="sparse.*2 chunks"):
        qa_contexts.contexts(merged)


def test_contexts_refuse_a_missing_arm():
    merged = {"q001": _ranking("q001", {
        "sparse": _six("s"), "dense": _six("d"), "hybrid": _six("h")})}
    with pytest.raises(ValueError, match="gated"):
        qa_contexts.contexts(merged)


def test_call_id_is_order_sensitive():
    ids = ("a", "b", "c", "d", "e")
    reordered = ("b", "a", "c", "d", "e")
    assert qa_contexts.call_id("q001", ids) != \
        qa_contexts.call_id("q001", reordered)
    assert qa_contexts.call_id("q001", ids) == \
        qa_contexts.call_id("q001", ids)


def test_dedupe_collapses_identical_contexts_and_keeps_every_row():
    shared = ("d0", "d1", "d2", "d3", "d4")
    hybrid = ("h0", "h1", "h2", "h3", "h4")
    sparse = ("s0", "s1", "s2", "s3", "s4")
    ctxs = {"q001": {"sparse": sparse, "dense": shared,
                     "hybrid": hybrid, "gated": shared}}
    calls, assignment = qa_contexts.dedupe(ctxs)
    assert len(calls) == 3
    assert set(assignment) == {("q001", arm) for arm in qa_contexts.QA_ARMS}
    gated_call = assignment[("q001", "gated")]
    assert gated_call == assignment[("q001", "dense")]
    # Arms sharing a call are recorded in QA_ARMS order, deterministically.
    assert calls[gated_call]["arms"] == ["dense", "gated"]


def test_dedupe_separates_same_ids_in_different_order():
    ctxs = {"q001": {"sparse": ("a", "b", "c", "d", "e"),
                     "dense": ("b", "a", "c", "d", "e"),
                     "hybrid": ("a", "b", "c", "d", "e"),
                     "gated": ("b", "a", "c", "d", "e")}}
    calls, assignment = qa_contexts.dedupe(ctxs)
    assert len(calls) == 2
    assert assignment[("q001", "sparse")] != assignment[("q001", "dense")]


def _store():
    """Six chunks over two accessions; the span sits in gold-1 only."""
    return [
        {"chunk_id": "gold-1", "accession": "acc-A",
         "text": "the twelve word span that answers the question sits here "
                 "in full"},
        {"chunk_id": "cold-1", "accession": "acc-A", "text": "nothing"},
        {"chunk_id": "cold-2", "accession": "acc-A", "text": "still nothing"},
        {"chunk_id": "cold-3", "accession": "acc-A", "text": "more nothing"},
        {"chunk_id": "cold-4", "accession": "acc-A", "text": "yet more"},
        {"chunk_id": "cold-5", "accession": "acc-B", "text": "other filing"},
    ]


_SPAN = ("the twelve word span that answers the question sits here in full")


def test_conditioned_split_reads_hits_at_k_per_arm():
    queries = [
        {"query_id": "q001", "stratum": "exact_entity", "query": "?",
         "gold": [{"accession": "acc-A", "span": _SPAN}]},
        {"query_id": "q009", "stratum": "unanswerable", "query": "?",
         "gold": []},
    ]
    hit = ["gold-1", "cold-1", "cold-2", "cold-3", "cold-4"]
    miss = ["cold-1", "cold-2", "cold-3", "cold-4", "cold-5"]
    merged = {"q001": _ranking("q001", {
        "sparse": miss, "dense": hit, "hybrid": miss, "gated": hit})}
    split = qa_contexts.conditioned_split(queries, _store(), merged)
    assert split["dense"] == {"q001": True}
    assert split["sparse"] == {"q001": False}
    # The unanswerable query takes no side of the split.
    assert "q009" not in split["dense"]


def _synthetic_split(counts):
    """A 50-query split with exactly `counts[arm]` queries in-context."""
    qids = [f"q{n:03d}" for n in range(1, 51)]
    return {arm: {qid: index < counts[arm]
                  for index, qid in enumerate(qids)}
            for arm in qa_contexts.QA_ARMS}


def test_split_control_passes_on_the_published_numerators():
    split = _synthetic_split(qa_contexts.SPLIT_CONTROL)
    assert qa_contexts.verify_split_control(split) == \
        qa_contexts.SPLIT_CONTROL


def test_split_control_refuses_one_off_in_either_direction():
    for arm, delta in (("dense", -1), ("sparse", 1)):
        counts = dict(qa_contexts.SPLIT_CONTROL)
        counts[arm] += delta
        with pytest.raises(ValueError, match="never the denominator"):
            qa_contexts.verify_split_control(_synthetic_split(counts))


def test_excerpts_carry_render_fields_and_scorer_fields():
    records = {"gold-1": {
        "chunk_id": "gold-1", "accession": "acc-A", "ticker": "GWW",
        "period": "2024-12-31", "item": "1", "text": "the text"}}
    excerpts = qa_contexts.excerpts_for(("gold-1",), records_by_id=records)
    assert excerpts == [{
        "chunk_id": "gold-1", "accession": "acc-A", "ticker": "GWW",
        "period": "2024-12-31", "item": "1", "text": "the text"}]


def test_excerpts_refuse_an_id_the_chunk_file_lacks():
    with pytest.raises(ValueError, match="wrong chunk file"):
        qa_contexts.excerpts_for(("ghost",), records_by_id={})
