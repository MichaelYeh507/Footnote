"""Queue, record shape, and validation for hand-labeling the eval corpus.

Protocol pre-registered in HYBRID-RETRIEVAL-SEC-PLAN.md §5, before this file
existed and before the first label.

This module deliberately imports nothing that can reach model output. It does
not import the extraction run, the OpenAI client, or anything that knows where
predictions live, and a test fails if the word appears in this file or if a
labeling session opens that path. The isolation is the point: the labels are
the ground truth the extractor is measured against, and a labeler who has seen
the model's answers produces something that looks like ground truth, scores
better, and cannot be distinguished from the real thing afterward.

Pure functions only. The interactive tool is scripts/label_filings.py.
"""

import datetime as _datetime
import json
import re

SCHEMA_VERSION = 1

# Pre-registered. `stated_none` compares as 0, `not_addressed` as null. The
# distinction is what makes the false-extraction rate computable at all: a
# filing that says "no impairment was recorded" and a filing that never
# mentions impairment are different facts, and a model that answers 0 to the
# second one has invented a number.
ANSWER_KINDS = ("value", "stated_none", "not_addressed")

SNIPPET_RADIUS = 320
DEFAULT_CANDIDATE_LIMIT = 8

# Ordered as in the plan's matching spec, so the queue walks a filing the way
# the spec reads.
QUEUE_FIELDS = (
    "company_name", "ticker", "fiscal_year_end", "employees", "total_assets",
    "revenue_most_recent_fy", "ceo_name", "dividends_declared_per_share",
    "goodwill_impairment",
)

# Where each field tends to be stated. These only order the labeler's reading;
# they never decide a label, and a field with no hit is not thereby absent --
# it is a prompt to search by hand. Deliberately broad: a narrow pattern that
# silently misses the real passage would push a labeler toward not_addressed,
# which is the one label that cannot be checked from the record.
FIELD_PATTERNS = {
    "company_name": (r"exact name of registrant", r"^\s*[A-Z][A-Za-z0-9.,&' -]+(Inc|Corp|Company|plc|Ltd)\.?\s*$"),
    "ticker": (r"trading symbol", r"title of each class"),
    "fiscal_year_end": (r"for the fiscal year ended", r"fiscal year ended"),
    "employees": (r"employe(?:es|ed)\b", r"human capital", r"full-time equivalent"),
    "total_assets": (r"total assets", r"consolidated balance sheet"),
    "revenue_most_recent_fy": (
        r"total net sales", r"total revenues?", r"total sales and revenues",
        r"total net revenues?", r"total operating revenues?",
        r"consolidated statements? of (?:\w+\s+){0,2}(?:operations|income|earnings)"),
    "ceo_name": (r"chief executive officer", r"principal executive officer"),
    "dividends_declared_per_share": (
        r"dividends? declared per", r"dividends? per (?:common )?share",
        r"cash dividends? declared"),
    "goodwill_impairment": (
        r"goodwill impairment", r"impairment of goodwill",
        r"no (?:goodwill )?impairment", r"impairment charge"),
}


def build_queue(manifest: dict) -> list[dict]:
    """Every (filing, field) to label, in labeling order.

    Ordered by (ticker, period) then matching-spec field order, so both fiscal
    years of an issuer are consecutive. That ordering is a speed decision and
    it creates a real carry-over risk between an issuer's two years; the
    mandatory locator is what answers it, by forcing the second year's value to
    be anchored in the second year's document.

    Over-window filings are excluded: there is no prediction to compare a label
    against until a chunker exists, so labeling them now would be work that
    scores nothing.
    """
    filings = [f for f in manifest["filings"] if f["fits_context_window"]]
    filings.sort(key=lambda f: (f["ticker"], f["period"]))
    return [
        {"accession": f["accession"], "ticker": f["ticker"],
         "period": f["period"], "field": field}
        for f in filings
        for field in QUEUE_FIELDS
    ]


def completed_keys(lines) -> set[tuple[str, str]]:
    """(accession, field) pairs already labeled, for resume.

    Per field rather than per filing: labeling nine fields takes long enough
    that interruptions land mid-filing far more often than between filings.
    A malformed line raises instead of being skipped -- the likely cause is a
    write interrupted partway, and treating that as "done" would drop the
    instance from the run with no denominator change to reveal it.
    """
    done = set()
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            done.add((record["accession"], record["field"]))
        except (json.JSONDecodeError, KeyError) as exc:
            raise ValueError(
                f"malformed label line, refusing to resume from it: {line[:80]!r}"
            ) from exc
    return done


def candidate_passages(text: str, field: str, limit: int = DEFAULT_CANDIDATE_LIMIT
                       ) -> list[dict]:
    """Passages worth reading for `field`, in document order.

    A reading aid, never a decision. Returns [] when nothing matches, which is
    a prompt to search by hand rather than evidence the field is absent -- see
    the note on FIELD_PATTERNS.
    """
    hits: list[dict] = []
    seen: set[int] = set()
    for pattern in FIELD_PATTERNS.get(field, ()):
        for match in re.finditer(pattern, text, re.I | re.M):
            start = match.start()
            if any(abs(start - previous) < SNIPPET_RADIUS for previous in seen):
                continue
            seen.add(start)
            hits.append({
                "offset": start,
                "matched": match.group(0)[:80],
                "snippet": text[max(0, start - SNIPPET_RADIUS // 4):
                                start + SNIPPET_RADIUS],
            })
    hits.sort(key=lambda h: h["offset"])
    return hits[:limit]


def label_record(item: dict, answer_kind: str, value=None, locator: dict | None = None,
                 ambiguous: bool = False, note: str = "",
                 status: str = "labeled") -> dict:
    """Build one label in the pre-registered shape.

    `value` is forced to None for the two absent kinds. Carrying both an
    answer_kind of stated_none and a number would give the record two sources
    of truth, and scoring reads answer_kind.
    """
    return {
        "accession": item["accession"],
        "ticker": item["ticker"],
        "period": item["period"],
        "field": item["field"],
        "status": status,
        "answer_kind": answer_kind,
        "value": value if answer_kind == "value" else None,
        "locator": locator or {},
        "ambiguous": bool(ambiguous),
        "note": note,
        "labeled_at": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
    }


def validate_label(record: dict) -> dict:
    """Enforce the pre-registered protocol. Raises ValueError on violation.

    Called before a record is written, so a label that fails these rules is
    never persisted rather than being cleaned up later.
    """
    kind = record.get("answer_kind")
    if kind not in ANSWER_KINDS:
        raise ValueError(f"unknown answer_kind {kind!r}, expected one of {ANSWER_KINDS}")

    locator = record.get("locator") or {}

    if kind == "value" and record.get("value") is None:
        raise ValueError(
            f"answer_kind 'value' with no value for {record.get('field')!r}: "
            f"use stated_none or not_addressed instead")

    if kind in ("value", "stated_none"):
        # Rule 1. Both assert something the filing says, so both must point at it.
        if not str(locator.get("anchor", "")).strip():
            raise ValueError(
                f"{kind!r} label for {record.get('field')!r} needs a locator "
                f"anchor -- a label that cannot be pointed at is not checkable")

    if kind == "not_addressed":
        # Rule 2. The only label asserting a negative.
        if not [t for t in locator.get("searched", []) if str(t).strip()]:
            raise ValueError(
                f"'not_addressed' for {record.get('field')!r} needs "
                f"locator.searched -- the terms tried, so the negative is the "
                f"result of looking and a reader can re-run them")

    return record
