"""Run extraction over the eval corpus and write predictions.

    python scripts/run_extraction.py

Reads corpus/manifest.json, extracts the 39 in-window filings, and appends one
record per (filing, field) to corpus/predictions.jsonl -- gitignored, like the
filings themselves.

THIS OUTPUT MUST NOT BE READ BEFORE LABELING IS COMPLETE. The same 39 filings
get hand-labeled next, and a labeler who has seen the model's answers is no
longer producing independent ground truth. The console prints only mechanical
status (see evaluation/extraction_run.progress_line, which is tested to be
incapable of printing a value), but the predictions file itself is plain text:
do not open it.

Resumable at filing granularity. Interrupt it and run it again; filings already
present are skipped. A filing whose extraction fails is recorded in the run
metadata and left absent from predictions, so a partial run is visibly partial
rather than quietly short.

Verify mechanical success only -- 39 succeeded, 351 instances written. Do not
inspect values.
"""

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from evaluation.extraction_run import (  # noqa: E402
    EVAL_FIELDS, completed_accessions, eval_filings, prediction_records,
    progress_line, prompt_fingerprint, run_metadata,
)
from services.html_parser import extract_text_from_html  # noqa: E402
from services.openai_structurer import (  # noqa: E402
    MODEL, SYSTEM_PROMPT, TEMPERATURE, structure_text,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=pathlib.Path,
                        default=pathlib.Path("corpus/manifest.json"))
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=pathlib.Path("corpus/filings"))
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path("corpus/predictions.jsonl"))
    parser.add_argument("--meta", type=pathlib.Path,
                        default=pathlib.Path("corpus/predictions_run.json"))
    parser.add_argument("--limit", type=int, default=None,
                        help="extract at most N filings; for a smoke run")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    selected = eval_filings(manifest)

    done = set()
    if args.out.exists():
        done = completed_accessions(args.out.read_text(encoding="utf-8").splitlines())

    pending = [f for f in selected if f["accession"] not in done]
    if args.limit is not None:
        pending = pending[:args.limit]

    fingerprint = prompt_fingerprint(SYSTEM_PROMPT, MODEL, TEMPERATURE)
    print(f"{len(selected)} filings selected, {len(done)} already done, "
          f"{len(pending)} to extract", file=sys.stderr)
    print(f"model {MODEL}  temperature {TEMPERATURE}  "
          f"prompt {fingerprint[:12]}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    failed = []
    succeeded = 0

    for filing in pending:
        document = args.filings_dir / f"{filing['ticker']}_{filing['period']}.htm"
        try:
            if not document.exists():
                raise FileNotFoundError(
                    f"{document} absent; re-fetch with scripts/fetch_filings.py")
            text = extract_text_from_html(document.read_bytes())
            extracted = structure_text(text)
            if not isinstance(extracted, dict):
                raise TypeError(f"model returned {type(extracted).__name__}, not an object")
        except Exception as exc:  # noqa: BLE001 -- one filing must not end the run
            failed.append({
                "ticker": filing["ticker"], "period": filing["period"],
                "accession": filing["accession"],
                "error": f"{type(exc).__name__}: {exc}"[:300],
            })
            print(progress_line(filing, "failed", detail=type(exc).__name__),
                  file=sys.stderr)
            continue

        records = prediction_records(filing, extracted)
        with args.out.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
        succeeded += 1
        print(progress_line(filing, "ok", extracted=extracted), file=sys.stderr)

    total_done = len(done) + succeeded
    metadata = run_metadata(MODEL, TEMPERATURE, fingerprint,
                            len(selected), total_done, failed)
    args.meta.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")

    print(f"\n{total_done}/{len(selected)} filings extracted, "
          f"{total_done * len(EVAL_FIELDS)} instances written to {args.out}",
          file=sys.stderr)
    if failed:
        print(f"\n{len(failed)} FAILED -- predictions are incomplete. Re-run to "
              f"retry; succeeded filings are skipped.", file=sys.stderr)
        for row in failed:
            print(f"    {row['ticker']} {row['period']}  {row['error']}",
                  file=sys.stderr)
        return 1

    print("mechanical success on every selected filing", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
