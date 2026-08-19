"""Score the extraction run against the frozen hand labels.

    python scripts/score_predictions.py

Runs the whole pre-registered pipeline: verify the labels file is the frozen
one, validate that labels and predictions both cover exactly the manifest's
(in-window filing x field) grid, join the two by key, score through
evaluation/scoring.py + matching.py, and print the report in the format plan
section 5 fixed before any of this data existed.

Three refusals, in order, each before any number is computed:

  hash      the labels bytes must match the sha256 recorded at the freeze
            (2026-08-18). Scoring any other bytes requires saying so with
            --labels-sha256, which is what makes a post-freeze edit a
            disclosed event rather than a quiet one.
  grid      one record per (accession, field), for labels and predictions
            alike, matching the manifest exactly. Every gap is printed.
  pairing   labels and predictions are joined by (accession, field), never
            by file position. summarize() zips positionally; a runner that
            trusted file order would score the wrong pairs and nothing
            downstream could tell.

Reads labels and predictions; writes nothing unless --json is given.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from evaluation.extraction_run import EVAL_FIELDS, eval_filings  # noqa: E402
from evaluation.scoring import (  # noqa: E402
    MIN_REPORTABLE_N, Outcome, classify, summarize,
)

# sha256 of corpus/labels.jsonl at the pre-unblinding freeze, 2026-08-18
# (backup labels-FINAL-prescoring-20260818-164957.jsonl). The audit link
# between every reported number and the label set that produced it.
FROZEN_LABELS_SHA256 = (
    "ad155dddb4a11c772f37973bcda0b3f2464da57798901aec5366c3ca2d671c50"
)

_FIELD_ORDER = {field: i for i, field in enumerate(EVAL_FIELDS)}


def load_jsonl(path: pathlib.Path) -> list[dict]:
    return [json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def expected_grid(manifest: dict) -> set[tuple[str, str]]:
    """Every (accession, field) pair the run must cover: the manifest's
    in-window filings crossed with the nine measured fields. Derived from the
    manifest rather than from either data file, so a truncated labels file
    cannot shrink the grid to match itself."""
    return {(filing["accession"], field)
            for filing in eval_filings(manifest)
            for field in EVAL_FIELDS}


def _duplicates(rows: list[dict]) -> list[tuple]:
    seen, dupes = set(), []
    for row in rows:
        key = (row["accession"], row["field"])
        if key in seen:
            dupes.append(key)
        seen.add(key)
    return dupes


def grid_gaps(grid: set, labels: list[dict], predictions: list[dict]) -> list[str]:
    """Every way the two files fail to cover the grid exactly once each.

    Returns human-readable lines, empty when clean. All defects are collected
    rather than stopping at the first: a session that fixes one gap only to
    hit the next is a session spent rediscovering this function's job.
    """
    gaps = []

    for name, rows in (("label", labels), ("prediction", predictions)):
        for key in _duplicates(rows):
            gaps.append(f"duplicate {name}: {key[0]} {key[1]}")
        keys = {(r["accession"], r["field"]) for r in rows}
        for accession, field in sorted(grid - keys):
            gaps.append(f"missing {name}: {accession} {field}")
        for accession, field in sorted(keys - grid):
            gaps.append(f"unexpected {name} (not in the manifest grid): "
                        f"{accession} {field}")

    for row in labels:
        if row.get("status") != "labeled":
            gaps.append(f"label not scoreable: {row['accession']} "
                        f"{row['field']} status={row.get('status')!r}")

    for row in predictions:
        if "value" not in row:
            gaps.append(f"prediction missing its 'value' key: "
                        f"{row['accession']} {row['field']} -- refusing to "
                        f"read absence as an abstention")

    return sorted(gaps)


def align(labels: list[dict], predictions: list[dict]):
    """(labels sorted deterministically, prediction values in the same order).

    Joined strictly by (accession, field). Raises KeyError on a pair with no
    prediction rather than skipping it -- run grid_gaps first for a readable
    account of what is missing.
    """
    by_key = {(p["accession"], p["field"]): p for p in predictions}
    ordered = sorted(labels, key=lambda r: (r["ticker"], r["period"],
                                            _FIELD_ORDER[r["field"]]))
    values = [by_key[(r["accession"], r["field"])]["value"] for r in ordered]
    return ordered, values


def _row(label: dict, predicted) -> dict:
    return {
        "ticker": label["ticker"],
        "period": label["period"],
        "accession": label["accession"],
        "field": label["field"],
        "answer_kind": label["answer_kind"],
        "label_value": label.get("value"),
        "predicted": predicted,
        "ambiguous": bool(label.get("ambiguous")),
        "note": label.get("note", ""),
    }


def detail_rows(labels: list[dict], values: list) -> dict:
    """The instance-level lists the report and the walkthrough need.

    ceo_name_mismatches holds WRONG_VALUE outcomes only: the plan's separate
    list exists so a reader can judge the name rule on pairs of names, and an
    abstention is not a pair of names. It appears under missed instead.
    """
    detail = {"false_extractions": [], "ceo_name_mismatches": [],
              "wrong_values": [], "missed": []}
    for label, predicted in zip(labels, values):
        outcome = classify(label, predicted)
        row = _row(label, predicted)
        if outcome is Outcome.FALSE_EXTRACTION:
            detail["false_extractions"].append(row)
        elif outcome is Outcome.WRONG_VALUE:
            detail["wrong_values"].append(row)
            if label["field"] == "ceo_name":
                detail["ceo_name_mismatches"].append(row)
        elif outcome is Outcome.MISSED:
            detail["missed"].append(row)
    return detail


def _fmt_rate(correct: int, n: int, accuracy, interval) -> str:
    return (f"{correct}/{n} = {accuracy:.3f}  "
            f"[{interval[0]:.3f}, {interval[1]:.3f}]")


def _case_lines(rows: list[dict], describe) -> list[str]:
    if not rows:
        return ["  (none)"]
    return [f"  {r['ticker']} {r['period']} {describe(r)}"
            f"{'  (ambiguous)' if r['ambiguous'] else ''}" for r in rows]


def render_report(summary: dict, detail: dict, provenance: dict) -> str:
    """The pre-registered report, plan section 5. ASCII only: the console
    this prints to is cp1252."""
    confidence = int(round(summary["confidence"] * 100))
    lines = [
        "=" * 78,
        "EXTRACTION SCORING REPORT -- pre-registered format, plan section 5",
        "=" * 78,
        f"labels sha256:  {provenance['labels_sha256']}"
        + ("  (frozen 2026-08-18: MATCH)" if provenance.get("labels_frozen")
           else "  (NOT the frozen set -- disclosed by explicit override)"),
        f"grid:           {provenance['filings']} in-window filings x "
        f"{len(EVAL_FIELDS)} fields = {provenance['expected_pairs']} instances",
    ]
    run_meta = provenance.get("run_meta")
    if run_meta:
        lines.append(
            f"predictions:    model {run_meta.get('model')}, temperature "
            f"{run_meta.get('temperature')}, prompt sha256 "
            f"{str(run_meta.get('prompt_sha256'))[:12]}..., mechanical_success="
            f"{run_meta.get('mechanical_success')}")
    lines += [
        f"intervals:      {confidence}% Wilson; per-field gate at "
        f"n >= {MIN_REPORTABLE_N}",
        "",
        f"PER-FIELD ACCURACY  (C=correct, WV=wrong_value, M=missed, "
        f"FE=false_extraction, CA=correct_abstention)",
        f"  {'field':<30} {'n':>3} {'corr':>4}  {'accuracy':>8}  "
        f"{confidence}% CI{'':<12} {'C':>3} {'WV':>3} {'M':>3} {'FE':>3} {'CA':>3}",
    ]
    for f in summary["fields"]:
        o = f["outcomes"]
        counts = (f"{o['correct']:>3} {o['wrong_value']:>3} {o['missed']:>3} "
                  f"{o['false_extraction']:>3} {o['correct_abstention']:>3}")
        if f["reportable"]:
            shown = (f"{f['accuracy']:>8.3f}  [{f['interval'][0]:.3f}, "
                     f"{f['interval'][1]:.3f}]  ")
        else:
            shown = f"{'GATED':>8}  (n < {MIN_REPORTABLE_N})    "
        lines.append(f"  {f['field']:<30} {f['n']:>3} {f['correct']:>4}  "
                     f"{shown}{counts}")
    if summary["gated_fields"]:
        lines.append(f"  gated (accuracy withheld, n < {MIN_REPORTABLE_N}): "
                     + ", ".join(summary["gated_fields"]))
    if summary["skipped"]:
        lines.append(f"  skipped unlabeled instances: {summary['skipped']}")

    pooled = summary["pooled"]
    macro = summary["macro"]
    ns = {f["n"] for f in summary["fields"]}
    lines += [
        "",
        "OVERALL",
        "  pooled (instance-weighted):      "
        + _fmt_rate(pooled["correct"], pooled["n"], pooled["accuracy"],
                    pooled["interval"]),
        f"  population-weighted (per-field mean over {macro['fields']} "
        f"fields): {macro['accuracy']:.3f}",
    ]
    if len(ns) == 1:
        lines.append("    (every field has the same n, so the two weightings "
                     "coincide by construction)")

    excl = summary["excluding_ambiguous"]
    lines += [
        "",
        f"AMBIGUOUS  ({summary['ambiguous']} instances carry the flag)",
        "  included (headline):  the pooled figure above",
        "  excluding ambiguous:  "
        + _fmt_rate(excl["correct"], excl["n"], excl["accuracy"],
                    excl["interval"]),
        "",
        "FIVE OUTCOMES, KEPT SEPARATE (totals)",
    ]
    totals = {o.value: 0 for o in Outcome}
    for f in summary["fields"]:
        for name, count in f["outcomes"].items():
            totals[name] += count
    lines.append("  " + "   ".join(f"{name}: {count}"
                                   for name, count in totals.items()))

    fe = summary["false_extraction"]
    lines += [
        "",
        "FALSE-EXTRACTION RATE",
        f"  denominator (settled 2026-08-09): {fe['denominator']} = {fe['n']}",
        "  rate: " + _fmt_rate(fe["count"], fe["n"], fe["rate"], fe["interval"]),
        "  DISCLOSED CONTAMINATION (plan section 5): before labeling began, "
        "the labeler learned",
        "  the extractor returned a non-null value on all 351 instances "
        "(abstention rate zero).",
        "  Any resulting label bias flatters the extractor on exactly this "
        "rate. Reported with",
        "  that disclosure attached; discounting this number entirely is a "
        "defensible reading.",
        "",
        "CEO_NAME MISMATCHES -- listed per plan; manual re-adjudication is "
        "not permitted",
        *_case_lines(detail["ceo_name_mismatches"],
                     lambda r: f"label {r['label_value']!r} vs predicted "
                               f"{r['predicted']!r}"),
        "",
        "FALSE EXTRACTIONS -- every case",
        *_case_lines(detail["false_extractions"],
                     lambda r: f"{r['field']}: label {r['answer_kind']}, "
                               f"predicted {r['predicted']!r}"),
        "",
        "WRONG VALUES -- every case (supplementary detail)",
        *_case_lines(detail["wrong_values"],
                     lambda r: f"{r['field']}: label {r['label_value']!r}, "
                               f"predicted {r['predicted']!r}"),
        "",
        "MISSED (model returned null on a present field) -- every case",
        *_case_lines(detail["missed"],
                     lambda r: f"{r['field']}: label {r['answer_kind']} "
                               f"{r['label_value']!r}, predicted null"),
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=pathlib.Path,
                        default=pathlib.Path("corpus/labels.jsonl"))
    parser.add_argument("--predictions", type=pathlib.Path,
                        default=pathlib.Path("corpus/predictions.jsonl"))
    parser.add_argument("--manifest", type=pathlib.Path,
                        default=pathlib.Path("corpus/manifest.json"))
    parser.add_argument("--labels-sha256", default=FROZEN_LABELS_SHA256,
                        help="expected sha256 of the labels file. Defaults to "
                             "the frozen pre-unblinding hash; passing any "
                             "other value is the disclosed way to score an "
                             "edited label set.")
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--json", type=pathlib.Path, default=None,
                        help="also write summary + detail + provenance here")
    args = parser.parse_args(argv)

    for path in (args.labels, args.predictions, args.manifest):
        if not path.exists():
            print(f"missing file: {path}")
            return 1

    actual_sha = hashlib.sha256(args.labels.read_bytes()).hexdigest()
    if actual_sha != args.labels_sha256:
        print("REFUSING to score: labels file does not match the expected "
              "sha256.")
        print(f"  expected {args.labels_sha256}")
        print(f"  actual   {actual_sha}")
        print("  If a post-freeze label edit was disclosed, rerun with "
              "--labels-sha256 <actual>.")
        return 2

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    labels = load_jsonl(args.labels)
    predictions = load_jsonl(args.predictions)

    grid = expected_grid(manifest)
    filings = len({accession for accession, _ in grid})
    print(f"grid check: expecting {len(grid)} pairs "
          f"({filings} in-window filings x {len(EVAL_FIELDS)} fields); "
          f"{len(labels)} label records, {len(predictions)} prediction records")

    gaps = grid_gaps(grid, labels, predictions)
    if gaps:
        for gap in gaps:
            print(f"  GAP: {gap}")
        print(f"grid check: FAIL -- {len(gaps)} gap(s). "
              f"REFUSING to score an incomplete grid.")
        return 1
    print("grid check: PASS -- labels and predictions each cover the grid "
          "exactly once per (accession, field)")

    ordered, values = align(labels, predictions)
    summary = summarize(ordered, values, confidence=args.confidence)
    detail = detail_rows(ordered, values)

    run_meta_path = args.predictions.parent / "predictions_run.json"
    run_meta = (json.loads(run_meta_path.read_text(encoding="utf-8"))
                if run_meta_path.exists() else None)

    provenance = {
        "labels_sha256": actual_sha,
        "labels_frozen": actual_sha == FROZEN_LABELS_SHA256,
        "expected_pairs": len(grid),
        "filings": filings,
        "run_meta": run_meta,
        "scored_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    print()
    print(render_report(summary, detail, provenance))

    if args.json:
        args.json.write_text(
            json.dumps({"provenance": provenance, "summary": summary,
                        "detail": detail}, indent=2),
            encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
