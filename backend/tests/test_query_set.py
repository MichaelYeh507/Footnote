"""The query set: its schema, its strata, and the guards on how it was written.

The query set is the input to every number Phase 5 reports, so a defect here is
not a bug — it is a wrong published result. The guards are therefore about
*provenance* as much as correctness:

  * the pre-registered strata and counts (AMENDMENT 3: 25 + 25 + 15)
  * every gold span resolves in the store, and to at most 5 chunks (AMENDMENT 4)
  * the smoke-query constraint disclosed 2026-08-19 — no query may take its gold
    from a goodwill-impairment passage in MA, DOW or WYNN
  * the conceptual stratum's own definition: no content word shared with its
    gold span, which is the rule that makes "conceptual" checkable rather than
    a judgement call

The file itself is data and lives outside the repo, beside the filings and the
chunk store. These tests skip cleanly until it exists, so they can be committed
before the queries are written — which is the point, since a validator written
afterwards can be shaped to accept whatever was already authored.

Written before evaluation/query_set.py existed (red first).
"""

import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import query_set  # noqa: E402


def _query(qid="q001", stratum="exact_entity", text="what was total revenue",
           **overrides):
    query = {
        "query_id": qid,
        "stratum": stratum,
        "query": text,
        "gold": [{"accession": "acc-1", "span": "a quoted span of filing text"}],
        "note": "",
    }
    query.update(overrides)
    return query


class TestThePreRegisteredShape:

    def test_the_strata_are_the_amended_two_plus_unanswerable(self):
        """AMENDMENT 3 dropped Mixed. Three names, not four."""
        assert query_set.STRATA == ("exact_entity", "conceptual", "unanswerable")

    def test_the_counts_are_25_25_15(self):
        assert query_set.TARGET_COUNTS == {
            "exact_entity": 25, "conceptual": 25, "unanswerable": 15,
        }

    def test_the_total_is_sixty_five(self):
        assert sum(query_set.TARGET_COUNTS.values()) == 65

    def test_the_answerable_total_is_fifty(self):
        answerable = sum(v for k, v in query_set.TARGET_COUNTS.items()
                         if k != "unanswerable")
        assert answerable == 50


class TestRecordValidation:

    def test_a_well_formed_query_passes(self):
        assert query_set.check_record(_query()) == []

    def test_a_missing_field_is_caught(self):
        broken = _query()
        del broken["stratum"]
        assert any("stratum" in p for p in query_set.check_record(broken))

    def test_an_unknown_stratum_is_caught(self):
        problems = query_set.check_record(_query(stratum="mixed"))
        assert any("mixed" in p for p in problems)

    def test_an_empty_query_string_is_caught(self):
        assert query_set.check_record(_query(text="   ")) != []

    def test_an_answerable_query_needs_gold(self):
        assert query_set.check_record(_query(gold=[])) != []

    def test_an_unanswerable_query_must_have_no_gold(self):
        """The 15 unanswerable carry no gold by definition — recall is
        undefined for them, and they are scored on abstention by the QA layer.
        Gold on one would silently enter the recall denominator."""
        problems = query_set.check_record(
            _query(stratum="unanswerable",
                   gold=[{"accession": "acc-1", "span": "x"}]))
        assert any("unanswerable" in p for p in problems)

    def test_an_unanswerable_query_with_empty_gold_passes(self):
        assert query_set.check_record(
            _query(stratum="unanswerable", gold=[])) == []

    def test_a_gold_entry_missing_its_span_is_caught(self):
        assert query_set.check_record(
            _query(gold=[{"accession": "acc-1"}])) != []


class TestTheConceptualRule:
    """AMENDMENT 3 made the stratum boundary checkable: a query is conceptual
    if it shares NO content word with its gold span. Without that, 'conceptual'
    is decided query by query by whoever writes it — a dial in the definition
    of the stratum whose number gets published."""

    def test_no_shared_content_word_passes(self):
        assert query_set.shares_content_word(
            "how many people does the firm employ",
            "our workforce numbered 24,000 at year end") is False

    def test_a_shared_content_word_is_detected(self):
        assert query_set.shares_content_word(
            "what was the goodwill impairment",
            "we recorded a goodwill impairment of $412 million") is True

    def test_stopwords_do_not_count_as_shared(self):
        """'the', 'of', 'was' appear in nearly every span; counting them would
        make every query lexical."""
        assert query_set.shares_content_word(
            "what was the size of the", "the size of the matter was") is True
        assert query_set.shares_content_word(
            "what was it", "the of and was a") is False

    def test_matching_is_case_and_punctuation_insensitive(self):
        assert query_set.shares_content_word(
            "Goodwill, impaired?", "goodwill impairment") is True

    def test_stemming_catches_a_morphological_variant(self):
        """'impaired' vs 'impairment' is lexical overlap in every sense that
        matters to the sparse arm, which stems both to 'impair'. Treating them
        as unrelated would file a lexical query under conceptual.

        The pair below is the ONLY overlap between the two strings, which is
        the point. The first version of this test used 'was goodwill impaired'
        against '... a goodwill impairment' and passed with stemming removed —
        'goodwill' was shared outright, so the assertion never exercised the
        stemmer at all.
        """
        assert query_set.shares_content_word(
            "was it impaired", "the company recorded an impairment") is True

    def test_without_a_shared_stem_there_is_no_overlap(self):
        """The negative half of the pair above, so the stemmer cannot pass by
        simply returning True for everything."""
        assert query_set.shares_content_word(
            "was it impaired", "the company employs 24,000 people") is False

    def test_a_conceptual_query_sharing_a_word_is_flagged(self):
        problems = query_set.check_record(_query(
            stratum="conceptual", text="what was the goodwill impairment",
            gold=[{"accession": "acc-1",
                   "span": "we recorded a goodwill impairment of $412 million"}]))
        assert any("content word" in p for p in problems)

    def test_an_exact_entity_query_sharing_a_word_is_fine(self):
        """Sharing words is the *point* of the exact-entity stratum."""
        assert query_set.check_record(_query(
            stratum="exact_entity", text="what was the goodwill impairment",
            gold=[{"accession": "acc-1",
                   "span": "we recorded a goodwill impairment"}])) == []


class TestTheSmokeQueryConstraint:
    """Disclosed 2026-08-19 and published: retrieval output was seen for one
    sparse query before the set was written, so no query may take its gold from
    a goodwill-impairment passage in MA, DOW or WYNN."""

    def test_the_constrained_tickers_are_the_disclosed_three(self):
        assert query_set.SMOKE_TICKERS == ("DOW", "MA", "WYNN")

    def test_a_goodwill_span_in_a_constrained_filing_is_refused(self):
        records = [{"chunk_id": "c1", "accession": "acc-1", "ticker": "MA",
                    "item": "8",
                    "text": "we recorded a goodwill impairment of $412 million"}]
        problems = query_set.check_smoke_constraint(
            [{"accession": "acc-1",
              "span": "recorded a goodwill impairment"}], records)
        assert len(problems) == 1
        assert "MA" in problems[0]

    def test_a_goodwill_span_elsewhere_is_fine(self):
        records = [{"chunk_id": "c1", "accession": "acc-9", "ticker": "LLY",
                    "item": "8",
                    "text": "we recorded a goodwill impairment of $412 million"}]
        assert query_set.check_smoke_constraint(
            [{"accession": "acc-9",
              "span": "recorded a goodwill impairment"}], records) == []

    def test_a_non_goodwill_span_in_a_constrained_filing_is_fine(self):
        """The exposure was one topic, not three whole issuers. Excluding MA,
        DOW and WYNN entirely would be a bigger distortion than the one being
        corrected."""
        records = [{"chunk_id": "c1", "accession": "acc-1", "ticker": "MA",
                    "item": "1",
                    "text": "our payment network operates in 200 countries"}]
        assert query_set.check_smoke_constraint(
            [{"accession": "acc-1",
              "span": "payment network operates in 200 countries"}],
            records) == []


class TestTheWholeSet:

    def _set(self, counts=None):
        counts = counts or {"exact_entity": 2, "conceptual": 2,
                            "unanswerable": 1}
        queries, n = [], 0
        for stratum, count in counts.items():
            for _ in range(count):
                n += 1
                queries.append(_query(
                    qid=f"q{n:03d}", stratum=stratum,
                    # Distinct text per query: the duplicate-text check is real
                    # and a fixture that repeats one string trips it.
                    text=f"question number {n}",
                    gold=[] if stratum == "unanswerable"
                    else [{"accession": "acc-1", "span": f"span {n}"}]))
        return queries

    def test_duplicate_query_ids_are_caught(self):
        queries = self._set()
        queries[1]["query_id"] = queries[0]["query_id"]
        assert any("duplicate" in p.lower()
                   for p in query_set.check_set(queries))

    def test_duplicate_query_text_is_caught(self):
        """Two identical questions are one query counted twice, which inflates
        the denominator without adding evidence."""
        queries = self._set()
        queries[1]["query"] = queries[0]["query"]
        assert any("duplicate" in p.lower()
                   for p in query_set.check_set(queries))

    def test_the_stratum_counts_are_checked_against_the_targets(self):
        problems = query_set.check_set(self._set())
        assert any("exact_entity" in p and "25" in p for p in problems)

    def test_a_complete_set_reports_no_count_problems(self):
        queries = self._set(query_set.TARGET_COUNTS)
        assert query_set.check_set(queries) == []

    def test_counts_are_reported_per_stratum(self):
        counts = query_set.stratum_counts(self._set())
        assert counts == {"exact_entity": 2, "conceptual": 2, "unanswerable": 1}
