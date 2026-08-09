"""Fetch the corpus and write its manifest.

    python scripts/fetch_filings.py

Reads the issuer list produced by select_issuers.py, downloads each primary
document, and writes corpus/manifest.json. The manifest is committed; the
filings are not (see .gitignore and HYBRID-RETRIEVAL-SEC-PLAN.md §2). Running
this from an empty checkout reproduces the corpus, which is the Phase 1 exit
condition.

Per filing the manifest records:

  sha256          of the raw bytes. EDGAR accessions are immutable once
                  accepted, so a re-fetch that differs means something is wrong
                  -- this is what makes "reproduces" checkable rather than
                  asserted.
  tokens          counted with the tokenizer the extraction model actually uses,
                  and whether the document fits the context window. The coverage
                  rate falls out of this and is a reported number, not a filter.
  has_balance_sheet / has_income_statement
                  guards the Item-8-incorporated-by-reference case: a filing
                  whose financial statements live in an exhibit is short enough
                  to pass any size check while missing two of the nine fields
                  entirely. The model would return null and score it as a
                  correct abstention.
"""

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time

import requests
import tiktoken
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from services.html_parser import extract_text_from_html  # noqa: E402

UA = "RAG-pipeline-prototype you@example.org"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=1.5,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET",)),
        pool_connections=4,
        pool_maxsize=4,
    ),
)

CONTEXT_WINDOW = 128_000          # gpt-4o-mini
OVERFLOW_THRESHOLD = 0.25         # plan §2, pre-registered
REQUEST_PAUSE = 0.2

BALANCE_SHEET = re.compile(
    r"consolidated\s+balance\s+sheet|statements?\s+of\s+financial\s+position", re.I)

# Qualifiers appear between "of" and the noun far more often than not:
# "Statements of Comprehensive Income", "Statements of Comprehensive Earnings".
# Allowing up to two intervening words covers those without matching
# "Statements of Changes in Shareholders' Equity" or "of Cash Flows".
INCOME_STATEMENT = re.compile(
    r"consolidated\s+statements?\s+of\s+(\w+\s+){0,2}"
    r"(operations|income|earnings|loss)", re.I)
INCORPORATED_BY_REFERENCE = re.compile(
    r"item\s*8[^.]{0,200}incorporated\s+(herein\s+)?by\s+reference", re.I)


def fetch(url: str, cache: pathlib.Path) -> bytes:
    """Download unless already cached. Cache is gitignored."""
    if cache.exists():
        return cache.read_bytes()
    response = SESSION.get(url, timeout=180)
    response.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(response.content)
    time.sleep(REQUEST_PAUSE)
    return response.content


def describe(raw: bytes, text: str, encoder) -> dict:
    tokens = len(encoder.encode(text))
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "document_bytes": len(raw),
        "text_chars": len(text),
        "tokens": tokens,
        "fits_context_window": tokens < CONTEXT_WINDOW,
        "pct_of_window": round(100 * tokens / CONTEXT_WINDOW, 1),
        "has_balance_sheet": bool(BALANCE_SHEET.search(text)),
        "has_income_statement": bool(INCOME_STATEMENT.search(text)),
        "item_8_by_reference": bool(INCORPORATED_BY_REFERENCE.search(text)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--issuers", type=pathlib.Path, default="corpus/issuers.json")
    parser.add_argument("--out", type=pathlib.Path, default="corpus/manifest.json")
    parser.add_argument("--filings-dir", type=pathlib.Path, default="corpus/filings")
    args = parser.parse_args()

    issuers = json.loads(pathlib.Path(args.issuers).read_text(encoding="utf-8"))
    encoder = tiktoken.encoding_for_model("gpt-4o-mini")

    rows = []
    for issuer in issuers["issuers"]:
        for filing in issuer["filings"]:
            cache = args.filings_dir / f"{issuer['ticker']}_{filing['period']}.htm"
            try:
                raw = fetch(filing["url"], cache)
            except requests.RequestException as exc:
                print(f"  !! {issuer['ticker']} {filing['period']}: {exc}", file=sys.stderr)
                continue

            text = extract_text_from_html(raw)
            row = {
                "ticker": issuer["ticker"],
                "cik": issuer["cik"],
                "name": issuer["name"],
                "sector": issuer["sector"],
                "accession": filing["accession"],
                "period": filing["period"],
                "filed": filing["filed"],
                "primary_document": filing["primary_document"],
                "url": filing["url"],
                **describe(raw, text, encoder),
            }
            rows.append(row)
            flags = ""
            if not row["fits_context_window"]:
                flags += "  OVER-WINDOW"
            if not (row["has_balance_sheet"] and row["has_income_statement"]):
                flags += "  NO-FINANCIALS"
            print(f"  {issuer['ticker']:<6}{filing['period']}  "
                  f"{row['tokens']:>7,} tok  {row['pct_of_window']:>5.0f}%{flags}",
                  file=sys.stderr)

    over = [r for r in rows if not r["fits_context_window"]]
    missing = [r for r in rows
               if not (r["has_balance_sheet"] and r["has_income_statement"])]
    by_ref = [r for r in rows if r["item_8_by_reference"]]
    rate = len(over) / len(rows) if rows else 0.0

    manifest = {
        "generated_by": "backend/scripts/fetch_filings.py",
        "issuers_source": str(args.issuers),
        "model": "gpt-4o-mini",
        "context_window": CONTEXT_WINDOW,
        "filing_count": len(rows),
        "coverage": {
            "fits": len(rows) - len(over),
            "over_window": len(over),
            "over_window_rate": round(rate, 3),
            "threshold": OVERFLOW_THRESHOLD,
            "threshold_exceeded": rate > OVERFLOW_THRESHOLD,
            "over_window_filings": [f"{r['ticker']} {r['period']}" for r in over],
        },
        "defects": {
            "missing_financial_statements":
                [f"{r['ticker']} {r['period']}" for r in missing],
            "item_8_by_reference": [f"{r['ticker']} {r['period']}" for r in by_ref],
        },
        "filings": rows,
    }

    pathlib.Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n",
                                      encoding="utf-8")

    toks = sorted(r["tokens"] for r in rows)
    print(f"\n{len(rows)} filings fetched", file=sys.stderr)
    if toks:
        print(f"tokens: min {toks[0]:,}  median {toks[len(toks)//2]:,}  "
              f"max {toks[-1]:,}", file=sys.stderr)
    print(f"\ncontext-window coverage: {len(rows) - len(over)}/{len(rows)} fit "
          f"({100 * (1 - rate):.0f}%)", file=sys.stderr)
    print(f"over window: {len(over)} ({100 * rate:.1f}%)  "
          f"threshold {100 * OVERFLOW_THRESHOLD:.0f}%  -> "
          f"{'EXCEEDED, chunking is a Phase 2 prerequisite' if rate > OVERFLOW_THRESHOLD else 'within threshold, proceed'}",
          file=sys.stderr)
    for r in over:
        print(f"    {r['ticker']} {r['period']}  {r['tokens']:,} tok "
              f"({r['pct_of_window']:.0f}%)  {r['sector']}", file=sys.stderr)
    if missing:
        print(f"\nDEFECT - no financial statements in primary document: "
              f"{len(missing)}", file=sys.stderr)
        for r in missing:
            print(f"    {r['ticker']} {r['period']}  "
                  f"balance_sheet={r['has_balance_sheet']} "
                  f"income_statement={r['has_income_statement']}", file=sys.stderr)
    print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
