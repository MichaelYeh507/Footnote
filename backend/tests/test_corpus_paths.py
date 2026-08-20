"""The primary documents' location is configurable, and configured in one place.

The filings are 163 MB of SEC HTML. They are data, not source, and the layout
that keeps them beside the repo rather than inside it needs the readers to
agree on where to look. Before this module the location was resolved six
different ways -- five argparse defaults relative to the working directory and
one hardcoded module constant in label_server.py -- so moving the corpus fixed
five of them and silently broke the labeling app.

Committed source may not name a machine-local absolute path
(tests/test_no_machine_local_paths.py), so the location is an environment
variable with a repo-relative default, never a constant.
"""

import os
import pathlib
import subprocess
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

import corpus_paths  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Each test states its own environment; none inherits the developer's."""
    monkeypatch.delenv("RAG_FILINGS_DIR", raising=False)
    monkeypatch.delenv("RAG_CALIBRATION_DIR", raising=False)


def test_default_filings_dir_is_inside_the_repo():
    assert corpus_paths.filings_dir() == BACKEND / "corpus" / "filings"


def test_default_calibration_dir_is_inside_the_repo():
    assert corpus_paths.calibration_dir() == BACKEND / "corpus" / "calibration"


def test_absolute_env_var_is_honored(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "filings"))
    assert corpus_paths.filings_dir() == tmp_path / "filings"


def test_calibration_has_its_own_variable(monkeypatch, tmp_path):
    """Calibration filings are the dev set. Pointing one must not move the other."""
    monkeypatch.setenv("RAG_CALIBRATION_DIR", str(tmp_path / "calib"))
    assert corpus_paths.calibration_dir() == tmp_path / "calib"
    assert corpus_paths.filings_dir() == BACKEND / "corpus" / "filings"


def test_relative_env_var_resolves_against_the_repo_not_the_cwd(monkeypatch):
    """A relative override must mean the same thing from any working directory.

    Resolving against the cwd is the defect this rules out: the scripts are
    documented as `cd backend && python scripts/...`, and anyone who ran one
    from the repo root instead would silently read an empty directory.
    """
    monkeypatch.setenv("RAG_FILINGS_DIR", "../rag-pipeline-data/filings")
    monkeypatch.chdir(pathlib.Path(BACKEND).parent)
    assert corpus_paths.filings_dir() == (BACKEND / ".." / "rag-pipeline-data"
                                          / "filings")


def test_blank_env_var_falls_back_to_the_default(monkeypatch):
    """An unset variable and one set to empty must not behave differently.

    `set RAG_FILINGS_DIR=` leaves an empty string on Windows rather than
    removing the variable. Treating that as a path yields the backend
    directory itself, which exists -- so the failure would be a wrong answer,
    not an error.
    """
    monkeypatch.setenv("RAG_FILINGS_DIR", "   ")
    assert corpus_paths.filings_dir() == BACKEND / "corpus" / "filings"


def test_env_var_is_read_at_call_time_not_at_import(monkeypatch, tmp_path):
    """Module-level constants would freeze whatever the environment was on import."""
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "a"))
    first = corpus_paths.filings_dir()
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "b"))
    assert corpus_paths.filings_dir() != first


def test_backup_dir_follows_the_data_root_not_the_repo(monkeypatch, tmp_path):
    """Label backups belong beside the filings, outside the repo.

    The first `relabel.py` wrote its backup next to `corpus/labels.jsonl`, and
    `.gitignore` names that file as an exact path -- so the backup landed
    untracked *inside* the repo. Hand-label data, one `git add -A` from being
    committed, against a standing rule that says never.

    The data root is spelled with `tmp_path` rather than a literal drive path:
    test_no_machine_local_paths.py forbids drive-absolute strings in committed
    source, and it failed the first draft of this test for exactly that.
    """
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
    assert corpus_paths.backup_dir() == tmp_path / "data" / "label-backups"


def test_backup_dir_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_BACKUP_DIR", str(tmp_path / "b"))
    assert corpus_paths.backup_dir() == tmp_path / "b"


def test_queries_dir_follows_the_data_root_not_the_repo(monkeypatch, tmp_path):
    """The query set is data: gold spans are verbatim filing text.

    Gold is `(accession, quoted span)`, so the file carries quoted passages out
    of filings whose content the repo never commits. It belongs beside the
    filings and the chunk store, under the same rule as labels and predictions.
    """
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
    assert corpus_paths.queries_dir() == tmp_path / "data" / "queries"


def test_the_three_data_locations_are_distinct_siblings(monkeypatch, tmp_path):
    """One data root, three directories, no two of them the same place.

    Pinned because the cheap wrong implementations are `filings_dir() /
    "queries"` and returning `chunks_dir()` outright, and both put the query
    set somewhere a reader would still find it -- the failure would be a wrong
    location rather than an error.
    """
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "data" / "filings"))
    locations = [corpus_paths.filings_dir(),
                 corpus_paths.chunks_dir(),
                 corpus_paths.queries_dir()]
    assert len(set(locations)) == 3, locations
    assert {p.parent for p in locations} == {tmp_path / "data"}


def test_queries_dir_is_read_at_call_time_not_at_import(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "a" / "filings"))
    first = corpus_paths.queries_dir()
    monkeypatch.setenv("RAG_FILINGS_DIR", str(tmp_path / "b" / "filings"))
    assert corpus_paths.queries_dir() != first


def test_gitignore_covers_the_in_repo_default_chunk_store():
    """The same hazard as the query set, one directory over, and older.

    `chunks_dir()` derives from `filings_dir()` too, so a run without
    RAG_FILINGS_DIR set writes the store -- 11,621 records, 22.9 MB of filing
    text -- to `backend/corpus/chunks/`. That was untracked and unignored while
    the query set beside it was covered.
    """
    patterns = _gitignore_patterns()
    for candidate in ("backend/corpus/chunks/chunks.jsonl",
                      "backend/corpus/chunks/chunks-20260819.jsonl"):
        assert _ignored(candidate, patterns), (
            f"{candidate} is not covered by any .gitignore pattern")


def test_gitignore_covers_the_in_repo_default_query_set():
    """The default is repo-relative, so the default lands *inside* the repo.

    `queries_dir()` derives from `filings_dir()`, whose default is
    `backend/corpus/filings` -- so anyone running without RAG_FILINGS_DIR set
    writes the query set to `backend/corpus/queries/`, untracked and, without
    this pattern, unignored. That is the exact state README-TARGET.md sat in
    for a day: untracked is not protected.
    """
    patterns = _gitignore_patterns()
    for candidate in ("backend/corpus/queries/queries.jsonl",
                      "backend/corpus/queries/queries-draft-batch1.jsonl"):
        assert _ignored(candidate, patterns), (
            f"{candidate} is not covered by any .gitignore pattern")


def _gitignore_patterns():
    """The live patterns, comments and blank lines dropped."""
    gitignore = BACKEND.parent / ".gitignore"
    return [line.strip() for line in
            gitignore.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def _ignored(candidate, patterns):
    """Whether git would ignore `candidate`, for the two pattern shapes used here.

    A glob on the path, or a trailing-slash directory pattern covering
    everything beneath it. `fnmatch` alone gets the second case wrong --
    "backend/corpus/queries/" never matches a file inside it -- and a test that
    silently answers False would report the protection missing when it is
    present, or pass a rewrite that removed it.
    """
    import fnmatch

    for pattern in patterns:
        if pattern.endswith("/"):
            if candidate.startswith(pattern):
                return True
        elif fnmatch.fnmatch(candidate, pattern):
            return True
    return False


def test_gitignore_covers_label_backups_by_glob():
    """An exact-path ignore rule does not cover a timestamped sibling.

    Checked by matching realistic filenames against the patterns rather than
    grepping for a literal line, so reformatting `.gitignore` cannot make this
    pass while the protection is gone.
    """
    patterns = _gitignore_patterns()
    for candidate in ("backend/corpus/labels-before-relabel-20260817-191821.jsonl",
                      "backend/corpus/labels.jsonl",
                      "backend/corpus/predictions-old.jsonl"):
        assert _ignored(candidate, patterns), (
            f"{candidate} is not covered by any .gitignore pattern")


READERS = (
    "scripts/label_server.py",
    "scripts/label_filings.py",
    "scripts/verify_labels.py",
    "scripts/run_extraction.py",
    "scripts/fetch_filings.py",
)


@pytest.mark.parametrize("relative", READERS)
def test_every_reader_resolves_through_corpus_paths(relative):
    """The point of the module is that there is no second place to change."""
    source = (BACKEND / relative).read_text(encoding="utf-8")
    assert "corpus_paths" in source, (
        f"{relative} does not import corpus_paths, so it will keep reading the "
        f"old location after the corpus moves"
    )


@pytest.mark.parametrize("relative", READERS)
def test_no_reader_hardcodes_the_filings_path(relative):
    """A leftover literal is the exact defect this module exists to remove."""
    source = (BACKEND / relative).read_text(encoding="utf-8")
    for literal in ('"corpus/filings"', "'corpus/filings'",
                    '"corpus" / "filings"'):
        assert literal not in source, f"{relative} still hardcodes {literal}"


def test_label_server_reads_the_configured_directory(tmp_path):
    """The end-to-end property, checked against the real server module.

    label_server.py resolves FILINGS at import. This starts a fresh
    interpreter with the variable set and asks the module where it is looking
    -- the only check that would have caught the hardcoded constant.
    """
    target = tmp_path / "elsewhere"
    target.mkdir()
    env = dict(os.environ, RAG_FILINGS_DIR=str(target), PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['label_server']; "
         "sys.path.insert(0, r'{}'); "
         "import importlib.util, pathlib; "
         "spec=importlib.util.spec_from_file_location('ls', r'{}'); "
         "m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
         "print(m.FILINGS)".format(BACKEND, BACKEND / "scripts" / "label_server.py")],
        capture_output=True, text=True, env=env, cwd=str(BACKEND),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target), result.stdout
