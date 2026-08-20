"""The review app's decision I/O -- the part where a defect loses work.

The HTTP layer is deliberately not tested here. What matters is that a
decision, once made, survives and stays attached to the text it was cast
against: re-reviewing 65 gold spans by hand is the expensive act this tool
exists to avoid repeating, and a verdict that outlives its query is worse than
no verdict, because it still reads as decided.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from evaluation import query_freeze  # noqa: E402
import review_queries as review  # noqa: E402


def query(qid="q001", text="how many primary suppliers", span="a quoted span"):
    return {"query_id": qid, "stratum": "exact_entity", "query": text,
            "gold": [{"accession": "0000277135-25-000010", "ticker": "GWW",
                      "item": "1", "span": span}]}


@pytest.fixture
def decisions(tmp_path):
    return tmp_path / "review-decisions.jsonl"


def test_a_decision_round_trips(decisions):
    review.write_decision(query(), "approved", "reads fine", path=decisions)
    stored = review.read_decisions(decisions)
    assert stored["q001"]["verdict"] == "approved"
    assert stored["q001"]["note"] == "reads fine"


def test_a_decision_records_the_hash_of_the_text_it_was_cast_against(decisions):
    """The whole reason the freeze needed an attestation for the first 65:
    without this field, nothing in the log says which text was approved."""
    reviewed = query()
    review.write_decision(reviewed, "approved", path=decisions)
    stored = review.read_decisions(decisions)["q001"]
    assert stored[query_freeze.DECISION_HASH_FIELD] ==         query_freeze.query_sha256(reviewed)


def test_editing_the_query_afterwards_breaks_the_binding(decisions):
    """q009 and q030, mechanically. The stored verdict still reads
    `approved`; the hash is what makes the staleness visible."""
    review.write_decision(query(), "approved", path=decisions)
    stored = review.read_decisions(decisions)["q001"]
    edited = query(text="how many primary suppliers, in FY2025")
    assert stored[query_freeze.DECISION_HASH_FIELD] !=         query_freeze.query_sha256(edited)


def test_a_verdict_cannot_be_recorded_from_a_query_id_alone(decisions):
    """The call shape is the guard. An id-only signature is what let the first
    65 decisions be written with nothing binding them to text."""
    with pytest.raises(TypeError, match="whole record"):
        review.write_decision("q001", "approved", path=decisions)
    assert not decisions.exists()


def test_missing_file_reads_as_no_decisions(decisions):
    """A first run must start empty rather than raising."""
    assert review.read_decisions(decisions) == {}


def test_re_deciding_wins_without_losing_the_earlier_line(decisions):
    """Append-only, collapsed on read: the latest verdict is what counts.

    Pinned in both directions -- the reader must return the newer verdict, and
    the file must still hold the older line, because an in-place rewrite is
    what would put already-made decisions at risk.
    """
    review.write_decision(query(), "approved", path=decisions)
    review.write_decision(query(), "rejected", "gold answers a different question",
                          path=decisions)
    assert review.read_decisions(decisions)["q001"]["verdict"] == "rejected"
    lines = [json.loads(l) for l in
             decisions.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [l["verdict"] for l in lines] == ["approved", "rejected"]


def test_an_unknown_verdict_is_refused(decisions):
    """A typo'd verdict would leave a query looking decided but counted as
    neither approved nor rejected."""
    with pytest.raises(ValueError, match="verdict must be one of"):
        review.write_decision(query(), "aproved", path=decisions)
    assert not decisions.exists()


def test_notes_are_stripped_and_optional(decisions):
    review.write_decision(query(), "approved", "  spaced  ", path=decisions)
    review.write_decision(query("q002"), "approved", path=decisions)
    stored = review.read_decisions(decisions)
    assert stored["q001"]["note"] == "spaced"
    assert stored["q002"]["note"] == ""


def test_summarize_counts_only_what_was_decided():
    queries = [{"query_id": f"q{n:03d}"} for n in range(1, 6)]
    decided = {"q001": {"verdict": "approved"},
               "q002": {"verdict": "approved"},
               "q003": {"verdict": "rejected"}}
    assert review.summarize(queries, decided) == {
        "approved": 2, "rejected": 1, "total": 5}


def test_decisions_land_beside_the_query_set_outside_the_repo(monkeypatch, tmp_path):
    """Verdicts are about spans of filing text and follow the same rule."""
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
    assert review.decisions_path() == tmp_path / "data" / "queries" / "review-decisions.jsonl"
    assert review.queries_path() == tmp_path / "data" / "queries" / "queries.jsonl"


def test_gold_span_is_escaped_into_the_page():
    """Spans are filing text and carry `&`, `<` and quotes; unescaped they
    would corrupt the markup and could silently truncate what is reviewed."""
    queries = [{"query_id": "q001", "stratum": "exact_entity",
                "query": "AT&T <b>bold</b>?",
                "gold": [{"accession": "0000-00", "ticker": "T", "item": "1",
                          "span": 'Procter & Gamble said "no" <script>x</script>'}]}]
    page = review.render_cards(queries, {"q001": {"gold_chunks": 1, "notes": []}}, {})
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page
    assert "Procter &amp; Gamble" in page


def test_preflight_names_the_variable_when_the_query_set_is_missing(monkeypatch, tmp_path):
    """The failure this replaces was reported as "localhost isn't loading".

    Unset RAG_FILINGS_DIR and queries_dir() falls back to its repo-relative
    default, where no query set exists. uvicorn still binds, so the server
    looks fine and every request 500s -- a symptom that points nowhere near
    the cause. Preflight has to run before the socket does, and has to name
    the variable.
    """
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "nope" / "filings"))
    problem = review.preflight()
    assert problem is not None
    assert "RAG_FILINGS_DIR" in problem
    assert str(review.queries_path()) in problem


def test_preflight_passes_when_the_query_set_is_there(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
    target = tmp_path / "data" / "queries"
    target.mkdir(parents=True)
    (target / "queries.jsonl").write_text("", encoding="utf-8")
    assert review.preflight() is None
