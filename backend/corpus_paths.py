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
