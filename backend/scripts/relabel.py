"""Remove specific labels so the queue serves them again.

The labeling app's undo pops the most recent label only, which is the wrong
tool once a defect is noticed several filings later -- as happened on
2026-08-17, when `goodwill_impairment` was found to hold the goodwill carrying
balance on both CTSH filings, six filings back.

Hand-editing a JSONL file is the alternative and it is worse: a truncated line
or a dropped record changes the denominator with nothing to reveal it.

    python scripts/relabel.py --ticker CTSH --field goodwill_impairment
    python scripts/relabel.py --ticker DGX --period 2024-12-31 \\
                              --field dividends_declared_per_share --yes

Prints what it would remove and stops. Pass --yes to write. A timestamped
backup lands beside the labels file first, and the path is printed, so the
removed records are always recoverable.
"""

import argparse
import datetime
import json
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpus_paths  # noqa: E402

from evaluation.labeling import drop_labels  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--field", default=None,
                        help="omit to drop every field for this issuer")
    parser.add_argument("--period", default=None,
                        help="omit to drop both fiscal years")
    parser.add_argument("--labels", type=pathlib.Path,
                        default=pathlib.Path("corpus/labels.jsonl"))
    parser.add_argument("--backup-dir", type=pathlib.Path,
                        default=corpus_paths.backup_dir(),
                        help="outside the repo by default; label data is never committed")
    parser.add_argument("--yes", action="store_true",
                        help="actually write; without it this is a dry run")
    args = parser.parse_args()

    if not args.labels.exists():
        print(f"no labels at {args.labels}", file=sys.stderr)
        return 1

    rows = [json.loads(line) for line
            in args.labels.read_text(encoding="utf-8").splitlines() if line.strip()]
    kept, removed = drop_labels(rows, args.ticker, args.field, args.period)

    if not removed:
        # Exit 1 rather than reporting success over a no-op. A typo in the
        # ticker would otherwise print "removed 0" and read as done.
        print(f"nothing matches ticker={args.ticker} field={args.field} "
              f"period={args.period}", file=sys.stderr)
        return 1

    print(f"{len(removed)} label(s) would be removed, {len(kept)} kept:\n")
    for row in removed:
        print(f"  {row['ticker']:<6} {row['period']}  {row['field']:<30} "
              f"{row.get('answer_kind')}")

    if not args.yes:
        print("\ndry run. Pass --yes to write.")
        return 0

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    backup = args.backup_dir / f"{args.labels.stem}-before-relabel-{stamp}.jsonl"
    shutil.copy2(args.labels, backup)

    args.labels.write_text(
        "".join(json.dumps(row) + "\n" for row in kept), encoding="utf-8")

    print(f"\nbackup : {backup}")
    print(f"labels : {len(kept)} remaining. Restart the labeling app to pick "
          f"the removed instances up again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
