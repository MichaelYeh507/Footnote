"""The freeze: what it must detect, and the ways a weaker one would not.

The freeze is the mechanical replacement for "all 65 are approved", which until
now meant counting lines in a log that records no query text. Every test below
is one edit the freeze has to catch, or one thing it must not mistake for an
edit.

Two of them are load-bearing against a plausible simpler design:

  * `test_changing_a_ticker_moves_the_hash` -- a freeze that hashed only
    `query`, `stratum` and `span` would pass this. The reviewer read the ticker
    on the card, so an edited ticker is an edited review.
  * `test_collapsing_whitespace_inside_a_span_moves_the_hash` -- the published
    normalization collapses whitespace at scoring time, and a freeze that
    reused it would call an edited span unchanged.

Written against fixtures, never against the live set: the query set is data and
lives outside the repo, so a suite that needed it could not run in a clone.
"""

import copy
import hashlib
import json
import pathlib
import random
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from evaluation import query_freeze as freeze  # noqa: E402


def answerable(qid="q001", stratum="exact_entity",
               text="how many primary suppliers does the issuer rely on",
               span="More than 5,000 primary suppliers worldwide provide goods.",
               ticker="GWW", item="1", accession="0000277135-25-000010"):
    return {
        "query_id": qid,
        "stratum": stratum,
        "query": text,
        "gold": [{"accession": accession, "ticker": ticker, "item": item,
                  "span": span}],
    }


def unanswerable(qid="q051", text="what was the chief executive's bonus",
                 why="stated only in the proxy statement"):
    return {"query_id": qid, "stratum": "unanswerable", "query": text,
            "gold": [], "why_unanswerable": why}


def small_set():
    return [answerable("q001"),
            answerable("q002", stratum="conceptual",
                       text="how does the issuer source what it sells",
                       ticker="LLY", item="1A",
                       accession="0000059478-26-000013"),
            unanswerable("q003")]


def edited(record, **overrides):
    """A copy of one query with fields replaced -- gold keys via `gold_`.

    The parameter is `record` rather than `query` so that `query=` can be
    overridden like any other field.
    """
    changed = copy.deepcopy(record)
    for key, value in overrides.items():
        if key.startswith("gold_"):
            changed["gold"][0][key[len("gold_"):]] = value
        else:
            changed[key] = value
    return changed


class TestTheCanonicalForm:

    def test_key_order_does_not_change_the_hash(self):
        """The set is read from JSON; a re-serialisation must be stable, or
        every hash would move whenever the file was rewritten."""
        query = answerable()
        reordered = {key: query[key] for key in reversed(list(query))}
        assert freeze.query_sha256(reordered) == freeze.query_sha256(query)

    def test_a_json_round_trip_does_not_change_the_hash(self):
        query = answerable()
        reparsed = json.loads(json.dumps(query, ensure_ascii=False))
        assert freeze.query_sha256(reparsed) == freeze.query_sha256(query)

    def test_editing_the_query_text_moves_the_hash(self):
        before = answerable()
        after = edited(before, query="how many suppliers, in FY2025")
        assert freeze.query_sha256(after) != freeze.query_sha256(before)

    def test_editing_one_character_of_a_span_moves_the_hash(self):
        before = answerable()
        after = edited(before, gold_span=before["gold"][0]["span"]
                       .replace("5,000", "6,000"))
        assert freeze.query_sha256(after) != freeze.query_sha256(before)

    def test_collapsing_whitespace_inside_a_span_moves_the_hash(self):
        """`retrieval_gold.normalize` collapses whitespace runs at scoring
        time. A freeze that reused that normalization would report an edited
        span as unchanged -- it is applied to a copy of the span, never to the
        stored one, and the freeze records what is stored."""
        before = answerable(span="net sales,  net of  returns")
        after = answerable(span="net sales, net of returns")
        assert freeze.query_sha256(after) != freeze.query_sha256(before)

    def test_changing_only_the_case_of_a_span_moves_the_hash(self):
        before = answerable(span="More than 5,000 primary suppliers")
        after = answerable(span="more than 5,000 primary suppliers")
        assert freeze.query_sha256(after) != freeze.query_sha256(before)

    def test_changing_a_ticker_moves_the_hash(self):
        """The ticker is on the review card. A hash over a chosen subset of
        fields would let it change with every hash still matching."""
        before = answerable(ticker="GWW")
        after = answerable(ticker="LLY")
        assert freeze.query_sha256(after) != freeze.query_sha256(before)

    def test_changing_an_item_moves_the_hash(self):
        assert freeze.query_sha256(answerable(item="7")) != \
            freeze.query_sha256(answerable(item="1"))

    def test_changing_an_accession_moves_the_hash(self):
        """Gold is accession-scoped, so this one decides what counts as a
        hit."""
        assert freeze.query_sha256(answerable(accession="0000-1")) != \
            freeze.query_sha256(answerable(accession="0000-2"))

    def test_changing_why_unanswerable_moves_the_hash(self):
        """It is rendered on the card, so the reviewer judged it."""
        assert freeze.query_sha256(unanswerable(why="not in a 10-K")) != \
            freeze.query_sha256(unanswerable(why="only in the proxy"))

    def test_reordering_gold_locations_moves_the_hash(self):
        first = {"accession": "a", "ticker": "T", "item": "1", "span": "one"}
        second = {"accession": "b", "ticker": "U", "item": "7", "span": "two"}
        before = {"query_id": "q001", "stratum": "exact_entity",
                  "query": "q", "gold": [first, second]}
        after = {"query_id": "q001", "stratum": "exact_entity",
                 "query": "q", "gold": [second, first]}
        assert freeze.query_sha256(after) != freeze.query_sha256(before)

    def test_a_query_id_alone_cannot_be_hashed(self):
        """The hash binds a verdict to text. A caller holding only an id must
        not be able to produce one, or the decision log would carry a hash
        that binds nothing."""
        with pytest.raises(TypeError, match="whole record"):
            freeze.query_sha256("q001")

    def test_a_record_without_a_query_id_is_refused(self):
        with pytest.raises(ValueError, match="query_id"):
            freeze.query_sha256({"stratum": "exact_entity", "query": "q",
                                 "gold": []})


class TestTheSetDigest:

    def test_the_digest_ignores_the_order_of_the_query_file(self):
        """Scoring keys by query_id, so reordering lines is not a change to
        the set and must not read as one."""
        queries = small_set()
        shuffled = queries[:]
        random.Random(0).shuffle(shuffled)
        assert freeze.set_digest(freeze.manifest_entries(shuffled)) == \
            freeze.set_digest(freeze.manifest_entries(queries))

    def test_the_digest_ignores_the_order_of_the_entries_it_is_given(self):
        """`manifest_entries` already sorts, so the sort inside `digest_input`
        looks redundant until `verify` recomputes a digest over rows read back
        from a file -- where the order is whatever the file holds."""
        entries = freeze.manifest_entries(small_set())
        shuffled = entries[:]
        random.Random(1).shuffle(shuffled)
        assert freeze.set_digest(shuffled) == freeze.set_digest(entries)

    def test_editing_any_query_moves_the_digest(self):
        queries = small_set()
        before = freeze.set_digest(freeze.manifest_entries(queries))
        queries[1]["query"] = "something else entirely"
        assert freeze.set_digest(freeze.manifest_entries(queries)) != before

    def test_removing_a_query_moves_the_digest(self):
        queries = small_set()
        before = freeze.set_digest(freeze.manifest_entries(queries))
        assert freeze.set_digest(freeze.manifest_entries(queries[:-1])) != before

    def test_adding_a_query_moves_the_digest(self):
        queries = small_set()
        before = freeze.set_digest(freeze.manifest_entries(queries))
        queries.append(answerable("q004"))
        assert freeze.set_digest(freeze.manifest_entries(queries)) != before

    def test_renaming_a_query_moves_the_digest(self):
        """Renaming is an edit like any other -- the id is inside the record,
        so the query's own hash moves with it."""
        queries = small_set()
        before = freeze.set_digest(freeze.manifest_entries(queries))
        queries[0]["query_id"] = "q099"
        assert freeze.set_digest(freeze.manifest_entries(queries)) != before

    def test_the_digest_binds_each_hash_to_its_query_id(self):
        """The line is `id  hash`, not `hash`. Two manifests holding the same
        hashes under different ids are different sets -- the decision log and
        the freeze both key by id -- and a digest over hashes alone could not
        tell these two apart."""
        before = [{"query_id": "q001", "sha256": "a" * 64},
                  {"query_id": "q002", "sha256": "b" * 64}]
        after = [{"query_id": "q001", "sha256": "a" * 64},
                 {"query_id": "q003", "sha256": "b" * 64}]
        assert freeze.set_digest(after) != freeze.set_digest(before)

    def test_the_digest_is_the_documented_formula(self):
        """Pinned against a hand-computed value so the rule in the docstring
        and the rule in the code cannot drift apart."""
        entries = freeze.manifest_entries(small_set())
        expected = hashlib.sha256("".join(
            entry["query_id"] + "  " + entry["sha256"] + "\n"
            for entry in sorted(entries, key=lambda e: e["query_id"])
        ).encode("utf-8")).hexdigest()
        assert freeze.set_digest(entries) == expected

    def test_a_duplicate_query_id_is_refused(self):
        """Two entries under one key: the digest stays well formed and
        `verify`, which keys by id, would compare one and never mention the
        other."""
        with pytest.raises(ValueError, match="duplicate query_id"):
            freeze.manifest_entries([answerable("q001"), answerable("q001")])


class TestComposition:

    def test_it_counts_strata_accessions_tickers_and_items(self):
        counted = freeze.composition(small_set())
        assert counted["queries"] == 3
        assert counted["answerable"] == 2
        assert counted["strata"] == {"conceptual": 1, "exact_entity": 1,
                                     "unanswerable": 1}
        assert counted["distinct_accessions"] == 2
        assert counted["distinct_tickers"] == 2
        assert counted["gold_locations"] == 2
        assert counted["items"] == {"1": 1, "1A": 1}

    def test_an_unanswerable_query_contributes_no_location(self):
        """It carries no gold by rule, so it must not enter any denominator
        that describes where gold sits."""
        counted = freeze.composition([unanswerable()])
        assert counted["gold_locations"] == 0
        assert counted["distinct_accessions"] == 0
        assert counted["items"] == {}

    def test_a_missing_item_is_counted_as_front_matter(self):
        """Some gold sits ahead of Item 1; DGX and CTSH have no Item 8 chunks
        at all and their financial content lands there."""
        counted = freeze.composition([answerable(item=None)])
        assert counted["items"] == {"front matter": 1}


class TestApprovalBinding:

    def decisions_for(self, queries, **overrides):
        made = {query["query_id"]: {"query_id": query["query_id"],
                                    "verdict": "approved",
                                    "note": "",
                                    freeze.DECISION_HASH_FIELD:
                                        freeze.query_sha256(query)}
                for query in queries}
        made.update(overrides)
        return made

    def test_hash_bound_approvals_pass_with_nothing_unbound(self):
        queries = small_set()
        result = freeze.check_approvals(queries, self.decisions_for(queries))
        assert result["problems"] == []
        assert result["unbound"] == []
        assert result["bound"] == ["q001", "q002", "q003"]

    def test_a_query_with_no_decision_is_a_problem(self):
        queries = small_set()
        decisions = self.decisions_for(queries)
        del decisions["q002"]
        result = freeze.check_approvals(queries, decisions)
        assert result["problems"] == ["q002: no decision in the log"]

    def test_a_rejected_query_is_a_problem(self):
        queries = small_set()
        decisions = self.decisions_for(queries)
        decisions["q003"]["verdict"] = "rejected"
        result = freeze.check_approvals(queries, decisions)
        assert len(result["problems"]) == 1
        assert "q003" in result["problems"][0]
        assert "not 'approved'" in result["problems"][0]

    def test_an_approval_against_different_text_is_a_problem(self):
        """The defect the freeze exists for: q009 and q030 were approved, then
        edited, and only a person remembering caught it."""
        queries = small_set()
        decisions = self.decisions_for(queries)
        queries[0]["query"] = "an edit made after the verdict"
        result = freeze.check_approvals(queries, decisions)
        assert len(result["problems"]) == 1
        assert "q001" in result["problems"][0]
        assert "different text" in result["problems"][0]
        assert result["bound"] == ["q002", "q003"]

    def test_an_approval_carrying_no_hash_is_unbound_not_a_problem(self):
        """The 65 decisions made before 2026-08-20 record no text. That is a
        gap in the record, not a failure: it is what the attestation covers,
        and it must not masquerade as either a pass or an error."""
        queries = small_set()
        decisions = self.decisions_for(queries)
        for decision in decisions.values():
            decision.pop(freeze.DECISION_HASH_FIELD)
        result = freeze.check_approvals(queries, decisions)
        assert result["problems"] == []
        assert result["unbound"] == ["q001", "q002", "q003"]
        assert result["bound"] == []

    def test_bound_and_unbound_are_reported_separately(self):
        queries = small_set()
        decisions = self.decisions_for(queries)
        decisions["q002"].pop(freeze.DECISION_HASH_FIELD)
        result = freeze.check_approvals(queries, decisions)
        assert result["unbound"] == ["q002"]
        assert result["bound"] == ["q001", "q003"]


class TestVerify:

    def frozen(self, queries, **kwargs):
        record = freeze.build_freeze(
            queries, frozen_at="2026-08-20", file_sha256="0" * 64,
            approvals={"source": "review-decisions.jsonl"}, **kwargs)
        # Through JSON, because that is how it is stored and read back.
        return json.loads(json.dumps(record, ensure_ascii=False))

    def test_an_unchanged_set_verifies(self):
        queries = small_set()
        assert freeze.verify(queries, self.frozen(queries)) == []

    def test_an_edited_query_is_named(self):
        queries = small_set()
        record = self.frozen(queries)
        queries[1]["gold"][0]["span"] = "a different passage entirely"
        problems = freeze.verify(queries, record)
        assert len(problems) == 1
        assert problems[0].startswith("q002 has changed since the freeze")

    def test_a_removed_query_is_named(self):
        queries = small_set()
        record = self.frozen(queries)
        problems = freeze.verify(queries[:-1], record)
        assert problems == ["q003 was frozen but is no longer in the query set"]

    def test_an_added_query_is_named(self):
        queries = small_set()
        record = self.frozen(queries)
        problems = freeze.verify(queries + [answerable("q004")], record)
        assert problems == ["q004 is in the query set but was never frozen"]

    def test_a_hand_edited_freeze_row_is_caught_by_the_stored_digest(self):
        """Edit a query and its row here to match, and every per-query
        comparison passes. Only the digest the file records about itself
        still disagrees."""
        queries = small_set()
        record = self.frozen(queries)
        queries[0]["query"] = "an edit smuggled past the per-query check"
        for entry in record["queries"]:
            if entry["query_id"] == "q001":
                entry["sha256"] = freeze.query_sha256(queries[0])
        problems = freeze.verify(queries, record)
        assert any("disagrees with itself" in problem for problem in problems)

    def test_an_unknown_manifest_version_refuses_before_comparing(self):
        """A different canonical form makes every hash incomparable, so
        reporting 65 changed queries would be noise pointing nowhere."""
        queries = small_set()
        record = self.frozen(queries)
        record["manifest_version"] = 99
        problems = freeze.verify(queries, record)
        assert len(problems) == 1
        assert "manifest_version" in problems[0]

    def test_an_edited_composition_block_is_caught(self):
        """It is not in the digest, so nothing above would notice."""
        queries = small_set()
        record = self.frozen(queries)
        record["composition"]["distinct_tickers"] = 22
        problems = freeze.verify(queries, record)
        assert len(problems) == 1
        assert "composition differs" in problems[0]

    def test_the_advisory_count_is_not_treated_as_a_composition_edit(self):
        """It is measured against the store at freeze time and has no live
        counterpart to recount, so it must not read as tampering."""
        queries = small_set()
        record = self.frozen(queries, duplicate_span_advisories=11)
        assert record["composition"]["duplicate_span_advisories"] == 11
        assert freeze.verify(queries, record) == []


class TestTheFreezeRecord:

    def test_it_carries_the_digest_the_composition_and_every_query(self):
        queries = small_set()
        record = freeze.build_freeze(
            queries, frozen_at="2026-08-20", file_sha256="a" * 64,
            approvals={"source": "review-decisions.jsonl", "approved": 3})
        assert record["manifest_version"] == freeze.MANIFEST_VERSION
        assert record["frozen_at"] == "2026-08-20"
        assert record["file_sha256"] == "a" * 64
        assert len(record["set_sha256"]) == 64
        assert [entry["query_id"] for entry in record["queries"]] == \
            ["q001", "q002", "q003"]
        assert record["composition"]["queries"] == 3
        assert record["approvals"]["approved"] == 3

    def test_an_attestation_is_recorded_under_approvals(self):
        attestation = {"date": "2026-08-20", "by": "owner", "covers": 65}
        record = freeze.build_freeze(
            small_set(), frozen_at="2026-08-20", file_sha256="a" * 64,
            approvals={"source": "log"}, attestation=attestation)
        assert record["approvals"]["attestation"] == attestation

    def test_no_gold_span_appears_anywhere_in_the_record(self):
        """The freeze is committed and the query set is not. Gold spans are
        verbatim filing text, which is the whole reason the set lives outside
        the repo -- a freeze carrying one would put corpus text in the public
        repo past every guard aimed at the filings."""
        queries = small_set()
        record = freeze.build_freeze(
            queries, frozen_at="2026-08-20", file_sha256="a" * 64,
            approvals={"source": "log"})
        serialised = json.dumps(record, ensure_ascii=False)
        for query in queries:
            assert query["query"] not in serialised
            for location in query["gold"]:
                assert location["span"] not in serialised
