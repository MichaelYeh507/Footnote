"""The runner: its refusals, and the record it leaves behind.

The arms themselves are tested in `tests/test_retrieval.py`. What is tested
here is the wiring, which is where this script can go wrong without erroring:

  * running against a set that has moved since the freeze, which produces
    numbers over some other set;
  * running against a database whose chunks are not the store's chunks, which
    scores as a retrieval miss because gold is derived from the store while
    rankings come from the database;
  * writing a provenance record that restates the constants instead of
    recording what the run actually did -- an `ef_search` copied from a
    module is not evidence the server applied it;
  * writing anything at all inside the repo.

No live services: the cursor and the OpenAI client are fakes, so the refusals
are exercised for free and a `--limit` run is not needed to test the plumbing.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import run_retrieval  # noqa: E402
from services import fusion, retrieval  # noqa: E402


def _query(query_id="q001", stratum="exact_entity", text="a question"):
    return {"query_id": query_id, "stratum": stratum, "query": text,
            "gold": [{"accession": "acc-1", "span": "a span"}]}


def _chunk(chunk_id, accession="acc-1"):
    return {"chunk_id": chunk_id, "accession": accession, "ticker": "AAA",
            "period": "2025-12-31", "item": "1", "title": "Business",
            "index": 0, "first_page": 1, "last_page": 1, "tokens": 10,
            "text": "a passage"}


class ScriptedCursor:
    """Answers by statement shape rather than by call order.

    The runner issues six different statements per query in a fixed sequence,
    and a queue-based fake would encode that sequence into every test -- so
    reordering two statements would break twenty tests without any of them
    being about ordering.
    """

    def __init__(self, *, chunk_ids=("c1",), unembedded=0, ef="100",
                 sparse=(("c1", 2.9),), dense=(("c1", 0.1),),
                 tsquery="'a' | 'question'", totals=(1, 1, 10)):
        self.chunk_ids = list(chunk_ids)
        self.unembedded = unembedded
        self.ef = ef
        self.sparse = list(sparse)
        self.dense = list(dense)
        self.tsquery = tsquery
        self.totals = totals
        self.statements = []
        self._rows = []

    def execute(self, sql, params=None):
        flat = " ".join(str(sql).split())
        self.statements.append(flat)
        if flat.startswith("set hnsw.ef_search"):
            self._rows = []
        elif flat == "show hnsw.ef_search":
            self._rows = [(self.ef,)]
        elif "iterative_scan" in flat:
            self._rows = [("off",)]
        elif flat == "select chunk_id from chunks":
            self._rows = [(cid,) for cid in self.chunk_ids]
        elif "embedding is null" in flat:
            self._rows = [(self.unembedded,)]
        elif "count(distinct accession)" in flat:
            self._rows = [self.totals]
        elif "plainto_tsquery" in flat:
            self._rows = [(self.tsquery,)]
        elif "ts_rank_cd" in flat:
            self._rows = list(self.sparse)
        elif "<=>" in flat:
            self._rows = list(self.dense)
        else:
            raise AssertionError(f"unscripted statement: {flat}")

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class FakeClient:
    def __init__(self, vector=None):
        self.vector = vector or [0.125] * retrieval.EMBED_DIMENSIONS
        self.calls = []
        client = self

        class Embeddings:
            def create(self, **kwargs):
                client.calls.append(kwargs)
                item = type("Item", (), {"embedding": client.vector})()
                return type("Response", (), {"data": [item]})()

        self.embeddings = Embeddings()


class TestTheStoreDatabaseCheck:

    def test_matching_ids_pass(self):
        cursor = ScriptedCursor(chunk_ids=("c1", "c2"))
        records = [_chunk("c1"), _chunk("c2")]
        assert run_retrieval.check_store_matches_database(cursor, records) == []

    def test_a_chunk_missing_from_the_database_is_refused(self):
        cursor = ScriptedCursor(chunk_ids=("c1",))
        records = [_chunk("c1"), _chunk("c2")]
        problems = run_retrieval.check_store_matches_database(cursor, records)
        assert any("store but not in the database" in p for p in problems)

    def test_a_chunk_missing_from_the_store_is_refused(self):
        cursor = ScriptedCursor(chunk_ids=("c1", "c2"))
        problems = run_retrieval.check_store_matches_database(
            cursor, [_chunk("c1")])
        assert any("database but not in the store" in p for p in problems)

    def test_same_count_different_ids_is_refused(self):
        """The reason the whole id set is compared rather than the count. Two
        stores of the same size are the case a count check calls identical."""
        cursor = ScriptedCursor(chunk_ids=("c1", "c9"))
        problems = run_retrieval.check_store_matches_database(
            cursor, [_chunk("c1"), _chunk("c2")])
        assert len(problems) == 2

    def test_an_unembedded_chunk_is_refused(self):
        cursor = ScriptedCursor(chunk_ids=("c1",), unembedded=3)
        problems = run_retrieval.check_store_matches_database(
            cursor, [_chunk("c1")])
        assert any("no embedding" in p for p in problems)


class TestRunningOneQuery:

    def test_all_three_arms_come_back(self):
        cursor = ScriptedCursor(sparse=(("c1", 2.9), ("c2", 1.0)),
                                dense=(("c2", 0.1), ("c3", 0.4)))
        result = run_retrieval.run_query(cursor, FakeClient(), _query())
        arms = result["record"]["arms"]
        assert [c for c, _ in arms["sparse"]] == ["c1", "c2"]
        assert [c for c, _ in arms["dense"]] == ["c2", "c3"]
        assert [c for c, _ in arms["hybrid"]] == ["c2", "c1", "c3"]

    def test_the_hybrid_arm_is_fused_from_the_other_two_not_re_queried(self):
        """Three arms, two searches. A hybrid arm that issued its own query
        would be measuring something the ablation never ran."""
        cursor = ScriptedCursor()
        run_retrieval.run_query(cursor, FakeClient(), _query())
        assert sum("ts_rank_cd" in s for s in cursor.statements) == 1
        assert sum("<=>" in s for s in cursor.statements) == 1

    def test_the_tsquery_is_recorded(self):
        cursor = ScriptedCursor(tsquery="'goodwil' | 'impair'")
        result = run_retrieval.run_query(cursor, FakeClient(), _query())
        assert result["record"]["tsquery"] == "'goodwil' | 'impair'"

    def test_the_query_text_is_what_gets_embedded(self):
        client = FakeClient()
        run_retrieval.run_query(ScriptedCursor(), client,
                                _query(text="How many suppliers?"))
        assert client.calls[0]["input"] == ["How many suppliers?"]

    def test_scores_are_kept_alongside_the_ids(self):
        cursor = ScriptedCursor(sparse=(("c1", 2.9),), dense=(("c1", 0.25),))
        arms = run_retrieval.run_query(
            cursor, FakeClient(), _query())["record"]["arms"]
        assert arms["sparse"][0] == ["c1", 2.9]
        assert arms["dense"][0] == ["c1", 0.25]

    def test_an_unanswerable_query_runs_like_any_other(self):
        """The 15 carry no gold and enter no recall denominator, but Phases 4
        and 5 measure abstention against what was actually retrieved."""
        query = {"query_id": "q010", "stratum": "unanswerable",
                 "query": "who is on the compensation committee?", "gold": []}
        record = run_retrieval.run_query(
            ScriptedCursor(), FakeClient(), query)["record"]
        assert record["stratum"] == "unanswerable"
        assert record["arms"]["hybrid"]

    def test_a_query_matching_no_lexeme_still_produces_a_dense_ranking(self):
        cursor = ScriptedCursor(tsquery=None)
        record = run_retrieval.run_query(
            cursor, FakeClient(), _query())["record"]
        assert record["arms"]["sparse"] == []
        assert record["arms"]["dense"]
        assert record["arms"]["hybrid"]


class TestTheEmbeddingsDigest:

    def test_it_is_stable_under_insertion_order(self):
        a = {"q001": [0.1] * 4, "q002": [0.2] * 4}
        b = {"q002": [0.2] * 4, "q001": [0.1] * 4}
        assert run_retrieval.embeddings_digest(a) == \
            run_retrieval.embeddings_digest(b)

    def test_it_moves_when_a_vector_moves(self):
        a = {"q001": [0.1, 0.2]}
        b = {"q001": [0.1, 0.2000001]}
        assert run_retrieval.embeddings_digest(a) != \
            run_retrieval.embeddings_digest(b)

    def test_it_moves_when_a_vector_is_attached_to_a_different_query(self):
        """Digesting the vectors without their ids would call these two runs
        identical, and they retrieved different things for q001."""
        a = {"q001": [0.1, 0.2], "q002": [0.3, 0.4]}
        b = {"q001": [0.3, 0.4], "q002": [0.1, 0.2]}
        assert run_retrieval.embeddings_digest(a) != \
            run_retrieval.embeddings_digest(b)

    def test_it_digests_the_literal_the_database_was_queried_with(self):
        vectors = {"q001": [0.5, -0.25]}
        import hashlib
        expected = hashlib.sha256(
            f"q001  {retrieval.to_pgvector([0.5, -0.25])}\n".encode("utf-8")
        ).hexdigest()
        assert run_retrieval.embeddings_digest(vectors) == expected


class TestTheProvenanceRecord:

    def _build(self, tmp_path, **overrides):
        rankings = tmp_path / "rankings-x.jsonl"
        rankings.write_text('{"query_id": "q001"}\n', encoding="utf-8")
        kwargs = dict(
            run_stamp="20260820-120000",
            started="2026-08-20T12:00:00", finished="2026-08-20T12:05:00",
            freeze={"set_sha256": "a35b2634", "frozen_at": "2026-08-20",
                    "composition": {"queries": 65, "answerable": 50,
                                    "duplicate_span_advisories": 11}},
            queries=[_query()], ef_applied=100, iterative_scan="off",
            vectors={"q001": [0.1, 0.2]},
            store_counts={"chunks": 11621, "accessions": 44,
                          "tokens": 4890354},
            database_url="postgresql://postgres:secret@host:5432/postgres",
            rankings_path=rankings, complete=True)
        kwargs.update(overrides)
        return run_retrieval.build_provenance(**kwargs)

    def test_it_carries_every_pre_registered_parameter(self, tmp_path):
        record = self._build(tmp_path)
        assert record["dense"]["model"] == "text-embedding-3-small"
        assert record["dense"]["dimensions"] == 1536
        assert record["dense"]["ef_search_requested"] == retrieval.EF_SEARCH
        assert record["dense"]["depth"] == retrieval.DEPTH
        assert record["sparse"]["rank_normalization"] == 0
        assert record["sparse"]["configuration"] == "english"
        assert record["hybrid"]["k"] == fusion.RRF_K
        assert record["hybrid"]["depth"] == fusion.FUSION_DEPTH

    def test_it_records_the_frozen_set_digest(self, tmp_path):
        record = self._build(tmp_path)
        assert record["query_set"]["set_sha256"] == "a35b2634"
        assert record["query_set"]["duplicate_span_advisories"] == 11

    def test_it_records_the_ef_search_the_server_reported(self, tmp_path):
        """Not the constant. The whole reason to read it back is that a SET can
        be accepted and never applied, and a record copying the module constant
        would report 100 in exactly that case."""
        record = self._build(tmp_path, ef_applied=40)
        assert record["dense"]["ef_search_applied"] == 40
        assert record["dense"]["ef_search_requested"] == 100

    def test_it_records_no_query_prefix_and_no_dimensions_request(self,
                                                                 tmp_path):
        record = self._build(tmp_path)
        assert record["dense"]["query_prefix"] is None
        assert record["dense"]["request_dimensions"] is None

    def test_it_never_writes_the_database_password(self, tmp_path):
        record = self._build(tmp_path)
        assert "secret" not in json.dumps(record)
        assert "***" in record["database"]

    def test_it_hashes_the_rankings_file_as_written(self, tmp_path):
        import hashlib
        rankings = tmp_path / "rankings-x.jsonl"
        record = self._build(tmp_path)
        assert record["rankings"]["sha256"] == \
            hashlib.sha256(rankings.read_bytes()).hexdigest()

    def test_it_hashes_the_code_that_produced_the_rankings(self, tmp_path):
        """A parameter list describes intent. These two digests describe the
        functions that ran."""
        record = self._build(tmp_path)
        assert len(record["code"]["services/retrieval.py"]) == 64
        assert len(record["code"]["services/fusion.py"]) == 64

    def test_a_limited_run_is_marked_incomplete(self, tmp_path):
        record = self._build(tmp_path, complete=False)
        assert record["complete"] is False

    def test_it_holds_no_query_text_and_no_gold_span(self, tmp_path):
        """The provenance file is the one artifact here a reader might be shown
        directly. Gold spans are verbatim filing text."""
        rendered = json.dumps(self._build(tmp_path))
        assert "a question" not in rendered
        assert "a span" not in rendered


class TestTheOutputLocation:

    def test_it_refuses_a_directory_inside_the_repo(self):
        with pytest.raises(RuntimeError, match="inside the repo"):
            run_retrieval._refuse_repo_output(BACKEND / "corpus" / "retrieval")

    def test_it_refuses_the_repo_root_itself(self):
        with pytest.raises(RuntimeError, match="inside the repo"):
            run_retrieval._refuse_repo_output(BACKEND.parent)

    def test_it_accepts_a_directory_beside_the_repo(self, tmp_path):
        run_retrieval._refuse_repo_output(tmp_path / "retrieval")

    def test_the_default_is_the_data_root(self, monkeypatch, tmp_path):
        import corpus_paths
        monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "d" / "filings"))
        assert corpus_paths.retrieval_dir() == tmp_path / "d" / "retrieval"


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        connection = self

        class Scope:
            def __enter__(self):
                return connection._cursor

            def __exit__(self, *exc):
                return False

        return Scope()


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """`main` with the database, the query set and OpenAI replaced by fakes.

    Written after a perturbation showed the `--limit` -> `complete` wiring was
    untested: every provenance test called `build_provenance` directly, so
    hard-coding `complete=True` in `main` broke nothing.
    """
    queries = [_query("q001"), _query("q002"), _query("q003")]
    cursor = ScriptedCursor(chunk_ids=("c1", "c2"),
                            sparse=(("c1", 2.9),), dense=(("c2", 0.1),),
                            totals=(2, 1, 20))
    monkeypatch.setattr(run_retrieval.review, "read_queries", lambda: queries)
    monkeypatch.setattr(run_retrieval.review, "queries_path",
                        lambda: tmp_path / "queries.jsonl")
    monkeypatch.setattr(
        run_retrieval.query_freeze, "refuse_unless_frozen",
        lambda q, path=None: {"set_sha256": "a35b2634", "frozen_at":
                              "2026-08-20",
                              "composition": {"queries": 3, "answerable": 3,
                                              "duplicate_span_advisories": 0}})
    monkeypatch.setattr(run_retrieval.chunk_store, "read",
                        lambda: [_chunk("c1"), _chunk("c2")])
    monkeypatch.setattr(run_retrieval.database, "url",
                        lambda: "postgresql://user:secret@host:5432/postgres")
    monkeypatch.setattr(run_retrieval.retrieval, "embedding_client",
                        lambda *a, **k: FakeClient())
    monkeypatch.setattr("psycopg.connect",
                        lambda *a, **k: FakeConnection(cursor))
    return tmp_path / "out", cursor


def _written(out):
    rankings = sorted(out.glob("rankings-*.jsonl"))
    provenance = sorted(out.glob("provenance-*.json"))
    assert len(rankings) == 1 and len(provenance) == 1, (rankings, provenance)
    records = [json.loads(line) for line in
               rankings[0].read_text(encoding="utf-8").splitlines() if line]
    return records, json.loads(provenance[0].read_text(encoding="utf-8"))


class TestMainWritesTheRun:

    def test_a_full_run_writes_every_query_and_is_marked_complete(self, wired):
        out, _ = wired
        assert run_retrieval.main(["--out", str(out)]) == 0
        records, provenance = _written(out)
        assert [r["query_id"] for r in records] == ["q001", "q002", "q003"]
        assert provenance["complete"] is True
        assert provenance["queries_run"] == 3

    def test_a_limited_run_is_marked_incomplete(self, wired):
        """The flag the scorer refuses on. Nothing else distinguishes a
        two-query file from a complete one at a glance."""
        out, _ = wired
        assert run_retrieval.main(["--out", str(out), "--limit", "2"]) == 0
        records, provenance = _written(out)
        assert [r["query_id"] for r in records] == ["q001", "q002"]
        assert provenance["complete"] is False
        assert provenance["queries_run"] == 2

    def test_the_rankings_file_holds_all_three_arms_per_query(self, wired):
        out, _ = wired
        run_retrieval.main(["--out", str(out)])
        records, _ = _written(out)
        for record in records:
            assert set(record["arms"]) == {"sparse", "dense", "hybrid"}

    def test_the_provenance_names_the_rankings_file_it_describes(self, wired):
        out, _ = wired
        run_retrieval.main(["--out", str(out)])
        _, provenance = _written(out)
        rankings = out / provenance["rankings"]["path"]
        import hashlib
        assert rankings.exists()
        assert provenance["rankings"]["sha256"] == \
            hashlib.sha256(rankings.read_bytes()).hexdigest()

    def test_a_dry_run_writes_nothing(self, wired):
        out, _ = wired
        assert run_retrieval.main(["--out", str(out), "--dry-run"]) == 0
        assert not out.exists()

    def test_it_refuses_before_writing_when_the_store_diverges(self,
                                                              monkeypatch,
                                                              wired):
        out, _ = wired
        monkeypatch.setattr(run_retrieval.chunk_store, "read",
                            lambda: [_chunk("c1"), _chunk("c9")])
        assert run_retrieval.main(["--out", str(out)]) == 2
        assert not out.exists()


class TestTheFreezeGateInMain:

    def test_it_refuses_and_runs_nothing_when_the_set_has_moved(
            self, monkeypatch, capsys):
        """The gate is checked before the database is opened, so a moved set
        cannot cost an embedding call or a retrieval."""
        monkeypatch.setattr(run_retrieval.review, "read_queries",
                            lambda: [_query()])
        monkeypatch.setattr(
            run_retrieval.query_freeze, "refuse_unless_frozen",
            lambda queries, path=None: (_ for _ in ()).throw(
                RuntimeError("q030 has changed since the freeze")))
        opened = []
        monkeypatch.setattr(run_retrieval.database, "url",
                            lambda: opened.append("url") or "postgresql://x")

        assert run_retrieval.main([]) == 2
        out = capsys.readouterr().out
        assert "REFUSING to run any arm" in out
        assert "q030 has changed" in out
        assert opened == [], "the database was opened despite the refusal"

    def test_it_refuses_when_the_set_was_never_frozen(self, monkeypatch,
                                                     capsys):
        monkeypatch.setattr(run_retrieval.review, "read_queries",
                            lambda: [_query()])
        monkeypatch.setattr(
            run_retrieval.query_freeze, "refuse_unless_frozen",
            lambda queries, path=None: (_ for _ in ()).throw(
                FileNotFoundError("no freeze at corpus/query-set-freeze.json")))
        assert run_retrieval.main([]) == 2
        assert "no freeze" in capsys.readouterr().out
