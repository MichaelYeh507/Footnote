"""Selection, record shape, and reporting for the extraction run.

Pure functions only -- no network, no API client, no file I/O. The CLI that
actually calls the model is scripts/run_extraction.py. Splitting them is what
makes the blindness contract testable: every byte the run prints comes from
progress_line(), so a test on progress_line() is a test on the whole console.

Why blindness matters here. The run produces model output for the same 39
filings that will be hand-labeled afterward. If that output is visible while
labeling, the labels are anchored to it, accuracy is inflated, and nothing in
the resulting numbers shows it happened. Ordering -- extract, then label
without looking -- is the entire defense, and it is only as good as the
weakest place output could leak.
"""

import hashlib
import json

# The nine measured fields, in the order they appear in the plan's matching
# spec. The prompt returns more than this (sector, description, risks,
# management); none of the rest is measured, so none of it enters predictions.
EVAL_FIELDS = (
    "company_name",
    "ticker",
    "fiscal_year_end",
    "employees",
    "total_assets",
    "revenue_most_recent_fy",
    "ceo_name",
    "dividends_declared_per_share",
    "goodwill_impairment",
)


def eval_filings(manifest: dict) -> list[dict]:
    """The filings to extract: in-window only, ordered by issuer then period.

    Over-window filings are excluded rather than truncated. Truncating would
    produce a prediction from a document the labeler read in full, and score
    the difference as a model error.

    Sorted independently of manifest order so a resumed run processes the same
    sequence, and so both fiscal years of an issuer are adjacent.
    """
    selected = [f for f in manifest["filings"] if f["fits_context_window"]]
    return sorted(selected, key=lambda f: (f["ticker"], f["period"]))


def prompt_fingerprint(prompt: str, model: str, temperature: float) -> str:
    """Identify the instrument that produced a set of predictions.

    Recorded with the run so a later reader can tell whether predictions and
    the current prompt came from the same instrument. Without it, an edited
    prompt and a stale predictions file are indistinguishable.
    """
    payload = json.dumps(
        {"prompt": prompt, "model": model, "temperature": temperature},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prediction_records(filing: dict, extracted: dict) -> list[dict]:
    """One record per measured field, in the shape labels use.

    A field the model omitted becomes an explicit null rather than a missing
    record: summarize() zips labels against predictions positionally, so a
    dropped record would shift every later pair by one and silently score the
    wrong instances against each other.
    """
    return [
        {
            "accession": filing["accession"],
            "ticker": filing["ticker"],
            "period": filing["period"],
            "field": field,
            "value": extracted.get(field),
        }
        for field in EVAL_FIELDS
    ]


def completed_accessions(lines) -> set[str]:
    """Accessions already written, for resume.

    Malformed lines raise. A truncated write -- the likely outcome of a run
    interrupted mid-filing -- would otherwise be skipped silently, and the
    resumed run would treat a partially written filing as complete.
    """
    done = set()
    for line in lines:
        if not line.strip():
            continue
        try:
            done.add(json.loads(line)["accession"])
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"malformed predictions line, refusing to resume from it: "
                f"{line[:80]!r}"
            ) from exc
    return done


def progress_line(filing: dict, status: str, extracted: dict | None = None,
                  detail: str = "") -> str:
    """One console line per filing. Carries no information about the extraction
    beyond whether the call mechanically succeeded.

    An earlier version reported "N/9 populated". Per filing that looks
    mechanical. Across a 39-filing run it is not: the aggregate is the model's
    abstention rate, which is one of the reported results and which bears
    directly on the two fields -- dividends_declared_per_share and
    goodwill_impairment -- that the plan expects to be absent from many filings.
    A labeler who knows the model always returned a number is anchored on
    exactly the value/stated_none/not_addressed call that matters most.

    So `extracted` is now accepted and deliberately ignored. It stays in the
    signature because the caller has it, and a future edit that starts using it
    should have to fail a test rather than merely be noticed in review.
    """
    del extracted  # never rendered; see docstring
    return "  ".join(part for part in (
        f"{filing['ticker']:<6}",
        f"{filing['period']}",
        f"{filing['accession']}",
        f"{status.upper():<8}",
        detail,
    ) if part)


def run_metadata(model: str, temperature: float, fingerprint: str,
                 selected: int, succeeded: int, failed: list[dict]) -> dict:
    """Run-level provenance, written next to the predictions."""
    return {
        "generated_by": "backend/scripts/run_extraction.py",
        "model": model,
        "temperature": temperature,
        "prompt_sha256": fingerprint,
        "fields": list(EVAL_FIELDS),
        "filings_selected": selected,
        "filings_succeeded": succeeded,
        "filings_failed": failed,
        "instances": succeeded * len(EVAL_FIELDS),
        "mechanical_success": not failed,
    }
