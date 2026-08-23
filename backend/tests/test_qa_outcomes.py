"""The QA outcome taxonomy: mechanical, exhaustive, one hit test.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
failures this file is written against:

  **Abstention by anything but null.** A prose refusal, an empty string, a
  "cannot answer" -- all are answered items. Only `"answer": null` abstains,
  and null abstains even when a citation is bizarrely attached, because the
  registered rule is that the answer field decides.

  **A boolean citation passing as excerpt 1.** `True` is an `int` to Python;
  a schema check that misses it turns a malformed response into a supported
  answer citing the top excerpt.

  **Quote support decided by a second implementation.** Containment must run
  through `retrieval_gold.normalize`/`contains_span`. The discriminating
  tests below carry the guard the project's own lessons require: they first
  prove raw containment fails, so they can only pass through the published
  normalization.

  **Wrong-filing drifting off its definition.** The flag is "contains a gold
  span, in a non-gold accession". A non-gold chunk *inside* a gold accession
  is the right filing and the wrong chunk, and must not raise it.
"""

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import qa_outcomes  # noqa: E402
from evaluation.qa_outcomes import QAOutcome  # noqa: E402

parse = qa_outcomes.parse_response


def test_parse_accepts_the_all_null_abstention():
    parsed = parse('{"answer": null, "citation": null, "quote": null}')
    assert parsed == {"ok": True, "answer": None, "citation": None,
                      "quote": None}


def test_parse_accepts_an_answered_response():
    parsed = parse('{"answer": "42 mills", "citation": 2, "quote": "42"}')
    assert parsed["ok"] and parsed["citation"] == 2


def test_parse_tolerates_an_extra_field():
    parsed = parse('{"answer": null, "citation": null, "quote": null, '
                   '"confidence": 0.9}')
    assert parsed["ok"]


def test_parse_refuses_non_json_and_non_objects():
    assert not parse("I cannot answer that.")["ok"]
    assert not parse('["answer", null]')["ok"]
    assert not parse("")["ok"]


def test_parse_refuses_a_missing_field():
    parsed = parse('{"answer": null, "citation": null}')
    assert not parsed["ok"]
    assert "quote" in parsed["reason"]


def test_parse_refuses_wrong_types_including_the_boolean_trap():
    assert not parse('{"answer": 7, "citation": 1, "quote": "q"}')["ok"]
    assert not parse('{"answer": "a", "citation": "1", "quote": "q"}')["ok"]
    assert not parse('{"answer": "a", "citation": true, "quote": "q"}')["ok"]
    assert not parse('{"answer": "a", "citation": 1, "quote": 3}')["ok"]


_SPAN = "more than 5,000 primary suppliers worldwide provide the businesses"


def _excerpts():
    return [
        {"chunk_id": "gold-1", "accession": "acc-A", "ticker": "GWW",
         "period": "2024-12-31", "item": "1",
         "text": f"Filler ahead. {_SPAN}. Filler behind."},
        {"chunk_id": "same-filing", "accession": "acc-A", "ticker": "GWW",
         "period": "2024-12-31", "item": "7",
         "text": "This chunk is in the gold filing but holds no span."},
        {"chunk_id": "twin-year", "accession": "acc-B", "ticker": "GWW",
         "period": "2025-12-31", "item": "1",
         "text": f"Identical boilerplate. {_SPAN}. Same words next year."},
        {"chunk_id": "cold-1", "accession": "acc-C", "ticker": "LLY",
         "period": "2025-12-31", "item": "1", "text": "Unrelated."},
        {"chunk_id": "cold-2", "accession": "acc-C", "ticker": "LLY",
         "period": "2025-12-31", "item": "7", "text": "Also unrelated."},
    ]


_GOLD_IDS = ["gold-1"]
_LOCATIONS = [("acc-A", _SPAN)]


def _classify(raw):
    return qa_outcomes.classify(parse(raw), _excerpts(), _GOLD_IDS,
                                _LOCATIONS)


def test_malformed_is_its_own_outcome():
    result = _classify("not json at all")
    assert result["outcome"] is QAOutcome.MALFORMED


def test_null_answer_abstains_even_with_a_citation_attached():
    result = _classify('{"answer": null, "citation": 1, "quote": "x"}')
    assert result["outcome"] is QAOutcome.ABSTAINED


def test_answer_without_citation_is_unsupported():
    result = _classify('{"answer": "5,000", "citation": null, '
                       '"quote": "more than 5,000"}')
    assert result["outcome"] is QAOutcome.UNSUPPORTED


def test_citation_out_of_range_is_unsupported():
    for citation in (0, 6, -1):
        result = _classify('{"answer": "a", "citation": %d, "quote": "q"}'
                           % citation)
        assert result["outcome"] is QAOutcome.UNSUPPORTED


def test_missing_or_blank_quote_is_unsupported():
    for quote in ("null", '""', '"   "'):
        result = _classify('{"answer": "a", "citation": 1, "quote": %s}'
                           % quote)
        assert result["outcome"] is QAOutcome.UNSUPPORTED


def test_quote_not_in_the_cited_excerpt_is_unsupported():
    result = _classify('{"answer": "a", "citation": 4, '
                       '"quote": "more than 5,000 primary suppliers"}')
    assert result["outcome"] is QAOutcome.UNSUPPORTED


def test_quote_support_runs_through_the_published_normalization():
    quote = "MORE  than 5,000   primary suppliers"
    # The guard: raw containment must fail, or this test could pass through
    # a plain `in` and prove nothing about the normalization.
    assert quote not in _excerpts()[0]["text"]
    result = _classify('{"answer": "more than 5,000", "citation": 1, '
                       '"quote": "%s"}' % quote)
    assert result["outcome"] is QAOutcome.SUPPORTED_GOLD
    assert result["cited_chunk_id"] == "gold-1"


def test_supported_citation_of_a_gold_chunk_is_supported_gold():
    result = _classify('{"answer": "more than 5,000", "citation": 1, '
                       '"quote": "more than 5,000 primary suppliers"}')
    assert result["outcome"] is QAOutcome.SUPPORTED_GOLD
    assert result["wrong_filing"] is False


def test_wrong_year_twin_is_nongold_and_flagged():
    result = _classify('{"answer": "more than 5,000", "citation": 3, '
                       '"quote": "more than 5,000 primary suppliers"}')
    assert result["outcome"] is QAOutcome.SUPPORTED_NONGOLD
    assert result["wrong_filing"] is True
    assert result["cited_chunk_id"] == "twin-year"


def test_right_filing_wrong_chunk_is_nongold_and_not_flagged():
    result = _classify('{"answer": "a", "citation": 2, '
                       '"quote": "holds no span"}')
    assert result["outcome"] is QAOutcome.SUPPORTED_NONGOLD
    assert result["wrong_filing"] is False


def test_unrelated_supported_citation_is_nongold_unflagged():
    result = _classify('{"answer": "a", "citation": 4, '
                       '"quote": "Unrelated."}')
    assert result["outcome"] is QAOutcome.SUPPORTED_NONGOLD
    assert result["wrong_filing"] is False


_SPAN_B = "twelve entirely different words fill this second gold span "\
    "for the crossover case"


def test_wrong_filing_discriminates_on_the_accession_alone():
    """A multi-location query: the cited chunk contains location B's span.

    In acc-A -- a gold accession -- that is the right corpus and the wrong
    location, NOT the duplicate-span case: unflagged. The same text in
    acc-Z, outside every gold accession, is the twin case: flagged. The
    pair is the guard -- span containment is identical in both, so only
    the accession scope can separate them.
    """
    def crossover(accession):
        return [{"chunk_id": "crossover", "accession": accession,
                 "ticker": "GWW", "period": "2024-12-31", "item": "7",
                 "text": f"Crossover. {_SPAN_B}. Tail."}]

    parsed = parse('{"answer": "a", "citation": 1, "quote": "%s"}'
                   % _SPAN_B[:40])
    locations = [("acc-A", _SPAN), ("acc-B", _SPAN_B)]
    inside = qa_outcomes.classify(parsed, crossover("acc-A"),
                                  gold_ids=["gold-1"],
                                  gold_locations=locations)
    outside = qa_outcomes.classify(parsed, crossover("acc-Z"),
                                   gold_ids=["gold-1"],
                                   gold_locations=locations)
    assert inside["outcome"] is QAOutcome.SUPPORTED_NONGOLD
    assert outside["outcome"] is QAOutcome.SUPPORTED_NONGOLD
    assert inside["wrong_filing"] is False
    assert outside["wrong_filing"] is True


def test_unanswerable_queries_cannot_reach_supported_gold():
    parsed = parse('{"answer": "a", "citation": 1, '
                   '"quote": "more than 5,000 primary suppliers"}')
    result = qa_outcomes.classify(parsed, _excerpts(), gold_ids=[],
                                  gold_locations=[])
    assert result["outcome"] is QAOutcome.SUPPORTED_NONGOLD
    assert result["wrong_filing"] is False
