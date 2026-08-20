"""The scoring CLI: its refusals and its report.

The statistics live in `evaluation/retrieval_scoring.py` and are tested there.
What is tested here is everything between the files and the numbers, which is
where a wrong result can be produced without anything failing:

  * scoring a run made against a different query set than the one now frozen;
  * scoring a `--limit` run, whose smaller denominator raises every rate;
  * scoring a ranked list that was edited after the run wrote it;
  * pairing one run's rankings with another run's provenance, so the numbers
    are printed under parameters that did not produce them.

Every one of those produces a plausible report. None of them produces an error
unless something refuses.
"""

import hashlib
import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import score_retrieval  # noqa: E402
from evaluation import retrieval_scoring as scoring  # noqa: E402

DIGEST = "a35b2634f47608fdee4d1dbd612e6d6d56f64d1e261ce85c4e6bb00d5cbde16a"


def _query(query_id, stratum="exact_entity", span="the first span"):
    return {"query_id": query_id, "stratum": stratum, "query": "a question",
            "gold": [{"accession": "acc-1", "span": span}]}


def _unanswerable(query_id):
    return {"query_id": query_id, "stratum": "unanswerable",
            "query": "nothing answers this", "gold": []}


def _chunk(chunk_id, text, accession="acc-1"):
    return {"chunk_id": chunk_id, "accession": accession, "ticker": "AAA",
            "period": "2025-12-31", "item": "1", "title": "Business",
            "index": 0, "first_page": 1, "last_page": 1,
            "tokens": len(text.split()), "text": text}


QUERIES = [_query("q1"), _query("q2", "conceptual", "the second span"),
           _unanswerable("q3")]
RECORDS = [_chunk("g1", "the first span"), _chunk("g2", "the second span"),
           _chunk("n1", "noise one")]


def _ranking(query_id, sparse, dense, hybrid):
    return {"query_id": query_id, "stratum": "exact_entity", "tsquery": "'a'",
            "arms": {"sparse": [[c, 1.0] for c in sparse],
                     "dense": [[c, 0.1] for c in dense],
                     "hybrid": [[c, 0.01] for c in hybrid]}}


@pytest.fixture
def run(tmp_path):
    """A complete, self-consistent run on disk: two rankings and a provenance
    whose sha256 matches the file it names."""
    out = tmp_path / "retrieval"
    out.mkdir()
    rankings = out / "rankings-20260820-120000.jsonl"
    rankings.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in [
            _ranking("q1", ["g1", "n1"], ["n1", "g1"], ["g1", "n1"]),
            _ranking("q2", ["n1"], ["g2"], ["g2", "n1"]),
        ]), encoding="utf-8")
    provenance = out / "provenance-20260820-120000.json"
    provenance.write_text(json.dumps({
        "run": "20260820-120000",
        "complete": True,
        "query_set": {"set_sha256": DIGEST},
        "sparse": {"configuration": "english", "lexeme_combination": "OR",
                   "rank_function": "ts_rank_cd", "rank_normalization": 0,
                   "depth": 50},
        "dense": {"model": "text-embedding-3-small", "dimensions": 1536,
                  "ef_search_applied": 100, "depth": 50,
                  "embeddings_sha256": "deadbeef"},
        "hybrid": {"k": 60, "depth": 50},
        "rankings": {
            "path": rankings.name,
            "sha256": hashlib.sha256(rankings.read_bytes()).hexdigest()},
    }, indent=2), encoding="utf-8")
    return out, rankings, provenance


@pytest.fixture
def wired(monkeypatch, run):
    out, rankings, provenance = run
    monkeypatch.setattr(score_retrieval.review, "read_queries",
                        lambda: list(QUERIES))
    monkeypatch.setattr(score_retrieval.query_freeze, "refuse_unless_frozen",
                        lambda q, path=None: {
                            "set_sha256": DIGEST,
                            "composition": {"duplicate_span_advisories": 0}})
    monkeypatch.setattr(score_retrieval.chunk_store, "read",
                        lambda: list(RECORDS))
    monkeypatch.setattr(score_retrieval.corpus_paths, "retrieval_dir",
                        lambda: out)
    return out, rankings, provenance


class TestPairingARunWithItsProvenance:

    def test_the_provenance_is_found_by_the_run_stamp(self, run):
        out, rankings, provenance = run
        assert score_retrieval.provenance_for(rankings) == provenance

    def test_a_rankings_file_with_no_provenance_is_refused(self, tmp_path):
        orphan = tmp_path / "rankings-20260820-999999.jsonl"
        orphan.write_text("", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="provenance"):
            score_retrieval.provenance_for(orphan)

    def test_the_newest_run_is_paired_with_its_own_provenance(self, run):
        """Two runs minutes apart is the case that matters: taking 'the newest
        of each' independently would report one run's numbers under the other
        run's parameters."""
        out, rankings, _ = run
        newer = out / "rankings-20260820-130000.jsonl"
        newer.write_text("", encoding="utf-8")
        (out / "provenance-20260820-130000.json").write_text(
            "{}", encoding="utf-8")
        chosen, provenance = score_retrieval.newest_run(out)
        assert chosen == newer
        assert provenance.name == "provenance-20260820-130000.json"

    def test_an_empty_output_directory_is_refused(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            score_retrieval.newest_run(tmp_path / "nothing")


class TestTheRunChecks:

    def _provenance(self, run, **overrides):
        _, rankings, provenance = run
        record = json.loads(provenance.read_text(encoding="utf-8"))
        record.update(overrides)
        return record, rankings

    def test_a_consistent_run_passes(self, run):
        record, rankings = self._provenance(run)
        assert score_retrieval.check_run(
            record, rankings, {"set_sha256": DIGEST}) == []

    def test_an_incomplete_run_is_refused(self, run):
        record, rankings = self._provenance(run, complete=False)
        problems = score_retrieval.check_run(record, rankings,
                                             {"set_sha256": DIGEST})
        assert any("incomplete" in p for p in problems)

    def test_a_run_against_a_different_query_set_is_refused(self, run):
        record, rankings = self._provenance(
            run, query_set={"set_sha256": "0" * 64})
        problems = score_retrieval.check_run(record, rankings,
                                             {"set_sha256": DIGEST})
        assert any("mix two query sets" in p for p in problems)

    def test_an_edited_rankings_file_is_refused(self, run):
        """The file is data outside the repo and nothing stops it being
        hand-edited. Its provenance recorded what it was."""
        record, rankings = self._provenance(run)
        rankings.write_text(
            rankings.read_text(encoding="utf-8").replace("n1", "g1"),
            encoding="utf-8")
        problems = score_retrieval.check_run(record, rankings,
                                             {"set_sha256": DIGEST})
        assert any("edited since the run" in p for p in problems)

    def test_every_failing_check_is_reported_not_just_the_first(self, run):
        record, rankings = self._provenance(
            run, complete=False, query_set={"set_sha256": "0" * 64})
        assert len(score_retrieval.check_run(
            record, rankings, {"set_sha256": DIGEST})) == 2


class TestLoadingRankings:

    def test_it_keys_by_query_id(self, run):
        _, rankings, _ = run
        assert sorted(score_retrieval.load_rankings(rankings)) == ["q1", "q2"]

    def test_a_repeated_query_id_is_refused(self, tmp_path):
        """One query cannot have two rankings. Keying by id would silently keep
        the last, and the two might be from different runs."""
        path = tmp_path / "rankings-x.jsonl"
        record = json.dumps(_ranking("q1", ["g1"], ["g1"], ["g1"]))
        path.write_text(record + "\n" + record + "\n", encoding="utf-8")
        with pytest.raises(ValueError, match="twice"):
            score_retrieval.load_rankings(path)


class TestMain:

    def test_a_clean_run_scores_and_prints_the_pre_registered_rows(
            self, wired, capsys):
        assert score_retrieval.main([]) == 0
        out = capsys.readouterr().out
        assert "RECALL@1" in out and "RECALL@5" in out
        assert "McNemar discordant pairs" in out
        assert "DUPLICATE-SPAN advisories" in out

    def test_the_report_says_the_unanswerable_were_excluded(self, wired,
                                                            capsys):
        score_retrieval.main([])
        out = capsys.readouterr().out
        assert "1 unanswerable EXCLUDED" in out
        assert "2 answerable" in out

    def test_every_arm_appears_in_the_report(self, wired, capsys):
        score_retrieval.main([])
        out = capsys.readouterr().out
        for arm in scoring.ARMS:
            assert arm in out

    def test_it_refuses_when_the_set_has_moved_since_the_freeze(
            self, monkeypatch, wired, capsys):
        monkeypatch.setattr(
            score_retrieval.query_freeze, "refuse_unless_frozen",
            lambda q, path=None: (_ for _ in ()).throw(
                RuntimeError("q030 has changed since the freeze")))
        assert score_retrieval.main([]) == 2
        assert "q030 has changed" in capsys.readouterr().out

    def test_it_refuses_an_incomplete_run(self, monkeypatch, wired, capsys):
        out, _, provenance = wired
        record = json.loads(provenance.read_text(encoding="utf-8"))
        record["complete"] = False
        provenance.write_text(json.dumps(record), encoding="utf-8")
        assert score_retrieval.main([]) == 2
        assert "incomplete" in capsys.readouterr().out

    def test_it_refuses_a_rankings_file_missing_a_query(self, wired, capsys):
        out, rankings, provenance = wired
        lines = rankings.read_text(encoding="utf-8").splitlines()
        rankings.write_text(lines[0] + "\n", encoding="utf-8")
        record = json.loads(provenance.read_text(encoding="utf-8"))
        record["rankings"]["sha256"] = hashlib.sha256(
            rankings.read_bytes()).hexdigest()
        provenance.write_text(json.dumps(record), encoding="utf-8")
        assert score_retrieval.main([]) == 2
        assert "q2" in capsys.readouterr().out

    def test_it_writes_the_json_record_when_asked(self, wired, tmp_path):
        assert score_retrieval.main(
            ["--json", str(tmp_path / "scores.json")]) == 0
        record = json.loads(
            (tmp_path / "scores.json").read_text(encoding="utf-8"))
        assert record["summary"]["queries"]["answerable"] == 2
        assert record["provenance"]["hybrid"]["k"] == 60

    def test_a_store_that_moved_since_the_freeze_is_called_out(
            self, monkeypatch, wired, capsys):
        """AMENDMENT 5's count is recounted from the store, not copied. A
        disagreement with the freeze means the store changed underneath."""
        monkeypatch.setattr(
            score_retrieval.query_freeze, "refuse_unless_frozen",
            lambda q, path=None: {
                "set_sha256": DIGEST,
                "composition": {"duplicate_span_advisories": 11}})
        score_retrieval.main([])
        assert "DISAGREEMENT" in capsys.readouterr().out


class TestTheReportBody:

    def _summary(self):
        return scoring.summarize(
            QUERIES, RECORDS,
            {"q1": _ranking("q1", ["g1"], ["n1"], ["g1"]),
             "q2": _ranking("q2", ["n1"], ["g2"], ["g2"])})

    def _provenance(self):
        return {"run": "x", "query_set": {"set_sha256": DIGEST},
                "sparse": {"configuration": "english",
                           "lexeme_combination": "OR",
                           "rank_function": "ts_rank_cd",
                           "rank_normalization": 0, "depth": 50},
                "dense": {"model": "text-embedding-3-small",
                          "dimensions": 1536, "ef_search_applied": 100,
                          "depth": 50, "embeddings_sha256": "beef"},
                "hybrid": {"k": 60, "depth": 50}}

    def test_no_rate_is_printed_without_its_denominator(self):
        """The standing rule: no undenominated numbers.

        Selects on the **interval**, not on the `=`. The first version of this
        test looked for lines containing both `=` and `[`, which meant that
        dropping `hits/n =` from the format also dropped the line from the
        test -- it passed against a report printing bare rates. Caught by
        perturbation.

        A recall row carries `hits/n`; a McNemar row carries `b=` and `c=`,
        which are its counts. Every line showing an interval must carry one or
        the other.
        """
        import re
        text = score_retrieval.report(self._summary(), self._provenance(), 0)
        interval = re.compile(r"\[\d\.\d{3}, \d\.\d{3}\]")
        lines = [line for line in text.splitlines() if interval.search(line)]
        assert len(lines) >= 12, (
            f"only {len(lines)} lines carry an interval; the report is not "
            f"being examined")
        for line in lines:
            assert re.search(r"\d+/\d+", line) or ("b=" in line and "c=" in line), \
                line

    def test_a_row_below_the_reporting_floor_says_so(self):
        """These strata are n=1 in this fixture, far under the n=25 gate. The
        row is still printed -- the denominator is what a reader needs -- with
        the fact attached."""
        text = score_retrieval.report(self._summary(), self._provenance(), 0)
        assert "below the reporting floor" in text

    def test_the_configuration_that_produced_the_numbers_is_printed(self):
        text = score_retrieval.report(self._summary(), self._provenance(), 0)
        assert "text-embedding-3-small at 1536d" in text
        assert "ef_search 100" in text
        assert "RRF k=60" in text
        assert "normalization 0" in text

    def test_arms_that_agree_everywhere_report_no_rate(self):
        """b + c = 0 has no rate, and printing 0.000 would assert one."""
        summary = scoring.summarize(
            QUERIES, RECORDS,
            {"q1": _ranking("q1", ["g1"], ["g1"], ["g1"]),
             "q2": _ranking("q2", ["g2"], ["g2"], ["g2"])})
        text = score_retrieval.report(summary, self._provenance(), 0)
        assert "there is no rate to report" in text

    def test_it_names_the_one_configuration_limitation(self):
        text = score_retrieval.report(self._summary(), self._provenance(), 0)
        assert "at one" in text and "configuration" in text
