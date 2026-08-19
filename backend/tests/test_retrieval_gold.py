"""The hit definition, which every retrieval number rests on.

Pre-registered 2026-08-19 in EVALUATION-SPEC.md, before either index existed
and before any query was written:

    gold is (accession, quoted span), never a chunk id
    a retrieved chunk is a hit iff it is from a gold accession AND its text
        contains the gold span under the declared normalization
    the gold chunk set is derived from the store at scoring time -- every chunk
        containing the span -- so re-chunking cannot invalidate a query
    recall@k = 1 iff at least one gold chunk appears in the arm's top k
    `item` is recorded but is NOT part of the hit test

This module is written **before any query exists**, which is deliberate. A
validator written after the query set can be shaped, one exception at a time,
to accept the queries somebody already wrote; a validator written first has to
be argued with instead.

The normalization is the part most able to move a number quietly. Fold too
little and a span quoted with a typewriter apostrophe misses a passage that
plainly contains it -- the store holds 13,603 curly apostrophes. Fold too much
and distinct passages start matching. The rules are fixed and dated; these
tests pin each one separately, so a change to any of them fails loudly.

Written before evaluation/retrieval_gold.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
from evaluation import retrieval_gold as gold  # noqa: E402
from services import chunk_store  # noqa: E402


# --------------------------------------------------------------- normalize

class TestEachNormalizationRuleSeparately:
    """One test per pre-registered rule. A single combined test would let a
    dropped rule hide behind the others."""

    def test_casefolds(self):
        assert gold.normalize("Goodwill IMPAIRMENT") == "goodwill impairment"

    def test_collapses_runs_of_spaces(self):
        assert gold.normalize("a     b") == "a b"

    def test_collapses_newlines(self):
        """The store joins blocks with \\n inside one chunk, so a span that
        crosses a block boundary holds a newline the quoter typed as a space.
        This is the rule that makes cross-block spans matchable at all."""
        assert gold.normalize("first line\nsecond line") == "first line second line"

    def test_collapses_tabs_and_mixed_whitespace(self):
        assert gold.normalize("a \t\n  b") == "a b"

    def test_strips_leading_and_trailing_whitespace(self):
        assert gold.normalize("  padded  ") == "padded"

    def test_folds_the_right_single_quote(self):
        """13,603 of these in the store. The single highest-value fold."""
        assert gold.normalize("Grainger’s") == "grainger's"

    def test_folds_the_left_single_quote(self):
        assert gold.normalize("‘quoted’") == "'quoted'"

    def test_folds_both_double_quotes(self):
        assert gold.normalize("“the Company”") == '"the company"'

    def test_folds_the_em_dash(self):
        """21,839 in the store, the most common non-ascii character of all."""
        assert gold.normalize("risk — and reward") == "risk - and reward"

    def test_folds_the_en_dash(self):
        assert gold.normalize("2024–2025") == "2024-2025"

    def test_does_not_strip_punctuation(self):
        """Pre-registered as `nothing is stripped`. Removing punctuation would
        make `net sales, net` match `net sales net`, which are different
        passages in a financial statement."""
        assert gold.normalize("Item 1A. Risk Factors") == "item 1a. risk factors"

    def test_does_not_fold_bullets_or_section_marks(self):
        """6,354 bullets and 114 section marks in the store, none of them
        pre-registered for folding. Folding beyond the declared rules is as
        much a change to the measurement as folding too little."""
        assert "•" in gold.normalize("• first")
        assert "§" in gold.normalize("§ 13")

    def test_is_idempotent(self):
        once = gold.normalize("A  — B’s")
        assert gold.normalize(once) == once


# ---------------------------------------------------------- contains_span

class TestContainment:

    def test_finds_an_exact_span(self):
        assert gold.contains_span("the total assets were 1,234", "total assets")

    def test_finds_a_span_across_a_block_boundary(self):
        assert gold.contains_span("first line\nsecond line", "line second")

    def test_finds_a_span_quoted_with_a_typewriter_apostrophe(self):
        """The trap this whole normalization exists for: the filing says
        `Grainger’s` and a human quoting it types `Grainger's`."""
        assert gold.contains_span("Grainger’s products", "Grainger's products")

    def test_finds_a_span_quoted_with_a_hyphen_for_an_em_dash(self):
        assert gold.contains_span("risk — reward", "risk - reward")

    def test_rejects_a_span_that_is_not_there(self):
        assert not gold.contains_span("total assets", "total liabilities")

    def test_rejects_a_span_that_only_partly_overlaps(self):
        """The failure that would inflate every number: a substring test on
        words rather than on the span."""
        assert not gold.contains_span("total assets were", "assets were 1,234")

    def test_an_empty_span_is_refused_rather_than_matching_everything(self):
        """`"" in anything` is True, so an empty gold span would make every
        chunk a hit and every arm score 1.0."""
        with pytest.raises(ValueError):
            gold.contains_span("any text at all", "")

    def test_a_whitespace_only_span_is_refused_too(self):
        with pytest.raises(ValueError):
            gold.contains_span("any text at all", "   \n  ")


# --------------------------------------------------------- gold_chunk_ids

def _record(chunk_id, accession, text, item="1"):
    return {"chunk_id": chunk_id, "accession": accession, "text": text,
            "item": item, "ticker": "AAA", "period": "2025-12-31",
            "title": "Business", "index": 0, "first_page": 1, "last_page": 1,
            "tokens": 10}


class TestDerivingTheGoldSet:

    RECORDS = [
        _record("c1", "acc-1", "the quick brown fox"),
        _record("c2", "acc-1", "brown fox jumps over"),
        _record("c3", "acc-1", "something else entirely"),
        _record("c4", "acc-2", "the quick brown fox"),
    ]

    def test_returns_every_chunk_containing_the_span(self):
        """Overlap means a span near a boundary sits in two chunks. That is
        correct semantics, not a defect: the question is whether the answering
        text reached the reader."""
        assert gold.gold_chunk_ids(self.RECORDS, "acc-1", "brown fox") == \
            ["c1", "c2"]

    def test_is_scoped_to_the_accession(self):
        """`c4` holds the identical text in a different filing. Counting it
        would make a query about one issuer satisfiable by another."""
        assert gold.gold_chunk_ids(self.RECORDS, "acc-1", "quick brown") == ["c1"]

    def test_returns_empty_when_nothing_matches(self):
        assert gold.gold_chunk_ids(self.RECORDS, "acc-1", "not present") == []

    def test_ignores_item_entirely(self):
        """Pre-registered: `item` is recorded but is not part of the hit test,
        because HON's Items 1 and 7 sit under Item 1B and requiring equality
        would score a disclosed chunker limitation as a retrieval failure."""
        records = [_record("c1", "acc-1", "the quick brown fox", item="1B")]
        assert gold.gold_chunk_ids(records, "acc-1", "brown fox") == ["c1"]

    def test_accepts_several_gold_locations(self):
        locations = [("acc-1", "brown fox"), ("acc-2", "quick brown")]
        assert gold.gold_chunk_ids_for(self.RECORDS, locations) == \
            ["c1", "c2", "c4"]

    def test_a_span_matching_nothing_anywhere_is_detectable(self):
        """The pre-registered validation guard: a span matching zero chunks is
        a broken query, not a retrieval failure, and the set is refused."""
        assert gold.gold_chunk_ids_for(self.RECORDS,
                                       [("acc-1", "nowhere at all")]) == []


# ------------------------------------------------- the gold-set size cap

class TestTheGoldSetCap:
    """AMENDMENT 4, 2026-08-19, decided before any query was written.

    The rule already refused a span matching zero chunks. This is the same
    defect from the other end: measured over the store, a 4-word span can match
    382 chunks -- more than a whole median filing of 250 -- and a query with a
    gold set that size is satisfied by recall@5 essentially by accident. It
    would enter the pooled number as a success, and the failure is silent in
    the direction that flatters the retriever.

    A span-length floor does not fix it: the maximum was still 14 chunks at
    both 12 and 20 words. Length is guidance; the cap is the guard.
    """

    RECORDS = ([_record(f"c{i}", "acc-1", "shared boilerplate sentence here")
                for i in range(9)]
               + [_record("u1", "acc-1", "a uniquely worded passage")])

    def test_the_cap_is_five(self):
        assert gold.MAX_GOLD_CHUNKS == 5

    def test_the_advisory_span_floor_is_twelve_words(self):
        assert gold.MIN_SPAN_WORDS == 12

    def test_a_normal_gold_set_passes(self):
        assert gold.validate_gold(self.RECORDS,
                                  [("acc-1", "uniquely worded")]) == []

    def test_a_span_matching_nothing_is_refused(self):
        problems = gold.validate_gold(self.RECORDS, [("acc-1", "not present")])
        assert len(problems) == 1
        assert "no chunk" in problems[0]

    def test_a_span_matching_too_many_chunks_is_refused(self):
        """Nine chunks share the boilerplate; the cap is five."""
        problems = gold.validate_gold(self.RECORDS,
                                      [("acc-1", "shared boilerplate")])
        assert len(problems) == 1
        assert "9" in problems[0]
        assert "5" in problems[0]

    def test_exactly_at_the_cap_is_allowed(self):
        """The boundary, asserted in the direction that matters: the cap is
        inclusive, so a five-chunk gold set is usable and a six-chunk one is
        not."""
        records = [_record(f"c{i}", "acc-1", "repeated phrase") for i in range(5)]
        assert gold.validate_gold(records, [("acc-1", "repeated phrase")]) == []

    def test_one_over_the_cap_is_refused(self):
        records = [_record(f"c{i}", "acc-1", "repeated phrase") for i in range(6)]
        assert gold.validate_gold(records, [("acc-1", "repeated phrase")]) != []

    def test_the_cap_applies_to_the_union_not_each_location(self):
        """The union is what decides how easy recall@k is, so it is what the
        cap bounds. A query naming several locations must keep its *total*
        gold set inside the cap, which in practice means two or three."""
        records = ([_record(f"a{i}", "acc-1", "phrase one") for i in range(3)]
                   + [_record(f"b{i}", "acc-2", "phrase two") for i in range(3)])
        problems = gold.validate_gold(
            records, [("acc-1", "phrase one"), ("acc-2", "phrase two")],
        )
        assert problems != [], "6 chunks across two locations should be refused"

    def test_a_short_span_is_advised_but_not_refused(self):
        """Guidance, not a guard -- because length demonstrably does not
        prevent the defect the cap prevents."""
        assert gold.validate_gold(self.RECORDS,
                                  [("acc-1", "uniquely worded")]) == []
        notes = gold.advisory_notes([("acc-1", "uniquely worded")])
        assert len(notes) == 1
        assert "12" in notes[0]

    def test_a_long_enough_span_draws_no_advisory(self):
        span = " ".join(f"word{i}" for i in range(14))
        assert gold.advisory_notes([("acc-1", span)]) == []


# ------------------------------------------------------------------ hit@k

class TestHitAtK:

    def test_hit_when_a_gold_chunk_is_in_the_top_k(self):
        assert gold.hit_at_k(["x", "c1", "y"], ["c1"], k=5)

    def test_miss_when_the_gold_chunk_is_below_k(self):
        assert not gold.hit_at_k(["x", "y", "c1"], ["c1"], k=2)

    def test_k_is_inclusive(self):
        """recall@1 must mean `rank 1`, not `rank 0`."""
        assert gold.hit_at_k(["c1"], ["c1"], k=1)
        assert not gold.hit_at_k(["x", "c1"], ["c1"], k=1)

    def test_any_one_of_several_gold_chunks_suffices(self):
        assert gold.hit_at_k(["x", "c2"], ["c1", "c2"], k=5)

    def test_no_gold_is_refused_rather_than_scored_a_miss(self):
        """A query with an empty gold set is broken. Scoring it 0 would fold a
        query-set defect into the retrieval number, which is precisely the
        confusion the validation guard exists to prevent."""
        with pytest.raises(ValueError):
            gold.hit_at_k(["x", "y"], [], k=5)

    def test_an_empty_ranking_is_a_miss_not_an_error(self):
        """A sparse query matching nothing is an ordinary outcome."""
        assert not gold.hit_at_k([], ["c1"], k=5)


# ------------------------------------------------------- the real corpus

@pytest.mark.corpus
class TestAgainstTheRealStore:
    """Skips cleanly when the store is absent (RAG_FILINGS_DIR).

    The unit tests above use six-word fixtures. These use the 11,621 chunks the
    eval will actually run against, because the normalization's whole job is to
    survive real filing text -- curly apostrophes, em dashes, table cells on
    their own lines -- and a fixture cannot demonstrate that.
    """

    @pytest.fixture(scope="class")
    def records(self):
        path = chunk_store.default_path()
        if not path.exists():
            pytest.skip(f"no chunk store at {path}")
        return chunk_store.read(path)

    def test_the_store_is_the_expected_size(self, records):
        """Pins the corpus the pre-registered figures describe."""
        assert len(records) == 11621
        assert len({r["accession"] for r in records}) == 44

    def test_a_span_quoted_from_a_chunk_finds_that_chunk(self, records):
        """The base case, over 200 real chunks spread through the store."""
        for record in records[::58]:
            words = record["text"].split()
            if len(words) < 12:
                continue
            span = " ".join(words[4:11])
            found = gold.gold_chunk_ids(records, record["accession"], span)
            assert record["chunk_id"] in found, (
                f"{record['chunk_id']} does not contain a span taken from "
                f"its own text: {span!r}"
            )

    def test_a_span_with_a_curly_apostrophe_matches_a_typewriter_one(self,
                                                                    records):
        """Real text, real apostrophes -- the failure a fixture cannot show."""
        curly = [r for r in records if "’" in r["text"]]
        assert len(curly) > 100, f"only {len(curly)} chunks carry a curly quote"
        checked = 0
        for record in curly[::200]:
            index = record["text"].index("’")
            span = record["text"][max(0, index - 30):index + 30]
            if len(span.split()) < 3:
                continue
            typed = span.replace("’", "'")
            assert gold.contains_span(record["text"], typed), (
                f"{record['chunk_id']}: a span retyped with a straight "
                f"apostrophe stopped matching"
            )
            checked += 1
        assert checked > 5, f"only exercised {checked} chunks"

    def test_a_span_from_one_filing_does_not_match_another(self, records):
        """Guards the accession scope against real near-duplicate boilerplate.

        Filings share a great deal of standard language, which is exactly why
        the scope matters: without it, a query about one issuer would be
        satisfiable by another issuer's identical forward-looking-statements
        notice.
        """
        by_accession = {}
        for record in records:
            by_accession.setdefault(record["accession"], []).append(record)
        accessions = sorted(by_accession)
        source = by_accession[accessions[0]][30]
        words = source["text"].split()
        if len(words) >= 12:
            span = " ".join(words[4:11])
            for other in accessions[1:]:
                assert source["chunk_id"] not in gold.gold_chunk_ids(
                    records, other, span
                )

    def test_no_chunk_is_empty_after_normalization(self, records):
        """An empty normalized chunk would match any span containment test that
        forgot to refuse an empty needle."""
        empty = [r["chunk_id"] for r in records if not gold.normalize(r["text"])]
        assert not empty, f"{len(empty)} chunks normalize to nothing"
