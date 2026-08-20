"""The freeze script: what it refuses, and that its clean verdicts can fire.

`test_query_freeze.py` covers the hashing and the comparison. This file covers
the wiring, which is where a freeze fails open rather than loud: a check whose
result is ignored, a write that happens anyway, a verify that passes because it
compared nothing.

The fixture store carries a known-positive goodwill-impairment chunk in a smoke
issuer on purpose. `controls` has to be able to make the smoke check fire, and a
fixture that could not would let this suite certify a check that never runs --
which is the exact defect the controls exist to catch.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import freeze_queries as script  # noqa: E402
from evaluation import query_freeze as freeze  # noqa: E402

# Twelve content words, so the span clears the length guidance and draws no
# advisory noise. None of them appear in any query text below.
SPAN_WORDS = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet "
              "kilo lima")

# A conceptual query shares no content word with its gold span. These share
# none with SPAN_WORDS, which is what makes the fixture legal rather than
# merely convenient.
CONCEPTUAL = "which quantity did management disclose"
EXACT = "how many units were sold"


def span_for(index):
    return f"{SPAN_WORDS} sierra{index:03d}"


def accession_for(index):
    return f"0000000000-25-{index:06d}"


def full_set():
    """25 exact-entity + 25 conceptual + 15 unanswerable, the pre-registered
    shape. Anything less fails `check_set` before the wiring under test runs."""
    queries = []
    for index in range(50):
        conceptual = index >= 25
        text = CONCEPTUAL if conceptual else EXACT
        queries.append({
            "query_id": f"q{index + 1:03d}",
            "stratum": "conceptual" if conceptual else "exact_entity",
            # The trailing token keeps every query text distinct without
            # putting a span word into the query.
            "query": f"{text} tango{index:03d}",
            "gold": [{"accession": accession_for(index), "ticker": "AAA",
                      "item": "1", "span": span_for(index)}],
        })
    for index in range(15):
        queries.append({
            "query_id": f"q{index + 51:03d}",
            "stratum": "unanswerable",
            "query": f"what did the proxy statement say victor{index:03d}",
            "gold": [],
            "why_unanswerable": "stated only in the proxy statement",
        })
    return queries


def chunk(chunk_id, accession, text, ticker="AAA"):
    return {"chunk_id": chunk_id, "accession": accession, "ticker": ticker,
            "period": "2025", "item": "1", "title": "Item 1. Business",
            "index": 0, "first_page": 1, "last_page": 1,
            "tokens": len(text.split()), "text": text}


def full_store():
    records = [chunk(f"c{index:03d}", accession_for(index),
                     f"preamble text {span_for(index)} trailing text")
               for index in range(50)]
    # The control's known positive. Never used as gold, and in a smoke issuer.
    records.append(chunk("c-smoke", "0000000000-25-999999",
                         "the company recorded a goodwill impairment charge "
                         "during the fiscal year on its reporting units",
                         ticker="MA"))
    return records


def decisions_for(queries, bind=True):
    made = {}
    for query in queries:
        record = {"query_id": query["query_id"], "verdict": "approved",
                  "note": ""}
        if bind:
            record[freeze.DECISION_HASH_FIELD] = freeze.query_sha256(query)
        made[query["query_id"]] = record
    return made


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A whole data root: filings, queries, decisions and a chunk store."""
    root = tmp_path / "data"
    monkeypatch.setenv("RAG_FILINGS_DIR", str(root / "filings"))
    queries_dir = root / "queries"
    queries_dir.mkdir(parents=True)
    (root / "chunks").mkdir(parents=True)

    queries = full_set()
    write_queries(queries_dir, queries)
    write_decisions(queries_dir, decisions_for(queries))
    (root / "chunks" / "chunks.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in full_store()),
        encoding="utf-8")
    return {"root": root, "queries_dir": queries_dir, "queries": queries,
            "freeze": tmp_path / "query-set-freeze.json"}


def write_queries(queries_dir, queries):
    (queries_dir / "queries.jsonl").write_text(
        "".join(json.dumps(query, ensure_ascii=False) + "\n"
                for query in queries), encoding="utf-8")


def write_decisions(queries_dir, decisions):
    (queries_dir / "review-decisions.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n"
                for record in decisions.values()), encoding="utf-8")


def rebind(data, queries):
    """Write edited queries and re-approve them against the new text.

    Every refusal test edits a query, and an edit on its own makes that
    query's approval stale -- so without this the test would refuse for the
    wrong reason and would keep passing with the rule it names switched off.
    """
    write_queries(data["queries_dir"], queries)
    write_decisions(data["queries_dir"], decisions_for(queries))


def run(data, *args):
    return script.main([*args, "--freeze-path", str(data["freeze"])])


class TestTheControls:

    def test_they_pass_on_a_store_that_can_fire_both_checks(self):
        assert script.controls(full_store()) == []

    def test_an_empty_store_is_a_reason_not_a_traceback(self):
        """Nothing in it can fail, so every check below would report clean.
        The guard also keeps `records[0]` from raising IndexError, which would
        say nothing about the cause."""
        problems = script.controls([])
        assert len(problems) == 1
        assert "store is empty" in problems[0]

    def test_a_store_with_no_known_positive_is_reported_not_ignored(self):
        """Without a goodwill-impairment passage in MA, DOW or WYNN, the smoke
        check has nothing that must fail, so its clean verdict over the 50 gold
        spans would establish nothing. That has to be loud."""
        records = [record for record in full_store()
                   if record["chunk_id"] != "c-smoke"]
        problems = script.controls(records)
        assert len(problems) == 1
        assert "cannot be shown to fire" in problems[0]

    def test_the_known_positive_is_actually_refused(self, monkeypatch):
        """The control must fail when the check it exercises is broken, or it
        is decoration. Blind the smoke check and the control has to notice."""
        monkeypatch.setattr(script.query_set, "check_smoke_constraint",
                            lambda gold, records: [])
        problems = script.controls(full_store())
        assert len(problems) == 1
        assert "accepted a goodwill-impairment span" in problems[0]

    def test_a_span_in_no_chunk_is_refused(self, monkeypatch):
        monkeypatch.setattr(script.gold, "validate_gold",
                            lambda records, locations: [])
        problems = script.controls(full_store())
        assert any("span that is in no chunk" in problem
                   for problem in problems)


class TestStoreProblems:

    def test_a_clean_set_produces_no_refusals(self):
        found_gold, found_smoke, advisories = script.store_problems(
            full_set(), full_store())
        assert found_gold == []
        assert found_smoke == []
        assert advisories == 0

    def test_a_span_in_no_chunk_is_named_by_query(self):
        queries = full_set()
        queries[3]["gold"][0]["span"] = "a passage that was never in any filing"
        found_gold, _, _ = script.store_problems(queries, full_store())
        assert len(found_gold) == 1
        assert found_gold[0].startswith("q004: ")

    def test_gold_and_smoke_refusals_are_reported_separately(self):
        """One combined line reading "ok" would hide which rule ran."""
        queries = full_set()
        queries[0]["gold"][0] = {"accession": "0000000000-25-999999",
                                 "ticker": "MA", "item": "1",
                                 "span": "goodwill impairment charge"}
        found_gold, found_smoke, _ = script.store_problems(
            queries, full_store())
        assert found_gold == []
        assert len(found_smoke) == 1
        assert found_smoke[0].startswith("q001: ")

    def test_a_duplicated_span_is_counted_as_an_advisory(self):
        """AMENDMENT 5 requires the count be reported, so it has to be
        counted rather than merely available."""
        records = full_store()
        records.append(chunk("c-dup", "0000000000-25-888888",
                             f"another filing repeating {span_for(0)} verbatim"))
        _, _, advisories = script.store_problems(full_set(), records)
        assert advisories == 1


class TestWhatItRefusesToWrite:

    def test_it_refuses_without_an_attestation_when_approvals_are_unbound(
            self, data, capsys):
        write_decisions(data["queries_dir"],
                        decisions_for(data["queries"], bind=False))
        assert run(data, "--write") == 1
        assert "REFUSING to write" in capsys.readouterr().out
        assert not data["freeze"].exists()

    def test_it_refuses_when_a_query_is_not_approved(self, data, capsys):
        decisions = decisions_for(data["queries"])
        decisions["q007"]["verdict"] = "rejected"
        write_decisions(data["queries_dir"], decisions)
        assert run(data, "--write", "--attest", "I affirm") == 1
        out = capsys.readouterr().out
        assert "q007" in out
        assert not data["freeze"].exists()

    def test_it_refuses_when_an_approval_is_stale(self, data, capsys):
        """The defect the freeze exists for: approved, then edited."""
        queries = data["queries"]
        queries[9]["query"] = "an edit made after the verdict was recorded"
        write_queries(data["queries_dir"], queries)
        assert run(data, "--write", "--attest", "I affirm") == 1
        out = capsys.readouterr().out
        assert "q010" in out
        assert "different text" in out
        assert not data["freeze"].exists()

    def test_it_refuses_when_a_gold_span_is_missing_from_the_store(
            self, data, capsys):
        queries = data["queries"]
        queries[2]["gold"][0]["span"] = "a passage in no filing anywhere"
        rebind(data, queries)
        assert run(data, "--write", "--attest", "I affirm") == 1
        assert "q003" in capsys.readouterr().out
        assert not data["freeze"].exists()

    def test_it_refuses_gold_from_the_disclosed_smoke_passage(self, data,
                                                              capsys):
        """The constraint disclosed 2026-08-19, and the one refusal whose own
        check runs clean over the real set -- so a query that violates it is
        the only way to show the refusal reaches the exit code.

        `rebind` is load-bearing. Editing a query without re-binding its
        approval makes it stale, and this test then passed on the stale-
        approval refusal while `report` printed the smoke message either way:
        both assertions on the output held with the smoke result discarded."""
        queries = data["queries"]
        queries[4]["gold"][0] = {"accession": "0000000000-25-999999",
                                 "ticker": "MA", "item": "1",
                                 "span": "goodwill impairment charge"}
        rebind(data, queries)
        assert run(data, "--write", "--attest", "I affirm") == 1
        out = capsys.readouterr().out
        assert "q005" in out
        assert "off limits" in out
        assert not data["freeze"].exists()

    def test_it_refuses_when_the_controls_cannot_fire(self, data, capsys):
        """A store with no goodwill-impairment passage in a smoke issuer gives
        the smoke check nothing that must fail, so its clean verdict over the
        50 gold spans would be worth nothing. Refusing is the only honest
        outcome, and it has to reach the exit code."""
        records = [record for record in full_store()
                   if record["chunk_id"] != "c-smoke"]
        (data["root"] / "chunks" / "chunks.jsonl").write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8")
        assert run(data, "--write", "--attest", "I affirm") == 1
        assert "cannot be shown to fire" in capsys.readouterr().out
        assert not data["freeze"].exists()

    def test_it_refuses_no_store(self, data, capsys):
        """A freeze that skipped its own validation records nothing."""
        assert run(data, "--write", "--attest", "I affirm", "--no-store") == 1
        assert "REFUSING to write" in capsys.readouterr().out
        assert not data["freeze"].exists()

    def test_it_refuses_to_overwrite_an_existing_freeze(self, data, capsys):
        assert run(data, "--write", "--attest", "I affirm") == 0
        assert run(data, "--write", "--attest", "I affirm again") == 1
        out = capsys.readouterr().out
        assert "REFUSING to overwrite" in out
        assert "--refreeze" in out

    def test_refreeze_is_the_disclosed_way_through(self, data):
        assert run(data, "--write", "--attest", "I affirm") == 0
        assert run(data, "--write", "--attest", "again", "--refreeze") == 0


class TestTheFrozenFile:

    def test_writing_then_verifying_round_trips(self, data, capsys):
        assert run(data, "--write", "--attest", "I affirm the approvals") == 0
        capsys.readouterr()
        assert run(data, "--verify") == 0
        assert "unchanged since the freeze: ok" in capsys.readouterr().out

    def test_editing_any_query_afterwards_fails_verify_by_name(self, data,
                                                               capsys):
        """The whole promise: an edit after the freeze cannot pass quietly."""
        assert run(data, "--write", "--attest", "I affirm") == 0
        queries = data["queries"]
        queries[41]["gold"][0]["span"] = span_for(41) + " and one more clause"
        write_queries(data["queries_dir"], queries)
        capsys.readouterr()
        assert run(data, "--verify") == 1
        out = capsys.readouterr().out
        assert "q042 has changed since the freeze" in out

    def test_verify_refuses_when_nothing_has_been_frozen(self, data, capsys):
        """Missing and unchanged must not share an exit code, or an arm run
        before the freeze would report a verification it never did."""
        assert run(data, "--verify") == 2
        assert "has not been frozen" in capsys.readouterr().out

    def test_the_attestation_is_recorded_as_an_attestation(self, data):
        write_decisions(data["queries_dir"],
                        decisions_for(data["queries"], bind=False))
        assert run(data, "--write", "--attest", "  I affirm these 65  ") == 0
        record = json.loads(data["freeze"].read_text(encoding="utf-8"))
        attestation = record["approvals"]["attestation"]
        assert attestation["text"] == "I affirm these 65"
        assert attestation["kind"] == script.ATTESTATION_KIND
        assert "not a mechanical verification" in attestation["kind"]
        assert attestation["covers"] == 65
        assert record["approvals"]["bound_by_hash"] == 0

    def test_no_attestation_is_recorded_when_every_approval_binds(self, data):
        """An attestation covering nothing would overstate what a human did."""
        # The fixture's decisions are already hash-bound; no --attest given.
        assert run(data, "--write") == 0
        record = json.loads(data["freeze"].read_text(encoding="utf-8"))
        assert "attestation" not in record["approvals"]
        assert record["approvals"]["bound_by_hash"] == 65
        assert record["approvals"]["unbound_by_hash"] == []

    def test_it_carries_the_advisory_count_and_the_composition(self, data):
        assert run(data, "--write", "--attest", "I affirm") == 0
        record = json.loads(data["freeze"].read_text(encoding="utf-8"))
        composition = record["composition"]
        assert composition["duplicate_span_advisories"] == 0
        assert composition["strata"] == {"conceptual": 25, "exact_entity": 25,
                                         "unanswerable": 15}
        assert composition["answerable"] == 50

    def test_it_contains_no_query_text_and_no_gold_span(self, data):
        """It is committed and the query set is not."""
        assert run(data, "--write", "--attest", "I affirm") == 0
        written = data["freeze"].read_text(encoding="utf-8")
        for query in data["queries"]:
            assert query["query"] not in written
            for location in query["gold"]:
                assert location["span"] not in written


class TestTheGuardTheArmsCall:

    def test_it_returns_the_freeze_when_the_set_is_unchanged(self, data):
        assert run(data, "--write", "--attest", "I affirm") == 0
        record = freeze.refuse_unless_frozen(data["queries"], data["freeze"])
        assert record["composition"]["queries"] == 65

    def test_it_raises_naming_the_query_that_moved(self, data):
        assert run(data, "--write", "--attest", "I affirm") == 0
        queries = data["queries"]
        queries[0]["query"] = "edited after the freeze"
        with pytest.raises(RuntimeError, match="q001 has changed"):
            freeze.refuse_unless_frozen(queries, data["freeze"])

    def test_it_raises_rather_than_passing_when_there_is_no_freeze(self, data):
        with pytest.raises(FileNotFoundError, match="has not been frozen"):
            freeze.refuse_unless_frozen(data["queries"], data["freeze"])
