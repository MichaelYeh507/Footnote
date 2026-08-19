"""The chunk store builder: coverage, identity, size invariant, location.

The builder is the piece between the chunker and both indexes, and its failure
modes are quiet ones. A filing silently missing from the store is a hole no
recall number can reveal -- queries written against it simply never hit, and
the arm looks worse for a reason that has nothing to do with retrieval. A
duplicate chunk id makes two passages one row. A chunk over the pre-registered
size passes through to the embedder and is truncated there, so the vector
describes a prefix while the citation names the whole passage.

The over-window guard is the one most likely to bite: `eval_filings` exists and
returns 39 of the 44, and reusing it here would build an index missing five
filings while every test that only counted records still passed.

Written before scripts/build_chunks.py existed (red first).
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import corpus_paths  # noqa: E402
from services.chunk_assembly import (  # noqa: E402
    Chunk, TARGET_TOKENS, count_tokens,
)

import build_chunks as builder  # noqa: E402


# --------------------------------------------------------------- fixtures

# The stub chunker below returns three of these per filing, so the manifest's
# token count is derived from them rather than picked: the fixture then sits at
# a ratio of exactly 1.0 and the coverage guard is exercised by every test that
# runs main(), not only by the ones written for it.
PLAIN_TEXT = "a paragraph of chunk body text"
PLAIN_TOKENS = count_tokens(PLAIN_TEXT) * 3


def _filing(ticker, accession, fits=True, tokens=PLAIN_TOKENS):
    return {"ticker": ticker, "period": "2025-12-31", "accession": accession,
            "fits_context_window": fits, "tokens": tokens}


MANIFEST = {"filings": [
    _filing("AAA", "0000000001-26-000001"),
    _filing("BBB", "0000000002-26-000002"),
    _filing("CCC", "0000000003-26-000003", fits=False),
]}

DOCUMENT = b"<html><body><p>a paragraph</p></body></html>"


@pytest.fixture
def filings_dir(tmp_path):
    directory = tmp_path / "data" / "filings"
    directory.mkdir(parents=True)
    for filing in MANIFEST["filings"]:
        name = filing["ticker"] + "_" + filing["period"] + ".htm"
        (directory / name).write_bytes(DOCUMENT)
    return directory


@pytest.fixture
def manifest_path(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    return path


def _chunks(accession, ticker, period, count=3, item="1", text=PLAIN_TEXT, ids=None):
    return [Chunk(chunk_id=(ids[i] if ids else accession + "-" + str(i)),
                  accession=accession, ticker=ticker, period=period,
                  item=item, title="Business", index=i,
                  first_page=1 + i, last_page=1 + i, text=text)
            for i in range(count)]


def _stub(monkeypatch, fn):
    monkeypatch.setattr(builder, "chunk_filing", fn)


def _plain(file_bytes, accession, ticker, period):
    return _chunks(accession, ticker, period)


def _run(manifest_path, filings_dir, out, extra=()):
    return builder.main(["--manifest", str(manifest_path),
                         "--filings-dir", str(filings_dir),
                         "--out", str(out), *extra])


# ------------------------------------------------- every filing is present

class TestCoverage:
    def test_a_missing_document_is_named_before_anything_is_written(self, filings_dir):
        (filings_dir / "BBB_2025-12-31.htm").unlink()
        missing = builder.missing_documents(MANIFEST["filings"], filings_dir)
        assert [f["ticker"] for f in missing] == ["BBB"]

    def test_main_refuses_when_a_document_is_missing(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        (filings_dir / "BBB_2025-12-31.htm").unlink()
        _stub(monkeypatch, _plain)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 2
        assert not out.exists()
        assert "BBB" in capsys.readouterr().out

    def test_over_window_filings_are_chunked_too(
            self, monkeypatch, manifest_path, filings_dir, tmp_path):
        """All 44, not the 39 `eval_filings` returns.

        Over-window means the extraction model could not read the document
        whole. It says nothing about whether the document can be split, and
        splitting it is the reason the chunker exists.
        """
        _stub(monkeypatch, _plain)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 0
        accessions = {json.loads(line)["accession"] for line in
                      out.read_text(encoding="utf-8").splitlines()}
        assert accessions == {f["accession"] for f in MANIFEST["filings"]}

    def test_a_filing_that_yields_no_chunks_is_refused(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        def empty_for_bbb(file_bytes, accession, ticker, period):
            return [] if ticker == "BBB" else _chunks(accession, ticker, period)
        _stub(monkeypatch, empty_for_bbb)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 2
        assert not out.exists()
        assert "BBB" in capsys.readouterr().out


# ----------------------------------------------------------------- identity

class TestIdentity:
    def test_duplicate_ids_are_found(self):
        records = [{"chunk_id": "a"}, {"chunk_id": "b"}, {"chunk_id": "a"}]
        assert builder.duplicate_ids(records) == ["a"]

    def test_main_refuses_on_a_duplicate_id(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        def colliding(file_bytes, accession, ticker, period):
            return _chunks(accession, ticker, period, ids=["dup", "dup", "x"])
        _stub(monkeypatch, colliding)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 2
        assert not out.exists()
        assert "dup" in capsys.readouterr().out


# ------------------------------------------------------- the size invariant

class TestSizeInvariant:
    def test_a_multi_block_chunk_over_the_target_is_a_violation(self):
        """No chunk exceeds the target unless one block does on its own.

        Rule 2 forbids cutting a block, so a single long paragraph legitimately
        overshoots. Many blocks adding up past the target does not: that is the
        separator-budgeting defect that put 619 tokens in a 490-token chunk.
        """
        long_block = "word " * (TARGET_TOKENS + 50)
        legal = {"chunk_id": "a", "text": long_block, "tokens": TARGET_TOKENS + 60}
        illegal = {"chunk_id": "b", "text": long_block + "\n" + long_block,
                   "tokens": TARGET_TOKENS + 60}
        assert builder.oversize_violations([legal]) == []
        assert [r["chunk_id"] for r in builder.oversize_violations([illegal])] == ["b"]

    def test_main_refuses_when_a_chunk_breaks_the_size_rule(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        def too_big(file_bytes, accession, ticker, period):
            body = ("word " * TARGET_TOKENS) + "\n" + ("word " * TARGET_TOKENS)
            return _chunks(accession, ticker, period, count=1, text=body)
        _stub(monkeypatch, too_big)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 2
        assert not out.exists()
        assert "512" in capsys.readouterr().out


# -------------------------------------------------------------- where it lands

class TestLocation:
    def test_the_default_store_sits_beside_the_filings_not_in_the_repo(
            self, monkeypatch, tmp_path):
        """Chunk text is filing text. It is data, and data lives outside the repo."""
        monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
        assert builder.default_out() == tmp_path / "data" / "chunks" / "chunks.jsonl"

    def test_chunks_dir_follows_the_data_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
        assert corpus_paths.chunks_dir() == tmp_path / "data" / "chunks"

    def test_an_existing_store_is_not_overwritten_without_force(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        """The store is what both indexes were built from. Replacing it silently
        would leave the indexes describing text that no longer exists."""
        _stub(monkeypatch, _plain)
        out = tmp_path / "chunks.jsonl"
        out.write_text("previous store\n", encoding="utf-8")
        assert _run(manifest_path, filings_dir, out) == 2
        assert out.read_text(encoding="utf-8") == "previous store\n"
        assert "--force" in capsys.readouterr().out

    def test_force_replaces_it(self, monkeypatch, manifest_path, filings_dir, tmp_path):
        _stub(monkeypatch, _plain)
        out = tmp_path / "chunks.jsonl"
        out.write_text("previous store\n", encoding="utf-8")
        assert _run(manifest_path, filings_dir, out, ["--force"]) == 0
        assert "previous store" not in out.read_text(encoding="utf-8")


# ------------------------------------------------------------------ records

class TestRecords:
    @pytest.fixture
    def records(self, monkeypatch, manifest_path, filings_dir, tmp_path):
        _stub(monkeypatch, _plain)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 0
        return [json.loads(line) for line in
                out.read_text(encoding="utf-8").splitlines()]

    def test_one_json_object_per_line(self, records):
        assert len(records) == 9

    def test_each_record_carries_what_a_citation_needs(self, records):
        for record in records:
            assert set(record) == {
                "chunk_id", "accession", "ticker", "period", "item", "title",
                "index", "first_page", "last_page", "tokens", "text"}

    def test_the_token_count_is_measured_not_assumed(self, records):
        """Stored once so the indexes and the report cannot disagree about it."""
        from services.chunk_assembly import count_tokens
        for record in records:
            assert record["tokens"] == count_tokens(record["text"])


# ---------------------------------------------------------------- reporting

class TestReport:
    @pytest.fixture
    def records(self):
        return [{"chunk_id": str(n), "accession": "a", "ticker": "AAA",
                 "period": "2025-12-31", "item": "1" if n < 6 else "8",
                 "title": "t", "index": n, "first_page": 1, "last_page": 1,
                 "tokens": n * 100, "text": "word"} for n in range(1, 11)]

    def test_counts_carry_their_denominator(self, records):
        summary = builder.summarize(records, MANIFEST["filings"])
        assert summary["chunks"] == 10
        assert summary["filings"] == 3
        assert summary["by_item"]["1"] == 5
        assert summary["by_item"]["8"] == 5

    def test_percentiles_are_nearest_rank(self, records):
        summary = builder.summarize(records, MANIFEST["filings"])
        assert summary["tokens"]["median"] == 500
        assert summary["tokens"]["max"] == 1000
        assert summary["tokens"]["total"] == 5500

    def test_the_report_names_oversized_chunks_rather_than_hiding_them(self, records):
        report = builder.render_report(builder.summarize(records, MANIFEST["filings"]))
        assert "over 512" in report
        assert "10 chunks" in report

    def test_the_report_states_the_pre_registered_parameters(self, records):
        report = builder.render_report(builder.summarize(records, MANIFEST["filings"]))
        assert "512" in report and "64" in report


# ------------------------------------------------------------ text coverage

class TestTextCoverage:
    """The guard whose absence let the chunker's worst defect through.

    "Every filing yields at least one chunk" passed HON while 99.3% of its text
    was missing from the store. Counting tokens against the manifest's own
    count catches that without a second parse and without a new threshold: a
    store that covers the document must hold at least as many tokens as the
    document, because overlap only ever adds. Measured over the repaired
    corpus, the ratio runs 1.123 to 1.210 -- so the floor sits 12% below the
    tightest filing, and the ceiling is there to catch the opposite failure, an
    overlap bug duplicating text.
    """

    def test_the_ratio_is_measured_against_the_manifests_own_count(self):
        filings = [dict(_filing("AAA", "acc"), tokens=1000)]
        records = [{"accession": "acc", "tokens": 600}]
        assert builder.token_coverage(filings, records) == [("AAA", "2025-12-31", 0.6)]

    def test_a_filing_within_the_band_is_not_reported(self):
        filings = [dict(_filing("AAA", "acc"), tokens=1000)]
        records = [{"accession": "acc", "tokens": 1150}]
        assert builder.token_coverage(filings, records) == []

    def test_main_refuses_when_a_filing_loses_its_text(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        """HON's failure, reproduced: chunks exist, but not the document."""
        def thin(file_bytes, accession, ticker, period):
            return _chunks(accession, ticker, period, count=1, text="x")
        _stub(monkeypatch, thin)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 2
        assert not out.exists()
        assert "BBB" in capsys.readouterr().out

    def test_main_refuses_on_runaway_duplication(
            self, monkeypatch, manifest_path, filings_dir, tmp_path, capsys):
        def bloated(file_bytes, accession, ticker, period):
            return _chunks(accession, ticker, period, count=3, text="word " * 900)
        _stub(monkeypatch, bloated)
        out = tmp_path / "chunks.jsonl"
        assert _run(manifest_path, filings_dir, out) == 2
        assert not out.exists()


# -------------------------------------------------------------- real corpus

@pytest.mark.corpus
class TestAgainstTheRealCorpus:
    @pytest.fixture(scope="class")
    def filings(self):
        directory = corpus_paths.filings_dir()
        paths = sorted(directory.glob("*.htm")) if directory.exists() else []
        if len(paths) < 2:
            pytest.skip("no local filings at " + str(directory))
        return paths[:2]

    def test_real_filings_build_records_that_survive_every_guard(self, filings):
        manifest = [{"ticker": p.stem.split("_")[0], "period": p.stem.split("_")[1],
                     "accession": "acc-" + str(n)} for n, p in enumerate(filings)]
        records = builder.build_records(manifest, filings[0].parent)
        assert len(records) > 40
        assert builder.duplicate_ids(records) == []
        assert builder.oversize_violations(records) == []
        assert builder.filings_without_chunks(manifest, records) == []
