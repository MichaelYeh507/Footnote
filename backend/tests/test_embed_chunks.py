"""Filling the dense index, and refusing to call a partial fill finished.

The embedding pass is the one step in Phase 3 that costs money and cannot be
replayed for free, so it is resumable by construction: it selects only rows
whose embedding is still NULL, and it commits per batch. A crash halfway
through is then a resumption rather than a restart.

The failure it must not have is the quiet one. A batch that silently returns
fewer vectors than it was given, or vectors of the wrong width, would leave
rows NULL while the run reports success -- and an HNSW index over a partly-NULL
column simply does not contain those chunks. They would never be retrieved, and
the dense arm would score worse for a reason that has nothing to do with
embeddings.

Written before scripts/embed_chunks.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import embed_chunks as embedder  # noqa: E402


class TestThePreRegisteredParameters:
    """Published in EVALUATION-SPEC.md on 2026-08-19, before either index
    existed. A change here is a change to a published document."""

    def test_the_model_is_text_embedding_3_small(self):
        assert embedder.MODEL == "text-embedding-3-small"

    def test_the_width_is_1536(self):
        assert embedder.DIMENSIONS == 1536

    def test_no_dimension_override_is_sent(self):
        """The model's native width, with no Matryoshka truncation, so there is
        no dimension parameter that could later be claimed to have been chosen
        for the result."""
        assert embedder.REQUEST_DIMENSIONS is None


class TestBatching:

    def test_batches_respect_the_item_limit(self):
        records = [("c%d" % i, "short text", 5) for i in range(45)]
        batches = list(embedder.batches(records, max_items=20,
                                        max_tokens=1_000_000))
        assert [len(b) for b in batches] == [20, 20, 5]

    def test_batches_respect_the_token_budget(self):
        """The API caps tokens per request, not only items. Median chunk is 460
        tokens, so an item-only limit would overshoot on Item 8 runs."""
        records = [("c%d" % i, "text", 400) for i in range(10)]
        batches = list(embedder.batches(records, max_items=100, max_tokens=1000))
        assert all(sum(r[2] for r in b) <= 1000 or len(b) == 1 for b in batches)
        assert len(batches) > 1

    def test_an_oversized_single_record_still_gets_its_own_batch(self):
        """Never silently dropped: a chunk over the budget on its own is
        emitted alone rather than skipped."""
        records = [("big", "text", 5000)]
        assert [len(b) for b in embedder.batches(records, max_items=100,
                                                 max_tokens=1000)] == [1]

    def test_every_record_appears_exactly_once(self):
        records = [("c%d" % i, "t", 100) for i in range(37)]
        batched = [r for b in embedder.batches(records, max_items=10,
                                               max_tokens=250) for r in b]
        assert [r[0] for r in batched] == [r[0] for r in records]

    def test_no_records_yields_no_batches(self):
        assert list(embedder.batches([], max_items=10, max_tokens=100)) == []


class TestTheVectorLiteral:

    def test_renders_as_a_pgvector_literal(self):
        assert embedder.to_pgvector([1.0, 2.5, -3.0]) == "[1.0,2.5,-3.0]"

    def test_round_trips_through_the_expected_width(self):
        literal = embedder.to_pgvector([0.1] * embedder.DIMENSIONS)
        assert literal.count(",") == embedder.DIMENSIONS - 1


class TestTheResponseCheck:
    """Each of these would otherwise leave rows NULL while reporting success."""

    def test_a_matching_response_passes(self):
        embedder.check_response([[0.0] * 1536, [0.0] * 1536], expected=2)

    def test_a_short_response_is_refused(self):
        """The API returning fewer vectors than inputs, which would silently
        misalign every subsequent chunk_id with someone else's vector."""
        with pytest.raises(ValueError) as raised:
            embedder.check_response([[0.0] * 1536], expected=2)
        assert "2" in str(raised.value) and "1" in str(raised.value)

    def test_a_wrong_width_vector_is_refused(self):
        with pytest.raises(ValueError) as raised:
            embedder.check_response([[0.0] * 768], expected=1)
        assert "768" in str(raised.value)
        assert "1536" in str(raised.value)

    def test_the_check_looks_at_every_vector_not_just_the_first(self):
        with pytest.raises(ValueError):
            embedder.check_response([[0.0] * 1536, [0.0] * 4], expected=2)


@pytest.mark.live
def test_the_model_really_returns_the_pre_registered_width():
    """One real call, on one short string.

    Worth its cost: it is the only check that the *model* returns 1536 rather
    than the schema merely declaring it. If OpenAI ever changed the default
    width, every other test here would still pass and the load would fail
    11,621 times at the database boundary.
    """
    from dotenv import dotenv_values
    key = (dotenv_values(BACKEND / ".env").get("OPENAI_API_KEY") or "").strip()
    if not key or key.startswith("sk-test"):
        pytest.skip("no live OpenAI key in backend/.env")
    import openai
    try:
        response = openai.OpenAI(api_key=key).embeddings.create(
            model=embedder.MODEL, input=["goodwill impairment"],
        )
    except Exception as exc:
        pytest.skip(f"OpenAI unreachable: {type(exc).__name__}")
    assert len(response.data[0].embedding) == embedder.DIMENSIONS
