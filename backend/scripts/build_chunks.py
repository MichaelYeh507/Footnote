"""Materialise the whole corpus as retrieval units, once, with the guards on.

    python scripts/build_chunks.py

Reads corpus/manifest.json, runs every filing through services/chunk_assembly.py
under the parameters pre-registered 2026-08-18, and writes one JSON object per
chunk to a store outside the repo. Both indexes are built from that store rather
than from the filings, so sparse and dense see byte-identical passages and any
difference between the arms is the retriever rather than the input.

**All 44 filings, not the 39 `eval_filings` returns.** Over-window means the
extraction model could not read the document whole; it says nothing about
whether the document can be split, and splitting it is what the chunker is for.

Four refusals, in order, each before the store is written:

  documents   every filing in the manifest must be on disk. A missing one is a
              hole no recall number reveals: queries written against it never
              hit, and the arm looks worse for a reason unrelated to retrieval.
  chunks      every filing must yield at least one chunk. Same hole, arrived at
              from the other side.
  identity    chunk ids must be unique across the store. The id is a truncated
              hash, so a collision is improbable rather than impossible, and it
              would silently make two passages one row.
  size        no chunk may exceed the target unless a single block does on its
              own (rule 2 forbids cutting one). An oversized chunk is truncated
              inside the embedder, so the vector would describe a prefix while
              the citation named the whole passage.

Writes nothing on any refusal, and refuses to replace an existing store without
--force: the store is what the indexes were built from, so replacing it quietly
would leave them describing text that no longer exists.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpus_paths  # noqa: E402
from services.chunk_assembly import (  # noqa: E402
    OVERLAP_TOKENS, TARGET_TOKENS, chunk_filing, count_tokens,
)


def default_out() -> pathlib.Path:
    return corpus_paths.chunks_dir() / "chunks.jsonl"


def document_path(filings_dir: pathlib.Path, filing: dict) -> pathlib.Path:
    """The same `<ticker>_<period>.htm` layout fetch_filings.py wrote."""
    return filings_dir / f"{filing['ticker']}_{filing['period']}.htm"


def missing_documents(filings: list[dict], filings_dir: pathlib.Path) -> list[dict]:
    return [f for f in filings if not document_path(filings_dir, f).exists()]


def build_records(filings: list[dict], filings_dir: pathlib.Path) -> list[dict]:
    """Every chunk in the corpus, in manifest order, as JSON-ready dicts.

    The token count is measured here and stored, so the report, the indexes and
    any later reader are all quoting one number rather than three that might
    disagree.
    """
    records: list[dict] = []
    for filing in filings:
        chunks = chunk_filing(
            document_path(filings_dir, filing).read_bytes(),
            accession=filing["accession"],
            ticker=filing["ticker"],
            period=filing["period"],
        )
        for chunk in chunks:
            records.append({
                "chunk_id": chunk.chunk_id,
                "accession": chunk.accession,
                "ticker": chunk.ticker,
                "period": chunk.period,
                "item": chunk.item,
                "title": chunk.title,
                "index": chunk.index,
                "first_page": chunk.first_page,
                "last_page": chunk.last_page,
                "tokens": count_tokens(chunk.text),
                "text": chunk.text,
            })
    return records


def filings_without_chunks(filings: list[dict], records: list[dict]) -> list[dict]:
    present = {r["accession"] for r in records}
    return [f for f in filings if f["accession"] not in present]


def duplicate_ids(records: list[dict]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for record in records:
        if record["chunk_id"] in seen and record["chunk_id"] not in duplicates:
            duplicates.append(record["chunk_id"])
        seen.add(record["chunk_id"])
    return duplicates


def oversize_violations(records: list[dict]) -> list[dict]:
    """Chunks over the target that are not a single block.

    One block longer than the target is legal and expected -- a 900-token
    paragraph cannot be cut without handing the QA layer half a sentence. Many
    blocks summing past it is the separator-budgeting defect that put 619
    tokens in a 490-token chunk, and it has to stay loud.
    """
    violations = []
    for record in records:
        if record["tokens"] <= TARGET_TOKENS:
            continue
        blocks = [line for line in record["text"].split("\n") if line.strip()]
        if len(blocks) > 1:
            violations.append(record)
    return violations


# A filing's chunks must hold at least as many tokens as the filing does, and
# not far more. The floor follows from covering the document: overlap only ever
# adds, so anything below 1.0 means text is missing. Measured over the repaired
# corpus the ratio runs 1.123 to 1.210, so the floor clears the tightest filing
# by 12% and the ceiling catches the opposite failure -- an overlap bug
# duplicating text -- with the same kind of margin.
#
# This is the guard whose absence let the chunker's worst defect through:
# "every filing yields at least one chunk" passed HON while 99.3% of its text
# was missing, and the ratio there was 0.007.
MIN_TOKEN_RATIO = 1.0
MAX_TOKEN_RATIO = 1.5


def token_coverage(filings: list[dict],
                   records: list[dict]) -> list[tuple[str, str, float]]:
    """(ticker, period, ratio) for filings whose chunk tokens leave the band.

    Checked against the manifest's own token count rather than a second parse
    of the document: it is free, and it is an independent measurement, so the
    two have to agree rather than agreeing by construction.
    """
    totals: dict[str, int] = {}
    for record in records:
        totals[record["accession"]] = totals.get(record["accession"], 0) + record["tokens"]

    outside = []
    for filing in filings:
        expected = filing.get("tokens")
        if not expected:
            continue  # nothing to compare against; the grid guards cover it
        ratio = totals.get(filing["accession"], 0) / expected
        if not MIN_TOKEN_RATIO <= ratio <= MAX_TOKEN_RATIO:
            outside.append((filing["ticker"], filing["period"], ratio))
    return outside


def _nearest_rank(values: list[int], percentile: float) -> int:
    """Percentile by nearest rank: the smallest value at or above the cut.

    Chosen over interpolation because every reported figure is then a token
    count some chunk actually has, rather than an average of two chunks.
    """
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(-(-len(ordered) * percentile // 1)) - 1))
    return ordered[index]


def summarize(records: list[dict], filings: list[dict]) -> dict:
    tokens = [r["tokens"] for r in records]
    by_item: dict[str, int] = {}
    for record in records:
        by_item[record["item"]] = by_item.get(record["item"], 0) + 1
    oversized = [r for r in records if r["tokens"] > TARGET_TOKENS]
    pages = [r for r in records if r["last_page"] > r["first_page"]]
    return {
        "chunks": len(records),
        "filings": len(filings),
        "chunked_filings": len({r["accession"] for r in records}),
        "by_item": by_item,
        "tokens": {
            "total": sum(tokens),
            "median": _nearest_rank(tokens, 0.50),
            "p75": _nearest_rank(tokens, 0.75),
            "p95": _nearest_rank(tokens, 0.95),
            "max": max(tokens) if tokens else 0,
        },
        "oversized": len(oversized),
        "spanning_pages": len(pages),
    }


def render_report(summary: dict) -> str:
    tokens = summary["tokens"]
    lines = [
        "CHUNK STORE",
        f"  parameters      target {TARGET_TOKENS} tokens, overlap "
        f"{OVERLAP_TOKENS}, pre-registered 2026-08-18",
        f"  built           {summary['chunks']} chunks from "
        f"{summary['chunked_filings']}/{summary['filings']} filings",
        f"  tokens          {tokens['total']} total, median {tokens['median']}, "
        f"p75 {tokens['p75']}, p95 {tokens['p95']}, max {tokens['max']}",
        f"  over {TARGET_TOKENS}        {summary['oversized']}/"
        f"{summary['chunks']} chunks, each a single block that cannot be cut",
        f"  span two pages  {summary['spanning_pages']}/{summary['chunks']} chunks",
        "",
        "  by item",
    ]
    for item, count in sorted(summary["by_item"].items(),
                              key=lambda pair: -pair[1]):
        label = f"Item {item}" if item else "(no section detected)"
        lines.append(f"    {label:<28} {count}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path,
                        default=pathlib.Path("corpus/manifest.json"))
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=corpus_paths.filings_dir())
    parser.add_argument("--out", type=pathlib.Path, default=None)
    parser.add_argument("--force", action="store_true",
                        help="replace an existing store")
    args = parser.parse_args(argv)

    out = args.out if args.out is not None else default_out()
    if out.exists() and not args.force:
        print(f"REFUSING to build: {out} already exists.")
        print("The indexes were built from it. Pass --force to replace it.")
        return 2

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    filings = manifest["filings"]

    missing = missing_documents(filings, args.filings_dir)
    if missing:
        print(f"REFUSING to build: {len(missing)} of {len(filings)} documents "
              f"are not in {args.filings_dir}.")
        for filing in missing:
            print(f"  {filing['ticker']} {filing['period']}")
        return 2

    print(f"chunking {len(filings)} filings from {args.filings_dir} ...")
    records = build_records(filings, args.filings_dir)

    empty = filings_without_chunks(filings, records)
    if empty:
        print(f"REFUSING to write: {len(empty)} filings produced no chunks.")
        for filing in empty:
            print(f"  {filing['ticker']} {filing['period']}")
        return 2

    duplicates = duplicate_ids(records)
    if duplicates:
        print(f"REFUSING to write: {len(duplicates)} duplicate chunk ids.")
        for chunk_id in duplicates[:10]:
            print(f"  {chunk_id}")
        return 2

    violations = oversize_violations(records)
    if violations:
        print(f"REFUSING to write: {len(violations)} chunks exceed "
              f"{TARGET_TOKENS} tokens while holding more than one block.")
        for record in violations[:10]:
            print(f"  {record['chunk_id']} {record['ticker']} "
                  f"Item {record['item']} {record['tokens']} tokens")
        return 2

    outside = token_coverage(filings, records)
    if outside:
        print(f"REFUSING to write: {len(outside)} filings hold a token count "
              f"outside [{MIN_TOKEN_RATIO}, {MAX_TOKEN_RATIO}] of the manifest's.")
        for ticker, period, ratio in outside[:10]:
            print(f"  {ticker} {period} {ratio:.3f}")
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = summarize(records, filings)
    print()
    print(render_report(summary))
    print()
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
