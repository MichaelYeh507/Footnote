"""Outcome classification and aggregation into the reported numbers.

Five outcomes, not two. Most extraction evals collapse "returned nothing" and
"returned a confident wrong number" into a single error bucket, which hides the
difference that matters most: a null is cheap, an invented figure is expensive.
Keeping them apart is also the only way the false-extraction rate is computable.

Grid pre-registered in HYBRID-RETRIEVAL-SEC-PLAN.md §5.
"""

from collections import Counter
from enum import Enum

from evaluation.matching import RULES, _is_nullish, matches
from evaluation.wilson import wilson_interval

# §3: gate any per-field claim below this. Below it the interval is so wide the
# number carries no information, and publishing it anyway is the failure mode
# this project exists to avoid.
MIN_REPORTABLE_N = 25

ABSENT_KINDS = ("stated_none", "not_addressed")
VALID_ANSWER_KINDS = ("value", *ABSENT_KINDS)


class Outcome(str, Enum):
    CORRECT = "correct"
    WRONG_VALUE = "wrong_value"
    MISSED = "missed"
    FALSE_EXTRACTION = "false_extraction"
    CORRECT_ABSTENTION = "correct_abstention"


CORRECT_OUTCOMES = (Outcome.CORRECT, Outcome.CORRECT_ABSTENTION)


def classify(label: dict, prediction) -> Outcome:
    """Place one (label, prediction) pair on the outcome grid."""
    if label.get("status") != "labeled":
        raise ValueError(
            f"refusing to score a {label.get('status')!r} instance for "
            f"{label.get('field')!r}: unlabeled is not the same as absent"
        )

    kind = label.get("answer_kind")
    if kind not in VALID_ANSWER_KINDS:
        raise ValueError(f"unknown answer_kind {kind!r}, expected one of "
                         f"{VALID_ANSWER_KINDS}")

    field = label["field"]
    if field not in RULES:
        raise KeyError(field)

    prediction_is_null = _is_nullish(prediction)

    if kind == "not_addressed":
        # Zero is an assertion that the filing stated zero. It stated nothing,
        # so zero is an invented value, not an abstention.
        return Outcome.CORRECT_ABSTENTION if prediction_is_null else Outcome.FALSE_EXTRACTION

    if kind == "stated_none":
        if prediction_is_null:
            return Outcome.MISSED
        return Outcome.CORRECT if matches(field, 0.0, prediction) else Outcome.FALSE_EXTRACTION

    if prediction_is_null:
        return Outcome.MISSED
    return Outcome.CORRECT if matches(field, label["value"], prediction) else Outcome.WRONG_VALUE


def _rate(correct: int, n: int, confidence: float) -> dict:
    return {
        "n": n,
        "correct": correct,
        "accuracy": correct / n if n else None,
        "interval": list(wilson_interval(correct, n, confidence)) if n else None,
    }


def summarize(labels: list[dict], predictions: list, confidence: float = 0.95) -> dict:
    """Score every labeled instance and aggregate.

    Reports pooled (micro) and macro averages side by side. §5 calls the gap
    between them the finding rather than a footnote: pooled weights every
    instance equally, macro weights every field equally, and they diverge
    exactly when field counts differ -- which happens as soon as instances are
    skipped unevenly.
    """
    if len(labels) != len(predictions):
        raise ValueError(
            f"{len(labels)} labels against {len(predictions)} predictions"
        )

    scored, skipped = [], 0
    for label, prediction in zip(labels, predictions):
        if label.get("status") != "labeled":
            skipped += 1
            continue
        scored.append((label, prediction, classify(label, prediction)))

    per_field: dict[str, dict] = {}
    for label, _prediction, outcome in scored:
        entry = per_field.setdefault(
            label["field"],
            {"field": label["field"], "n": 0, "correct": 0,
             "outcomes": Counter({o.value: 0 for o in Outcome})},
        )
        entry["n"] += 1
        entry["outcomes"][outcome.value] += 1
        if outcome in CORRECT_OUTCOMES:
            entry["correct"] += 1

    fields = []
    for entry in per_field.values():
        accuracy = entry["correct"] / entry["n"]
        fields.append({
            "field": entry["field"],
            "n": entry["n"],
            "correct": entry["correct"],
            "accuracy": accuracy,
            "interval": list(wilson_interval(entry["correct"], entry["n"], confidence)),
            "outcomes": dict(entry["outcomes"]),
            "reportable": entry["n"] >= MIN_REPORTABLE_N,
        })
    fields.sort(key=lambda f: f["field"])

    total_correct = sum(f["correct"] for f in fields)
    total_n = sum(f["n"] for f in fields)

    absent = [t for t in scored if t[0]["answer_kind"] in ABSENT_KINDS]
    false_extractions = sum(1 for *_, o in absent if o is Outcome.FALSE_EXTRACTION)

    unambiguous = [t for t in scored if not t[0].get("ambiguous")]
    unambiguous_correct = sum(1 for *_, o in unambiguous if o in CORRECT_OUTCOMES)

    return {
        "confidence": confidence,
        "skipped": skipped,
        "ambiguous": len(scored) - len(unambiguous),
        "fields": fields,
        "pooled": _rate(total_correct, total_n, confidence),
        "macro": {
            "accuracy": (sum(f["accuracy"] for f in fields) / len(fields))
                        if fields else None,
            "fields": len(fields),
        },
        "excluding_ambiguous": _rate(unambiguous_correct, len(unambiguous), confidence),
        "false_extraction": {
            "n": len(absent),
            "count": false_extractions,
            "rate": false_extractions / len(absent) if absent else None,
            "interval": list(wilson_interval(false_extractions, len(absent), confidence))
                        if absent else None,
            "denominator": "stated_none + not_addressed instances",
        },
        "gated_fields": [f["field"] for f in fields if not f["reportable"]],
    }
