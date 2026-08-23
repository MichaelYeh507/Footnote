"""The blind adjudication queue, and the verdicts recorded against it.

PRE-REGISTERED 2026-08-21 in `EVALUATION-SPEC.md`, appendix *PHASE 4/5*.
The adjudicator judges answer *content* against the gold span, under the
published rubric, blind to everything else -- and this module is where the
blinding happens structurally rather than by convention: `blind_queue` parses
each recorded answer and keeps **only** the fields the adjudicator may see
(query, gold spans, answer text), discarding the citation, the quote, the
arms and the excerpt ids in the same expression that reads them. The server
in `scripts/adjudicate_qa.py` never handles an unstripped record, and a test
asserts the queue's shape exactly.

What needs a verdict, exactly: **every answered item on an answerable
query.** Abstentions assert nothing and need none. Malformed responses are
an instrument state, not an answer. The 15 unanswerable queries need none --
any answered outcome on them is a failure mechanically, and there is no gold
to judge content against.

**One verdict per distinct (query, normalized answer).** Identical outputs
cannot drift into different verdicts, and the normalization is
`retrieval_gold.normalize` -- the project's one text-folding rule, reused
rather than re-invented. Verdicts are append-only, last write wins before
the freeze (a misclick is corrected by appending, never by editing bytes);
after `freeze_verdicts` the file's digest is the reference every later edit
must be disclosed against, exactly as post-unblinding label edits were.

**The order is a seeded shuffle.** Sorted by key first so the shuffle is a
function of the seed and the set, not of file order; the seed is registered
here and recorded in the freeze.
"""

import datetime
import hashlib
import json
import random

from evaluation import qa_outcomes
from evaluation import retrieval_gold as gold

SHUFFLE_SEED = 20260821

VERDICTS = ("correct", "incorrect")

# A misclick is undone by appending a retraction, never by editing bytes:
# the item's latest state becomes "no verdict", it returns to the queue, and
# the click history stays in the file. The freeze and the scorer both treat
# a latest-retracted item as unjudged -- the freeze refuses to cover it and
# the scorer refuses to score it -- so a retraction cannot default to
# anything.
RETRACTED = "retracted"

# The rubric, verbatim from the appendix, shown beside every item. An
# answered item is correct iff all three hold; anything else is incorrect,
# and an item the rubric does not clearly decide is recorded ambiguous and
# scored incorrect in the headline cells -- the tie resolves against the
# pipeline.
RUBRIC = (
    ("R1 — responsive",
     "It answers what was asked: the quantity, name or fact the question "
     "requests, for the period the question names if it names one."),
    ("R2 — agrees with gold",
     "Every fact it asserts in answer to the question agrees with the gold "
     "span: numbers equal after converting the stated units and scale; "
     "names match the entity the span names; a qualifier that changes the "
     "claim (\"more than\", \"approximately\", \"at least\") is preserved "
     "or at least not contradicted."),
    ("R3 — no contradicting additions",
     "It asserts nothing additional that contradicts the gold span."),
)


def answer_key(query_id: str, answer: str) -> str:
    """The dedupe key: one verdict per distinct (query, normalized answer)."""
    digest = hashlib.sha256(
        f"{query_id}|{gold.normalize(answer)}".encode("utf-8")).hexdigest()
    return f"{query_id}:{digest[:12]}"


def blind_queue(answer_lines: list[dict], queries: list[dict]) -> list[dict]:
    """The adjudication items, stripped to what the adjudicator may see.

    Each item is exactly `{key, query_id, question, gold_spans, answer}`.
    Nothing else survives this function: not the citation, not the quote,
    not the arms, not the excerpt ids, not the raw response.
    """
    by_id = {q["query_id"]: q for q in queries}
    items: dict = {}
    for line in answer_lines:
        query = by_id.get(line["query_id"])
        if query is None:
            raise ValueError(
                f"answers name {line['query_id']!r}, which the query set "
                f"does not hold. The answers file and the query set are "
                f"from different worlds.")
        if query["stratum"] == "unanswerable":
            continue
        parsed = qa_outcomes.parse_response(line["raw"])
        if not parsed["ok"] or parsed["answer"] is None:
            continue
        answer = parsed["answer"]
        key = answer_key(query["query_id"], answer)
        items.setdefault(key, {
            "key": key,
            "query_id": query["query_id"],
            "question": query["query"],
            "gold_spans": [g["span"] for g in query["gold"]],
            "answer": answer,
        })
    ordered = sorted(items.values(), key=lambda item: item["key"])
    random.Random(SHUFFLE_SEED).shuffle(ordered)
    return ordered


def validate_verdict(record: dict, queue_keys: set) -> list[str]:
    problems = []
    if record.get("key") not in queue_keys:
        problems.append(f"key {record.get('key')!r} is not in the queue")
    if record.get("verdict") not in VERDICTS:
        problems.append(
            f"verdict must be one of {VERDICTS}, got "
            f"{record.get('verdict')!r}")
    if not isinstance(record.get("ambiguous"), bool):
        problems.append("ambiguous must be true or false, explicitly")
    return problems


def verdict_record(key: str, verdict: str, ambiguous: bool,
                   note: str = "") -> dict:
    return {
        "key": key,
        "verdict": verdict,
        "ambiguous": ambiguous,
        "note": note,
        "adjudicated_at": datetime.datetime.now().isoformat(
            timespec="seconds"),
    }


def retraction_record(key: str) -> dict:
    return {
        "key": key,
        "verdict": RETRACTED,
        "ambiguous": False,
        "note": "",
        "adjudicated_at": datetime.datetime.now().isoformat(
            timespec="seconds"),
    }


def undo_target(path) -> str | None:
    """The key an undo retracts: the most recent appended line whose key
    still stands as a real verdict.

    Walks the file from the end so repeated undo steps back through the
    session: a retracted key is skipped (it is already undone), and a key
    re-judged after a retraction is live again and undoes first.
    """
    if not path.exists():
        return None
    lines = [json.loads(line) for line in
             path.read_text(encoding="utf-8").splitlines() if line.strip()]
    latest = {}
    for record in lines:
        latest[record["key"]] = record["verdict"]
    for record in reversed(lines):
        if (record["verdict"] != RETRACTED
                and latest[record["key"]] != RETRACTED):
            return record["key"]
    return None


def read_verdicts(path) -> dict:
    """key -> the latest verdict record for it. Last write wins.

    Append-only by construction: this reader never rewrites the file, and a
    correction is a new line. The full history stays in the bytes, which is
    what makes the frozen digest meaningful.
    """
    if not path.exists():
        return {}
    verdicts = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            verdicts[record["key"]] = record
    return verdicts


def outstanding(queue: list[dict], verdicts: dict) -> list[dict]:
    """Items still needing a verdict. A latest-retracted item is unjudged:
    it comes back at its queue position, which -- since judging proceeds in
    queue order -- is the front of what remains."""
    return [item for item in queue
            if verdicts.get(item["key"], {}).get("verdict")
            in (None, RETRACTED)]


def freeze_verdicts(verdicts_path, queue: list[dict]) -> dict:
    """The freeze record, or a refusal naming what is still unjudged.

    Coverage first: a freeze over a partial adjudication would let the
    scorer emit cells whose missing verdicts default silently.
    """
    verdicts = read_verdicts(verdicts_path)
    missing = outstanding(queue, verdicts)
    if missing:
        raise RuntimeError(
            f"{len(missing)} of {len(queue)} items still need a verdict "
            f"(first: {missing[0]['key']}). The scorer refuses partial "
            f"coverage, and so does this freeze.")
    return {
        "frozen_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "file_sha256": hashlib.sha256(
            verdicts_path.read_bytes()).hexdigest(),
        "verdicts": len(verdicts),
        "queue": len(queue),
        "shuffle_seed": SHUFFLE_SEED,
        "ambiguous": sum(1 for v in verdicts.values() if v["ambiguous"]),
    }
