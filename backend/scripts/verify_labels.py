"""Check hand labels for mechanical defects. Run after every labeling session.

    python scripts/verify_labels.py

Checks nothing about whether a value is *correct* -- that is the labeler's
judgment and no script can second-guess it. It checks the things a script can:

  protocol    every label still passes validate_label
  provenance  every anchor actually occurs in ITS OWN filing. Catches the
              wrong-document error, which is otherwise invisible: an anchor
              copied from the prior fiscal year looks perfectly reasonable in
              the record, and both years of an issuer are labeled back to back.
  duplicates  two labels for the same (accession, field) would misalign the
              positional zip in summarize() and score the wrong pairs.
  coverage    which filings are complete, and the running total.

Reads only labels and filings. Never touches model output.

Whitespace is stripped entirely before comparing anchors, not merely collapsed.
Anchors are copied from rendered HTML where "($0.4975" is contiguous, while the
extracted text puts each inline-XBRL fact on its own line as "($ 0.4975". A
collapse-to-single-space comparison reports those as missing, which is a false
alarm on a correct label -- and a false alarm here is expensive, because it
sends the labeler back to re-verify work that was already right.
"""

import argparse
import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from evaluation.labeling import QUEUE_FIELDS, validate_label  # noqa: E402
from services.html_parser import extract_text_from_html  # noqa: E402


def squeeze(text: str) -> str:
    """Drop all whitespace and lowercase. See the note in the module docstring."""
    return re.sub(r"\s+", "", text).lower()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=pathlib.Path,
                        default=pathlib.Path("corpus/labels.jsonl"))
    parser.add_argument("--manifest", type=pathlib.Path,
                        default=pathlib.Path("corpus/manifest.json"))
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=pathlib.Path("corpus/filings"))
    args = parser.parse_args()

    if not args.labels.exists():
        print(f"no labels at {args.labels}", file=sys.stderr)
        return 1

    rows = [json.loads(line) for line
            in args.labels.read_text(encoding="utf-8").splitlines() if line.strip()]
    filings = {f["accession"]: f
               for f in json.loads(args.manifest.read_text(encoding="utf-8"))["filings"]}

    problems: list[str] = []

    # protocol
    for row in rows:
        try:
            validate_label(row)
        except ValueError as exc:
            problems.append(f"PROTOCOL  {row['ticker']} {row['period']} "
                            f"{row['field']}: {exc}")

    # duplicates
    counts = collections.Counter((r["accession"], r["field"]) for r in rows)
    for (accession, field), n in counts.items():
        if n > 1:
            problems.append(f"DUPLICATE {accession} {field}: {n} labels")

    # provenance
    cache: dict[str, str] = {}
    checked = 0
    for row in rows:
        anchor = (row.get("locator") or {}).get("anchor", "")
        if not anchor.strip():
            continue
        accession = row["accession"]
        if accession not in cache:
            filing = filings.get(accession)
            if filing is None:
                problems.append(f"UNKNOWN   accession {accession} not in manifest")
                continue
            document = (args.filings_dir /
                        f"{filing['ticker']}_{filing['period']}.htm")
            cache[accession] = squeeze(extract_text_from_html(document.read_bytes()))
        checked += 1
        if squeeze(anchor) not in cache[accession]:
            problems.append(
                f"ANCHOR    {row['ticker']} {row['period']} {row['field']}: "
                f"not found in this filing -- {anchor[:60]!r}")

    # coverage
    by_filing = collections.defaultdict(set)
    for row in rows:
        by_filing[(row["ticker"], row["period"])].add(row["field"])

    print(f"{len(rows)} labels across {len(by_filing)} filings "
          f"({checked} anchors verified)\n")
    for (ticker, period), fields in sorted(by_filing.items()):
        missing = [f for f in QUEUE_FIELDS if f not in fields]
        state = "complete" if not missing else f"missing {', '.join(missing)}"
        print(f"  {ticker:<6} {period}  {len(fields)}/9  {state}")

    print()
    if problems:
        print(f"{len(problems)} PROBLEM(S):", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print("no mechanical defects found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
