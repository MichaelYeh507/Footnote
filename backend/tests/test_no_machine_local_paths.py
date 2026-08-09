"""No committed source file may hardcode a machine-local absolute path.

This guard exists because of a specific defect: check_extraction_stability.py
pointed at a Claude session scratchpad
(`.../Temp/claude/<session-id>/scratchpad/filing_cache`) for its calibration
filings. That directory is session-scoped -- it does not survive the session
that created it, and it has never existed in any fresh clone. The script would
have printed "no cached text" for every filing and exited 0, writing a report
with zero results. A reproducibility failure that reports success is exactly the
kind this repo cannot afford.

The class is broader than that one path: any absolute path rooted in a user
directory or a temp directory works on this machine and nowhere else. Paths
belong in argparse defaults resolved relative to the repo, not in module
constants.
"""

import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent

SKIP_DIRS = {"venv", "__pycache__", ".pytest_cache", "node_modules"}

# This file necessarily contains the patterns it forbids.
SELF = pathlib.Path(__file__).name

PATTERNS = (
    # Windows drive-absolute: C:\..., D:/... -- in raw or escaped form.
    # The lookbehind is load-bearing: without it, any URL scheme matches, because
    # "https://t" ends in the same letter-colon-slash-letter shape as "C:/t".
    # A drive letter is exactly one letter, so anything letter-prefixed is a URL.
    (re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]{1,2}[A-Za-z]"), "drive-absolute path"),
    # POSIX user/temp roots.
    (re.compile(r"/(?:Users|home|tmp|var/folders)/"), "user or temp directory"),
    # Session-scoped scratch, whatever the root.
    (re.compile(r"scratchpad|AppData[\\/]|Local[\\/]Temp"), "session scratch directory"),
)


def source_files():
    for path in sorted(BACKEND.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == SELF:
            continue
        yield path


def test_source_files_were_found():
    """A glob that silently matches nothing would make every check below pass."""
    files = list(source_files())
    assert len(files) > 20, f"only found {len(files)} source files: {files}"


@pytest.mark.parametrize(
    "path", list(source_files()), ids=lambda p: str(p.relative_to(BACKEND))
)
def test_no_machine_local_path(path):
    text = path.read_text(encoding="utf-8")
    offenders = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, label in PATTERNS:
            match = pattern.search(line)
            if match:
                offenders.append(f"  line {lineno}: {label}: {line.strip()[:100]}")
    assert not offenders, (
        f"{path.relative_to(BACKEND)} hardcodes machine-local path(s):\n"
        + "\n".join(offenders)
    )
