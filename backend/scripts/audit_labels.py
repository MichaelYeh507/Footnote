"""Cross-check every hand label against the facts its filer tagged.

    python scripts/audit_labels.py                        # everything
    python scripts/audit_labels.py --field dividends_declared_per_share
    python scripts/audit_labels.py --ticker DGX --verbose
    python scripts/audit_labels.py --out review.md        # markdown for review

`verify_labels.py` answers "is this record well formed and does its anchor
exist". This answers a different question: **does the number agree with what
the registrant tagged for that period?** That is the check neither the anchor
nor the protocol can make, and it is the one that catches a real figure
belonging to the wrong fiscal year.

Nothing here proposes a value or edits a label. A verdict is a prompt to look.
`UNVERIFIED` is the expected outcome for a large share of instances -- filers
tag inconsistently and a figure stated only in prose carries no tag at all --
so a clean run is not "all OK", it is "nothing contradicts the filing".

Reads labels, the manifest and the filings. Never touches model output.
"""

import argparse
import collections
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import corpus_paths  # noqa: E402

from evaluation.field_audit import (  # noqa: E402
    FIELD_CONCEPTS, audit_verdict, is_fiscal_year, undimensioned,
)
from evaluation.labeling import QUEUE_FIELDS  # noqa: E402
from evaluation.xbrl import facts_named, parse_facts, period_label  # noqa: E402

# Most in need of a human look first.
SEVERITY = ["ABSENT-BUT-TAGGED", "PERIOD", "DIMS", "DIFFERS", "OK-SUM",
            "UNVERIFIED", "NO-CONCEPT", "ABSENT-OK", "OK", "MISSING"]

NEEDS_EYES = {"ABSENT-BUT-TAGGED", "PERIOD", "DIMS", "DIFFERS"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--labels", type=pathlib.Path,
                        default=pathlib.Path("corpus/labels.jsonl"))
    parser.add_argument("--manifest", type=pathlib.Path,
                        default=pathlib.Path("corpus/manifest.json"))
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=corpus_paths.filings_dir())
    parser.add_argument("--field", default=None, choices=sorted(QUEUE_FIELDS))
    parser.add_argument("--skip-field", action="append", default=[],
                        choices=sorted(QUEUE_FIELDS), metavar="FIELD",
                        help="exclude a field, mirroring label_server's flag")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--verbose", action="store_true",
                        help="show every instance, not only those needing eyes")
    parser.add_argument("--out", type=pathlib.Path, default=None,
                        help="also write a markdown report here")
    args = parser.parse_args()

    rows = [json.loads(line) for line
            in args.labels.read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    labelled = {(r["accession"], r["field"]): r for r in rows}

    filings = [f for f in manifest["filings"] if f["fits_context_window"]]
    if args.ticker:
        filings = [f for f in filings if f["ticker"] == args.ticker.upper()]
    filings.sort(key=lambda f: (f["ticker"], f["period"]))
    fields = [args.field] if args.field else list(QUEUE_FIELDS)
    fields = [f for f in fields if f not in set(args.skip_field)]

    findings, counts = [], collections.Counter()
    for filing in filings:
        document = args.filings_dir / f"{filing['ticker']}_{filing['period']}.htm"
        if not document.exists():
            print(f"not fetched: {document.name}", file=sys.stderr)
            continue
        raw = document.read_bytes().decode("utf-8", "replace")
        facts = parse_facts(raw)
        print(f"  read {filing['ticker']} {filing['period']}"
              f" ({len(facts):,} tagged facts)", file=sys.stderr)

        for field in fields:
            spec = FIELD_CONCEPTS.get(field, {})
            relevant = facts_named(facts, spec.get("concepts", ()))
            label = labelled.get((filing["accession"], field))
            code, detail = audit_verdict(label, relevant, filing["period"], field)
            counts[code] += 1
            findings.append({
                "ticker": filing["ticker"], "period": filing["period"],
                "field": field, "code": code, "detail": detail,
                "label": label,
                "year_facts": [f for f in undimensioned(relevant)
                               if is_fiscal_year(f, filing["period"],
                                                 spec.get("period", "duration"))],
            })

    findings.sort(key=lambda f: (SEVERITY.index(f["code"]), f["ticker"],
                                 f["period"], f["field"]))
    report = render(findings, counts, args.verbose)
    print(report)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"\nwritten to {args.out}", file=sys.stderr)
    return 0


def render(findings: list[dict], counts: collections.Counter, verbose: bool) -> str:
    out = ["# Label audit — labels vs the filer's own XBRL tags", ""]
    total = sum(counts.values())
    out.append(f"{total} instances checked.\n")
    out.append("| verdict | n | meaning |")
    out.append("|---|---|---|")
    meaning = {
        "OK": "matches the undimensioned fiscal-year fact",
        "OK-SUM": "matches 4x a stated quarterly rate (RESOLUTION 1)",
        "ABSENT-OK": "labelled absent, nothing tagged — consistent",
        "UNVERIFIED": "nothing tagged for the year — cannot check, not a fault",
        "NO-CONCEPT": "field has no XBRL concept (ceo_name)",
        "MISSING": "not labelled yet",
        "DIFFERS": "**a fiscal-year fact exists and the label matches none**",
        "PERIOD": "**the figure belongs to a different period**",
        "DIMS": "**matches a segment / subsidiary / scenario, not the consolidated figure**",
        "ABSENT-BUT-TAGGED": "**labelled absent while the filing tags a value**",
    }
    for code in SEVERITY:
        if counts[code]:
            out.append(f"| `{code}` | {counts[code]} | {meaning.get(code,'')} |")

    flagged = [f for f in findings if f["code"] in NEEDS_EYES]
    out.append(f"\n## Needs your eyes — {len(flagged)}\n")
    if not flagged:
        out.append("Nothing contradicts the filings.\n")
    for finding in flagged:
        out.append(_detail_block(finding))

    if verbose:
        rest = [f for f in findings if f["code"] not in NEEDS_EYES]
        out.append(f"\n## Everything else — {len(rest)}\n")
        out.append("| ticker | period | field | verdict | label |")
        out.append("|---|---|---|---|---|")
        for f in rest:
            value = (f["label"] or {}).get("value")
            kind = (f["label"] or {}).get("answer_kind", "—")
            shown = value if kind == "value" else kind
            out.append(f"| {f['ticker']} | {f['period']} | {f['field']} | "
                       f"`{f['code']}` | {shown} |")
    return "\n".join(out)


def _detail_block(finding: dict) -> str:
    label = finding["label"] or {}
    anchor = " ".join(((label.get("locator") or {}).get("anchor") or "").split())
    lines = [
        f"### `{finding['code']}` — {finding['ticker']} FY{finding['period']}"
        f" · {finding['field']}",
        "",
        f"- **labelled** `{label.get('value')}` ({label.get('answer_kind')})",
    ]
    if finding["detail"]:
        lines.append(f"- **audit** {finding['detail']}")
    if finding["year_facts"]:
        tagged = ", ".join(f"`{f['text']}` ({period_label(f)})"
                           for f in finding["year_facts"][:3])
        lines.append(f"- **filer tags for the fiscal year** {tagged}")
    if anchor:
        lines.append(f"- **anchor** `{anchor[:90]}`")
    if label.get("note"):
        lines.append(f"- **note** {label['note']}")
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
