"""Reading the materialised chunk store.

`scripts/build_chunks.py` writes it; the loader, the gold-span matcher and any
later reader all consume it. One reader rather than three, because three would
be three chances to disagree about what a chunk record is -- and the store is
the artifact that makes the sparse and dense arms comparable in the first
place, so a field silently dropped on one path is exactly the defect it exists
to prevent.

The store is data: 11,621 records holding 22.9 MB of filing text, kept outside
the repo beside the filings. See corpus_paths.chunks_dir.
"""

import json
import pathlib

import corpus_paths

# Every field scripts/build_chunks.py writes. Declared once so a reader can
# refuse a truncated or hand-edited store rather than silently returning
# records that are missing their citation metadata.
FIELDS = (
    "chunk_id", "accession", "ticker", "period", "item", "title",
    "index", "first_page", "last_page", "tokens", "text",
)


def default_path() -> pathlib.Path:
    return corpus_paths.chunks_dir() / "chunks.jsonl"


def read(path: pathlib.Path | None = None) -> list[dict]:
    """Every chunk in the store, in the order it was written.

    Raises on a missing store and on any record missing a field, rather than
    returning what it could parse. A partially-read store is the same class of
    hole as a filing missing from the index: nothing downstream reports it, and
    queries written against the missing text simply never hit.
    """
    path = default_path() if path is None else path
    if not path.exists():
        raise FileNotFoundError(
            f"no chunk store at {path}. Build it with "
            f"`python scripts/build_chunks.py` (it needs RAG_FILINGS_DIR)."
        )
    records = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            missing = [f for f in FIELDS if f not in record]
            if missing:
                raise ValueError(
                    f"{path} line {number}: chunk record is missing "
                    f"{missing}. The store is written by "
                    f"scripts/build_chunks.py and is not hand-editable."
                )
            records.append(record)
    if not records:
        raise ValueError(f"{path} is empty")
    return records
