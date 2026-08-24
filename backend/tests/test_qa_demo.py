"""The live-demo QA orchestration: arms over live retrieval, the measured
instrument unchanged.

What these tests pin, and why it matters more here than in most UI glue:

- **The instrument is `services/qa.py`'s, byte for byte.** The demo's whole
  claim is "the configuration you are clicking is the configuration that was
  measured", so the default `ask` must literally be `qa.ask` and the response
  must carry the pinned fingerprint. A demo that quietly wrapped a different
  prompt would be a second instrument wearing the first one's numbers.
- **The gated arm refuses an unmeasured lexeme count rather than defaulting.**
  `tau(L)` exists only for the sizes the threshold artifact measured;
  defaulting low gates nothing, defaulting high gates everything, and a demo
  answer would not say which happened. The refusal names the measured sizes so
  the UI can say exactly why.
- **Rank order survives the database round-trip.** `= any(...)` returns rows
  in whatever order Postgres pleases; excerpts are numbered in rank order and
  the model cites those numbers, so a reordered fetch would silently point
  every citation at the wrong excerpt.
- **The quote highlight can only agree with the published check.**
  `quote_location` is presentation, but it is built on `retrieval_gold`'s own
  `normalize` and cross-checks itself against it at runtime, degrading to
  no-highlight rather than ever highlighting a span the published
  `contains_span` would not have matched.
"""

import inspect
import json

import pytest

from evaluation import retrieval_gold as gold
from services import qa, qa_demo, retrieval


# ---------------------------------------------------------------------------
# load_taus
# ---------------------------------------------------------------------------

def _write_threshold(directory, stamp, sizes):
    # The real artifact's shape (scripts/measure_gate_threshold.py): `sizes`
    # maps L to the query ids that had that lexeme count; the null
    # distributions and tau live under `null`, keyed by the same L. The first
    # version of this helper invented a flatter shape and the tests passed
    # against their own assumption -- the live artifact is the contract.
    payload = {
        "sizes": {str(size): ["q001"] for size in sizes},
        "null": {str(size): {"tau": tau, "queries": 1, "bags": 1000,
                             "min": 0.1, "median": 1.0, "mean": 1.0,
                             "max": 9.9, "zero_scoring_bags": 0}
                 for size, tau in sizes.items()},
    }
    path = directory / f"gate-threshold-{stamp}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_taus_reads_sizes_as_int_to_float(tmp_path):
    _write_threshold(tmp_path, "20260821-001744", {4: 3.3, 7: 3.8})
    taus = qa_demo.load_taus(tmp_path)
    assert taus == {4: 3.3, 7: 3.8}
    assert all(isinstance(k, int) for k in taus)
    assert all(isinstance(v, float) for v in taus.values())


def test_load_taus_missing_artifact_names_the_directory(tmp_path):
    with pytest.raises(FileNotFoundError) as caught:
        qa_demo.load_taus(tmp_path)
    assert str(tmp_path) in str(caught.value)
    assert "measure_gate_threshold" in str(caught.value)


def test_load_taus_picks_the_latest_stamp(tmp_path):
    _write_threshold(tmp_path, "20260820-000000", {4: 9.9})
    _write_threshold(tmp_path, "20260821-001744", {4: 3.3})
    assert qa_demo.load_taus(tmp_path) == {4: 3.3}


# ---------------------------------------------------------------------------
# choose_ranking
# ---------------------------------------------------------------------------

SPARSE = [("c-sparse-1", 9.0), ("c-both", 7.0), ("c-sparse-3", 5.0)]
DENSE = [("c-dense-1", 0.10), ("c-both", 0.20), ("c-dense-3", 0.30)]
TSQUERY = "'goodwil' | 'impair' | 'charg' | 'fiscal'"  # L = 4
TAUS = {4: 3.3}


def test_choose_ranking_sparse_is_the_sparse_order():
    out = qa_demo.choose_ranking("sparse", TSQUERY, SPARSE, DENSE, TAUS)
    assert [cid for cid, _ in out["ranking"]] == [
        "c-sparse-1", "c-both", "c-sparse-3"]
    assert out["gate"] is None


def test_choose_ranking_dense_is_the_dense_order():
    out = qa_demo.choose_ranking("dense", TSQUERY, SPARSE, DENSE, TAUS)
    assert [cid for cid, _ in out["ranking"]] == [
        "c-dense-1", "c-both", "c-dense-3"]
    assert out["gate"] is None


def test_choose_ranking_hybrid_matches_the_published_fusion():
    out = qa_demo.choose_ranking("hybrid", TSQUERY, SPARSE, DENSE, TAUS)
    expected = retrieval.hybrid([cid for cid, _ in SPARSE],
                                [cid for cid, _ in DENSE])
    assert out["ranking"] == expected
    assert out["gate"] is None


def test_choose_ranking_gated_boundary_equality_fires():
    # s1 == tau is the gated case, pinned in evaluation/gate.py: evidence
    # exactly at the null's 95th percentile is not evidence.
    sparse = [("c-sparse-1", 3.3)] + SPARSE[1:]
    out = qa_demo.choose_ranking("gated", TSQUERY, sparse, DENSE, TAUS)
    assert out["gate"] == {"fired": True, "s1": 3.3, "tau": 3.3, "lexemes": 4}
    dense_only = [cid for cid, _ in out["ranking"]]
    assert dense_only == ["c-dense-1", "c-both", "c-dense-3"]


def test_choose_ranking_gated_not_fired_is_bit_identical_to_hybrid():
    out = qa_demo.choose_ranking("gated", TSQUERY, SPARSE, DENSE, TAUS)
    hybrid = qa_demo.choose_ranking("hybrid", TSQUERY, SPARSE, DENSE, TAUS)
    assert out["gate"]["fired"] is False
    assert out["ranking"] == hybrid["ranking"]


def test_choose_ranking_gated_unmeasured_size_refuses_naming_sizes():
    two_lexeme = "'amcor' | 'ceo'"
    with pytest.raises(LookupError) as caught:
        qa_demo.choose_ranking("gated", two_lexeme, SPARSE, DENSE,
                               {4: 3.3, 7: 3.8})
    message = str(caught.value)
    assert "2" in message           # the size that has no threshold
    assert "4" in message and "7" in message  # the sizes that do


def test_choose_ranking_gated_zero_lexemes_gates_by_definition():
    # An all-stopword question has no lexemes, the sparse arm returns nothing,
    # and tau(0) = 0.0 by the published rule in measure_gate_threshold: the
    # gate fires and the ranking is the dense arm's.
    out = qa_demo.choose_ranking("gated", None, [], DENSE, TAUS)
    assert out["gate"]["fired"] is True
    assert out["gate"]["tau"] == 0.0
    assert [cid for cid, _ in out["ranking"]] == [
        "c-dense-1", "c-both", "c-dense-3"]


def test_choose_ranking_unknown_arm_refuses():
    with pytest.raises(ValueError):
        qa_demo.choose_ranking("bm25", TSQUERY, SPARSE, DENSE, TAUS)


# ---------------------------------------------------------------------------
# top_excerpts
# ---------------------------------------------------------------------------

class ShuffledCursor:
    """Returns chunk rows in an order unrelated to the one asked for --
    which is what `= any(...)` actually does."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append((sql, params))

    def fetchall(self):
        return self.rows


ROWS = [
    ("c2", "0000000000-25-000002", "KVUE", "2025-12-31", "8", "text two"),
    ("c1", "0000000000-25-000001", "AMCR", "2025-06-30", "1A", "text one"),
    ("c3", "0000000000-25-000003", "WYNN", "2025-12-31", "", "text three"),
]


def test_top_excerpts_preserves_rank_order_not_database_order():
    cursor = ShuffledCursor(ROWS)
    excerpts = qa_demo.top_excerpts(cursor, ["c1", "c2", "c3"])
    assert [e["chunk_id"] for e in excerpts] == ["c1", "c2", "c3"]
    assert excerpts[0]["ticker"] == "AMCR"
    assert excerpts[2]["item"] == ""
    assert set(excerpts[0]) == {
        "chunk_id", "accession", "ticker", "period", "item", "text"}


def test_top_excerpts_missing_chunk_refuses_by_name():
    cursor = ShuffledCursor(ROWS[:2])
    with pytest.raises(ValueError) as caught:
        qa_demo.top_excerpts(cursor, ["c1", "c2", "c3"])
    assert "c3" in str(caught.value)


def test_top_excerpts_empty_ids_is_empty_without_a_statement():
    cursor = ShuffledCursor([])
    assert qa_demo.top_excerpts(cursor, []) == []
    assert cursor.statements == []


# ---------------------------------------------------------------------------
# quote_location
# ---------------------------------------------------------------------------

def test_quote_location_exact_span():
    text = "Total net revenues were $4,466 million for fiscal 2025."
    span = qa_demo.quote_location(text, "revenues were $4,466 million")
    assert span is not None
    start, end = span
    assert text[start:end] == "revenues were $4,466 million"


def test_quote_location_folds_curly_quotes_and_case():
    # The chunk has a curly apostrophe and capitals; the model's quote has a
    # straight one and lowercase. The published normalize matches them, so the
    # highlight must too.
    text = "The Company’s Total Assets Grew during the period."
    span = qa_demo.quote_location(text, "the company's total assets grew")
    assert span is not None
    start, end = span
    assert text[start:end] == "The Company’s Total Assets Grew"


def test_quote_location_collapses_whitespace_runs():
    text = "Net sales,\n     net of returns, were $914 million."
    span = qa_demo.quote_location(text, "Net sales, net of returns,")
    assert span is not None
    start, end = span
    assert text[start:end] == "Net sales,\n     net of returns,"


def test_quote_location_absent_is_none():
    assert qa_demo.quote_location("some chunk text", "not present here") is None


def test_quote_location_empty_or_whitespace_quote_is_none():
    assert qa_demo.quote_location("some chunk text", "") is None
    assert qa_demo.quote_location("some chunk text", "   ") is None
    assert qa_demo.quote_location("some chunk text", None) is None


def test_quote_location_self_check_degrades_on_fold_drift(monkeypatch):
    # The walker restates retrieval_gold's fold table, which is safe only
    # because it verifies its reconstruction against the published `normalize`
    # on every call. Simulate the tables drifting apart: the answer must
    # become "no highlight", never a wrong span.
    monkeypatch.setattr(qa_demo, "_FOLDS", {})
    text = "The Company’s total assets grew."
    assert qa_demo.quote_location(text, "company's total assets") is None


def test_quote_location_agrees_with_the_published_normalize():
    # The property that makes the highlight honest: whatever raw span comes
    # back normalizes to exactly what the quote normalizes to, so the marked
    # text is the text the published containment check matched.
    text = ("Management’s  discussion — and analysis.\n"
            "We operated 914 warehouses,   890 of them wholly owned.")
    quote = 'management\'s discussion - and analysis.'
    span = qa_demo.quote_location(text, quote)
    assert span is not None
    start, end = span
    assert gold.normalize(text[start:end]) == gold.normalize(quote)


# ---------------------------------------------------------------------------
# answer_question
# ---------------------------------------------------------------------------

EXCERPTS = [
    {"chunk_id": "c1", "accession": "a1", "ticker": "AMCR",
     "period": "2025-06-30", "item": "7",
     "text": "First excerpt text with nothing relevant."},
    {"chunk_id": "c2", "accession": "a2", "ticker": "KVUE",
     "period": "2025-12-31", "item": "8",
     "text": "Total net revenues were $4,466 million for fiscal 2025."},
]


@pytest.fixture
def live_stubs(monkeypatch):
    """Stub the live-retrieval boundary: SQL and the embeddings API. The
    fusion math, the gate rule, the parse and the containment check stay
    real."""
    monkeypatch.setattr(retrieval, "or_tsquery",
                        lambda cursor, text: TSQUERY)
    monkeypatch.setattr(retrieval, "sparse_search",
                        lambda cursor, tsquery, depth=50: list(SPARSE))
    monkeypatch.setattr(retrieval, "embed_query",
                        lambda client, text: [0.0] * 1536)
    monkeypatch.setattr(retrieval, "dense_search",
                        lambda cursor, vector, depth=50, ef_search=100:
                        list(DENSE))
    monkeypatch.setattr(qa_demo, "top_excerpts",
                        lambda cursor, ids: [
                            dict(e, chunk_id=cid) for cid, e in
                            zip(ids, EXCERPTS * 3)])


def _ask_returning(payload):
    def fake_ask(question, excerpts, client=None, sleep=None):
        return {"raw": json.dumps(payload), "attempts": 1,
                "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    return fake_ask


def test_answer_question_answered_carries_provenance(live_stubs):
    response = qa_demo.answer_question(
        "What were total net revenues?", "dense", cursor=object(),
        client=object(), taus=TAUS,
        ask=_ask_returning({
            "answer": "$4,466 million",
            "citation": 2,
            "quote": "Total net revenues were $4,466 million for fiscal "
                     "2025."}))
    assert response["state"] == "answered"
    assert response["citation"] == 2
    assert response["citation_valid"] is True
    assert response["quote_verified"] is True
    assert response["highlight"] == (
        "Total net revenues were $4,466 million for fiscal 2025.")
    assert [e["n"] for e in response["excerpts"]] == [1, 2, 3]
    assert response["excerpts"][0]["chunk_id"] == "c-dense-1"
    assert response["instrument_sha256"] == qa.INSTRUMENT_SHA256
    assert response["model"] == qa.MODEL
    assert response["arm"] == "dense"


def test_answer_question_abstention_is_a_first_class_state(live_stubs):
    response = qa_demo.answer_question(
        "What was Apple's revenue?", "dense", cursor=object(),
        client=object(), taus=TAUS,
        ask=_ask_returning({"answer": None, "citation": None, "quote": None}))
    assert response["state"] == "abstained"
    assert response["answer"] is None
    assert response["quote_verified"] is None
    assert response["highlight"] is None
    # The excerpts the model declined over are still returned: the decline's
    # provenance is what makes it credible.
    assert len(response["excerpts"]) == 3


def test_answer_question_malformed_is_reported_not_hidden(live_stubs):
    def fake_ask(question, excerpts, client=None, sleep=None):
        return {"raw": "I think the answer is 42.", "attempts": 1,
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
    response = qa_demo.answer_question(
        "Anything?", "dense", cursor=object(), client=object(), taus=TAUS,
        ask=fake_ask)
    assert response["state"] == "malformed"
    assert response["malformed_reason"] == "not valid JSON"
    assert response["raw"] == "I think the answer is 42."


def test_answer_question_citation_out_of_range_is_flagged(live_stubs):
    response = qa_demo.answer_question(
        "Question?", "dense", cursor=object(), client=object(), taus=TAUS,
        ask=_ask_returning({"answer": "something", "citation": 9,
                            "quote": "a quote"}))
    assert response["state"] == "answered"
    assert response["citation_valid"] is False
    assert response["quote_verified"] is None
    assert response["highlight"] is None


def test_answer_question_unverified_quote_is_flagged(live_stubs):
    response = qa_demo.answer_question(
        "Question?", "dense", cursor=object(), client=object(), taus=TAUS,
        ask=_ask_returning({"answer": "something", "citation": 2,
                            "quote": "words that appear in no excerpt"}))
    assert response["state"] == "answered"
    assert response["citation_valid"] is True
    assert response["quote_verified"] is False
    assert response["highlight"] is None


def test_answer_question_verifies_quotes_under_the_published_normalize(
        live_stubs, monkeypatch):
    # The model's quote and the chunk disagree on whitespace and quote glyphs
    # -- exactly what `normalize` was registered to absorb. A naive substring
    # check here would flag a quote the published check accepts.
    excerpts = [{"chunk_id": "cx", "accession": "a", "ticker": "COST",
                 "period": "2025-08-31", "item": "7",
                 "text": "The Company’s net sales,\n   net were $914."}]
    monkeypatch.setattr(qa_demo, "top_excerpts",
                        lambda cursor, ids: excerpts)
    response = qa_demo.answer_question(
        "Net sales?", "dense", cursor=object(), client=object(), taus=TAUS,
        ask=_ask_returning({"answer": "$914", "citation": 1,
                            "quote": "The Company's net sales, net were "
                                     "$914."}))
    assert response["quote_verified"] is True
    assert response["highlight"] == (
        "The Company’s net sales,\n   net were $914.")


def test_answer_question_no_passages_never_calls_the_model(monkeypatch):
    monkeypatch.setattr(retrieval, "or_tsquery",
                        lambda cursor, text: None)
    monkeypatch.setattr(retrieval, "sparse_search",
                        lambda cursor, tsquery, depth=50: [])

    def exploding_ask(*args, **kwargs):
        raise AssertionError("the model must not be called with no excerpts")

    response = qa_demo.answer_question(
        "the of and", "sparse", cursor=object(), client=object(), taus=TAUS,
        ask=exploding_ask)
    assert response["state"] == "no_passages"
    assert response["excerpts"] == []
    assert response["answer"] is None


def test_answer_question_sparse_arm_never_embeds(monkeypatch):
    monkeypatch.setattr(retrieval, "or_tsquery",
                        lambda cursor, text: TSQUERY)
    monkeypatch.setattr(retrieval, "sparse_search",
                        lambda cursor, tsquery, depth=50: list(SPARSE))

    def exploding_embed(client, text):
        raise AssertionError("the sparse arm must not spend an embedding call")

    monkeypatch.setattr(retrieval, "embed_query", exploding_embed)
    monkeypatch.setattr(qa_demo, "top_excerpts",
                        lambda cursor, ids: [
                            dict(EXCERPTS[0], chunk_id=cid) for cid in ids])
    response = qa_demo.answer_question(
        "goodwill impairment charge fiscal", "sparse", cursor=object(),
        client=object(), taus=TAUS,
        ask=_ask_returning({"answer": None, "citation": None, "quote": None}))
    assert response["state"] == "abstained"
    assert response["gate"] is None


def test_answer_question_gated_carries_the_gate_block(live_stubs):
    response = qa_demo.answer_question(
        "goodwill impairment charge fiscal", "gated", cursor=object(),
        client=object(), taus=TAUS,
        ask=_ask_returning({"answer": None, "citation": None, "quote": None}))
    assert response["gate"] == {
        "fired": False, "s1": 9.0, "tau": 3.3, "lexemes": 4}


def test_answer_question_refuses_bad_inputs():
    with pytest.raises(ValueError):
        qa_demo.answer_question("", "dense", cursor=object(), client=object())
    with pytest.raises(ValueError):
        qa_demo.answer_question("   ", "dense", cursor=object(),
                                client=object())
    with pytest.raises(ValueError):
        qa_demo.answer_question("x" * (qa_demo.MAX_QUESTION_CHARS + 1),
                                "dense", cursor=object(), client=object())
    with pytest.raises(ValueError):
        qa_demo.answer_question("fine question", "bm25", cursor=object(),
                                client=object())


def test_answer_question_default_instrument_is_the_measured_one():
    # The seam the whole demo rests on: by default the question goes through
    # `services/qa.py`'s `ask` -- the byte-checked instrument -- not a wrapper.
    default = inspect.signature(qa_demo.answer_question).parameters["ask"]
    assert default.default is qa.ask


def test_context_depth_is_the_registered_k():
    # k = 5 is the only registered context depth (recall@1 is broken by
    # construction, deeper is a new metric). The demo feeds the same k.
    assert qa_demo.CONTEXT_K == 5
