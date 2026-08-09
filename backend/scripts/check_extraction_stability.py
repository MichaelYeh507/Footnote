"""Instrument stability check — run before any accuracy number exists.

Plan §5: `structure_text` runs at temperature=0.1, so the extractor is not
obviously deterministic. If the same filing yields different answers across
runs, then every accuracy figure carries run-to-run noise, and a model error
cannot be distinguished from a sampling artifact. That has to be known before
measuring, not after.

    python scripts/fetch_calibration_filings.py     # populates corpus/calibration
    python scripts/check_extraction_stability.py

Runs on CALIBRATION-SET filings only. Those eight were already read while
writing the prompt and are excluded from the eval corpus, so no eval filing has
its model output produced -- let alone inspected -- before labeling.

Calls the API directly rather than through structure_text() so temperature can
be varied without editing the service. The prompt and model are imported, so
what is measured is the real instrument.
"""

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from services.openai_structurer import MODEL, SYSTEM_PROMPT, client  # noqa: E402

EVAL_FIELDS = (
    "company_name", "ticker", "fiscal_year_end", "employees", "total_assets",
    "revenue_most_recent_fy", "ceo_name", "dividends_declared_per_share",
    "goodwill_impairment",
)

FILINGS = ("costco", "apple")   # costco first: more ambiguous fields
TEMPERATURES = (0.1, 0.0)       # current setting, then the proposed one
RUNS = 3


def extract(text: str, temperature: float) -> dict:
    response = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": f"Extract structured data from this document:\n\n{text}"},
        ],
        temperature=temperature,
    )
    return json.loads(response.choices[0].message.content)


def canonical(value) -> str:
    """Render a field value so identical answers compare equal."""
    if value is None:
        return "null"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path("corpus/stability.json"))
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=pathlib.Path("corpus/calibration"),
                        help="directory of <slug>.txt extracted calibration "
                             "filings; gitignored, populated by "
                             "fetch_calibration_filings.py")
    args = parser.parse_args()

    report = {"model": MODEL, "runs_per_setting": args.runs, "results": []}
    unstable_any = False
    missing = []

    for slug in FILINGS:
        path = args.filings_dir / f"{slug}.txt"
        if not path.exists():
            missing.append(path)
            print(f"  !! no cached text for {slug} at {path}", file=sys.stderr)
            continue
        text = path.read_text(encoding="utf-8")

        for temperature in TEMPERATURES:
            runs = []
            for i in range(args.runs):
                try:
                    runs.append(extract(text, temperature))
                except Exception as exc:  # noqa: BLE001
                    print(f"  !! {slug} t={temperature} run {i+1}: "
                          f"{type(exc).__name__}: {exc}", file=sys.stderr)
            if len(runs) < 2:
                continue

            disagreements = {}
            for field in EVAL_FIELDS:
                values = [canonical(r.get(field)) for r in runs]
                if len(set(values)) > 1:
                    disagreements[field] = dict(Counter(values))

            stable = not disagreements
            unstable_any = unstable_any or not stable
            report["results"].append({
                "filing": slug,
                "temperature": temperature,
                "runs": len(runs),
                "stable": stable,
                "fields_disagreeing": len(disagreements),
                "disagreements": disagreements,
            })

            status = "STABLE" if stable else f"UNSTABLE ({len(disagreements)}/9 fields)"
            print(f"  {slug:<8} temperature={temperature:<4} "
                  f"{len(runs)} runs -> {status}", file=sys.stderr)
            for field, counts in disagreements.items():
                rendered = "  |  ".join(f"{v!r} x{n}" for v, n in counts.items())
                print(f"      {field:<30}{rendered}"[:150], file=sys.stderr)

    # A run that measured nothing must not overwrite a real report with an empty
    # one, and must not print a clean verdict. "no disagreement across runs" and
    # "no runs happened" render identically downstream, and the second is what a
    # stale filings path produces.
    if not report["results"]:
        print(f"\nMEASURED NOTHING: {len(missing)}/{len(FILINGS)} calibration "
              f"filings absent from {args.filings_dir}. Populate it with "
              f"scripts/fetch_calibration_filings.py. {args.out} left unchanged.",
              file=sys.stderr)
        return 1

    report["any_instability"] = unstable_any
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(f"\nwrote {args.out}", file=sys.stderr)
    print("\nVERDICT: " + (
        "instability observed -- run-to-run noise must be characterized and "
        "carried through every interval"
        if unstable_any else
        "no disagreement across runs at either temperature"), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
