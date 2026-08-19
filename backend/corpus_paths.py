"""Where the corpus's primary documents live.

The 44 filings are 163 MB of SEC HTML and the calibration set another 0.4 MB.
Both are gitignored: they are data, not source, and the repo commits their
accessions and a fetch script instead. Keeping them beside the repo rather
than inside it is a layout choice, so the readers need one place to agree on.

Committed source may not name a machine-local absolute path -- see
tests/test_no_machine_local_paths.py, and the session-scoped temp directory
defect that guard was written for -- so the location is an environment
variable with a repo-relative default, never a constant:

    RAG_FILINGS_DIR      default backend/corpus/filings
    RAG_CALIBRATION_DIR  default backend/corpus/calibration

Two variables rather than one root, because the calibration filings are the
dev set. Eight issuers were read while writing the extraction prompt and are
excluded from the eval corpus; a single variable would let one override move
both, which is the one mistake here that silently crosses that split.

Read at call time, not at import: a module constant would freeze whatever the
environment happened to be when the first importer ran.
"""

import os
import pathlib

BACKEND = pathlib.Path(__file__).resolve().parent

DEFAULT_FILINGS = pathlib.Path("corpus/filings")
DEFAULT_CALIBRATION = pathlib.Path("corpus/calibration")


def _resolve(variable: str, default: pathlib.Path) -> pathlib.Path:
    # `.strip()` before the truth test: `set RAG_FILINGS_DIR=` leaves an empty
    # string rather than removing the variable, and an empty path resolves to
    # BACKEND itself -- a directory that exists, so the failure would be a
    # wrong answer rather than an error.
    raw = os.environ.get(variable, "").strip()
    path = pathlib.Path(raw) if raw else default
    # Relative overrides resolve against the repo, not the working directory,
    # so a path means the same thing whether a script was started from
    # backend/ or from the repo root.
    return path if path.is_absolute() else BACKEND / path


def filings_dir() -> pathlib.Path:
    """The 44 eval-corpus filings."""
    return _resolve("RAG_FILINGS_DIR", DEFAULT_FILINGS)


def calibration_dir() -> pathlib.Path:
    """The 8 dev-set filings the extraction prompt was written against."""
    return _resolve("RAG_CALIBRATION_DIR", DEFAULT_CALIBRATION)


def backup_dir() -> pathlib.Path:
    """Where copies of the hand labels go before anything rewrites them.

    Derived from the filings location rather than being a third variable,
    because it wants the same answer: the data root beside the repo. With the
    filings at `<data>/filings`, backups land at `<data>/label-backups`.

    This exists because the first `relabel.py` wrote its backup beside
    `corpus/labels.jsonl`, inside the repo -- and `.gitignore` names
    `backend/corpus/labels.jsonl` as an exact path, so
    `labels-before-relabel-*.jsonl` was not ignored and appeared as untracked.
    The standing rule is that label data is backed up *outside* the repo.
    """
    override = os.environ.get("RAG_BACKUP_DIR", "").strip()
    if override:
        path = pathlib.Path(override)
        return path if path.is_absolute() else BACKEND / path
    return filings_dir().parent / "label-backups"


def chunks_dir() -> pathlib.Path:
    """Where the materialised chunk store goes.

    Chunk text is filing text, so the store is data under the standing rule
    that data never enters the repo -- and it is 3.9M tokens of it. Derived
    from the filings location for the same reason as `backup_dir`: one data
    root, one place a reader has to agree on, and no third variable to set.
    """
    return filings_dir().parent / "chunks"
