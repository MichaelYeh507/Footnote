"""Reading the chunk store, and refusing a store that is not one.

The store is the artifact that makes the two arms comparable: sparse and dense
index byte-identical passages because both read this file. So the failure that
matters is not a crash, it is a *partial* read -- a truncated store, or records
missing their citation metadata, loaded without complaint. Nothing downstream
reports that. Queries written against the missing text simply never hit, and
the arm looks worse for a reason unrelated to retrieval. It is the same hole
scripts/build_chunks.py refuses at the writing end, closed again at the reading
end because the file lives outside the repo and nothing stops it being
truncated by a full disk or edited by hand.

Added after a perturbation report showed `chunk_store`'s field check was the
one guard in the retrieval path with no test behind it: removing the check
broke nothing.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402
from services import chunk_store  # noqa: E402


def _record(chunk_id="c1", **overrides):
    record = {
        "chunk_id": chunk_id, "accession": "acc-1", "ticker": "AAA",
        "period": "2025-12-31", "item": "1", "title": "Business",
        "index": 0, "first_page": 1, "last_page": 1, "tokens": 10,
        "text": "a passage of filing text",
    }
    record.update(overrides)
    return record


def _store(tmp_path, records, name="chunks.jsonl"):
    path = tmp_path / name
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )
    return path


class TestReading:

    def test_reads_every_record_in_order(self, tmp_path):
        path = _store(tmp_path, [_record("c1"), _record("c2"), _record("c3")])
        assert [r["chunk_id"] for r in chunk_store.read(path)] == \
            ["c1", "c2", "c3"]

    def test_preserves_non_ascii_text(self, tmp_path):
        """The store holds 21,839 em dashes and 13,603 curly apostrophes, and
        the gold-span matcher folds them. A reader that mangled the encoding
        would make every fold rule meaningless."""
        path = _store(tmp_path, [_record(text="Grainger’s risk — and reward")])
        assert chunk_store.read(path)[0]["text"] == "Grainger’s risk — and reward"

    def test_skips_blank_lines(self, tmp_path):
        path = tmp_path / "chunks.jsonl"
        path.write_text(json.dumps(_record("c1")) + "\n\n"
                        + json.dumps(_record("c2")) + "\n", encoding="utf-8")
        assert len(chunk_store.read(path)) == 2


class TestTheRefusals:
    """Each of these is a partial read, which is the failure mode that hides."""

    def test_a_missing_store_raises_with_guidance(self, tmp_path):
        with pytest.raises(FileNotFoundError) as raised:
            chunk_store.read(tmp_path / "absent.jsonl")
        assert "build_chunks" in str(raised.value)

    def test_an_empty_store_is_refused(self, tmp_path):
        path = tmp_path / "chunks.jsonl"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ValueError):
            chunk_store.read(path)

    def test_a_record_missing_a_field_is_refused(self, tmp_path):
        """This is the guard the perturbation report caught as untested."""
        broken = _record("c2")
        del broken["first_page"]
        path = _store(tmp_path, [_record("c1"), broken])
        with pytest.raises(ValueError) as raised:
            chunk_store.read(path)
        assert "first_page" in str(raised.value)

    def test_the_refusal_names_the_line(self, tmp_path):
        """A store has 11,621 lines. A refusal that does not say which one
        cannot be acted on."""
        broken = _record("c3")
        del broken["text"]
        path = _store(tmp_path, [_record("c1"), _record("c2"), broken])
        with pytest.raises(ValueError) as raised:
            chunk_store.read(path)
        assert "line 3" in str(raised.value)

    @pytest.mark.parametrize("field", chunk_store.FIELDS)
    def test_every_declared_field_is_actually_required(self, tmp_path, field):
        """Parametrized over FIELDS rather than spot-checking one.

        A field present in the tuple but not genuinely required would be a
        declaration that lies -- and `item` and `title` are the ones at risk,
        because they are empty strings for the front matter and the
        post-signature tail, so a truthiness check would let them through.
        """
        broken = _record()
        del broken[field]
        path = _store(tmp_path, [broken])
        with pytest.raises(ValueError) as raised:
            chunk_store.read(path)
        assert field in str(raised.value)

    def test_an_empty_item_is_not_treated_as_missing(self, tmp_path):
        """1,393 of 11,621 chunks carry an empty item deliberately -- AMENDMENT
        2 gives the front matter and the post-signature tail their own sections
        with no Item label. Refusing them would reject 12% of the store."""
        path = _store(tmp_path, [_record(item="", title="")])
        assert chunk_store.read(path)[0]["item"] == ""


class TestTheDefaultLocation:

    def test_default_path_follows_the_corpus_root(self, tmp_path, monkeypatch):
        """Derived from RAG_FILINGS_DIR, not a second variable and never a
        machine-local constant -- see corpus_paths and the guard in
        tests/test_no_machine_local_paths.py."""
        monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
        assert chunk_store.default_path() == \
            tmp_path / "data" / "chunks" / "chunks.jsonl"

    def test_read_uses_the_default_when_given_no_path(self, tmp_path,
                                                      monkeypatch):
        monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
        chunks = tmp_path / "data" / "chunks"
        chunks.mkdir(parents=True)
        _store(chunks, [_record("c1")])
        assert [r["chunk_id"] for r in chunk_store.read()] == ["c1"]
