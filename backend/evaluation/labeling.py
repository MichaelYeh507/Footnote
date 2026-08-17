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
import difflib as _difflib
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
    "company_name": (
        r"exact name of registrant",
        # Corporate suffixes spelled out as well as abbreviated. The original
        # stopped at `Corp`, so `AppLovin Corporation` was never lit and the
        # labeler anchored it by hand.
        r"^\s*[A-Z][A-Za-z0-9.,&' -]+"
        r"(?:Inc|Corp|Corporation|Incorporated|Company|Companies|plc|PLC|Ltd"
        r"|Limited|Holdings?|Group|N\.?V|S\.?A|LLC|L\.?P)\.?,?\s*$"),
    "ticker": (r"trading symbol", r"title of each class"),
    "fiscal_year_end": (r"for the fiscal year ended", r"fiscal year ended"),
    "employees": (r"employe(?:es|ed)\b", r"human capital", r"full-time equivalent"),
    "total_assets": (r"total assets", r"consolidated balance sheet"),
    "revenue_most_recent_fy": (
        # `total` is optional. Three of the first five filings label the top
        # line without it -- AMCR `Net sales`, APP `Revenue`, CHTR `REVENUES`.
        r"\btotal\s+(?:net\s+)?(?:sales|revenues?|operating\s+revenues?)",
        r"total sales and revenues",
        r"^\s*(?:net\s+)?(?:sales|revenues?)\b",
        r"(?:net\s+)?(?:sales|revenues?)[\s.$]*\d",
        r"consolidated statements? of (?:\w+\s+){0,2}(?:operations|income|earnings)"),
    "ceo_name": (r"chief executive officer", r"principal executive officer"),
    # The widest set in this table, deliberately. A missed dividend becomes a
    # wrong `not_addressed`, which lands straight on the false-extraction
    # denominator -- the most fragile number in the project. A spurious hit
    # costs one keypress of `n`.
    "dividends_declared_per_share": (
        r"dividends?\s+declared\s+per",
        r"cash\s+dividends?\s+declared",
        r"dividends?\s+per\s+(?:\w+\s+){0,3}(?:share|unit)",
        # `Dividends declared ($0.4975 per share)` -- the money amount sits
        # between "declared" and "per share", so every pattern above misses
        # it. This is the form Amcor uses, and it was invisible until
        # 2026-08-17 despite being the anchor a human picked twice.
        r"dividends?\s+(?:declared|paid)[^.\n]{0,60}per\s+(?:\w+\s+){0,3}(?:share|unit)",
        # A bare per-share money amount, for the table case where the caption
        # and the figure land in different cells, so in different text nodes.
        # `par value $0.01 per share` is excluded: it is never a dividend, and
        # it appears on every cover page and throughout any merger description
        # -- 5 of 12 hits on CHTR FY2024 before this exclusion.
        r"(?<!par value )(?<!par value of )\$\s?\d+(?:\.\d+)?\s*per\s+(?:\w+\s+){0,3}(?:share|unit)",
        # The sentences that justify `stated_none` for a non-payer.
        r"(?:never|not|no)\s+(?:declared|paid)[^.\n]{0,40}dividends?",
        r"no\s+dividends?[^.\n]{0,30}(?:declared|paid)",
        r"dividends?[^.\n]{0,30}(?:have|has|were|was)\s+not\s+(?:been\s+)?(?:declared|paid)",
        r"distributions?\s+declared\s+per",
        r"dividend\s+(?:policy|yield)"),
    "goodwill_impairment": (
        r"goodwill impairment", r"impairment of goodwill",
        r"no (?:goodwill )?impairment", r"impairment charge",
        # `the Company concluded that goodwill was not impaired` -- the AMCR
        # anchor, and the standard way a filing states that none occurred.
        r"goodwill[^.\n]{0,40}(?:not|no)\s+impair",
        r"(?:not|no)\s+impair[^.\n]{0,30}goodwill"),
}


# A word that must appear in the anchor for the label to be evidence for THIS
# field. Only fields where a keyword is genuinely obligatory get a rule: a check
# that cries wolf sends the labeler back over work that was already right, which
# is the expensive failure here. `ceo_name` and `company_name` are names with no
# obligatory neighbouring word, so they have none.
#
# Written 2026-08-17 after `goodwill_impairment` was labeled `6085` from the
# anchor `Total goodwill $ 6,085` on both CTSH filings -- the carrying balance,
# which the field guidance names by name as not an impairment. Anchor-existence
# checking passed it: the text is real and in the right filing.
ANCHOR_MUST_MENTION = {
    "goodwill_impairment": ("impair",),
    "dividends_declared_per_share": ("dividend", "distribution"),
    "total_assets": ("total asset", "assets"),
    "revenue_most_recent_fy": ("revenue", "sales"),
    "employees": ("employ", "human capital", "full-time", "headcount"),
}

# Fields that SHOULD read the same in both of an issuer's fiscal years. Flagging
# them as carry-over would produce two guaranteed false alarms per issuer.
STABLE_ACROSS_YEARS = ("ticker", "company_name")


def anchor_supports_field(field: str, answer_kind: str, anchor: str) -> bool:
    """Does this anchor say anything about this field at all?

    Not a correctness check -- the value itself is the labeler's judgment and no
    script can second-guess it. This asks only whether the cited text is
    evidence for the right quantity.

    `not_addressed` is exempt: it records searched terms rather than an anchor.
    """
    if answer_kind == "not_addressed":
        return True
    required = ANCHOR_MUST_MENTION.get(field)
    if not required:
        return True
    lowered = re.sub(r"\s+", " ", anchor or "").lower()
    return any(word in lowered for word in required)


def carried_over_pairs(rows: list[dict]) -> list[tuple[str, str]]:
    """(ticker, field) where both years hold the same value AND the same anchor.

    The counterweight to `prior_hint`. Labeling an issuer's two years back to
    back is a speed decision that protocol rule 3 pairs with a named risk, and
    starting the second year on the first year's row raises that risk on
    purpose. This is what makes the risk visible afterwards.

    Both conditions are required. Values legitimately repeat -- a company can
    report identical headcount two years running. What does not repeat is the
    identical sentence with identical spacing, because the two filings are
    different documents.
    """
    seen: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if row.get("field") in STABLE_ACROSS_YEARS:
            continue
        if row.get("answer_kind") != "value":
            continue
        seen.setdefault((row.get("ticker", ""), row.get("field", "")), []).append(row)

    flagged = []
    for (ticker, field), group in seen.items():
        if len({r.get("period") for r in group}) < 2:
            continue
        values = {str(r.get("value")) for r in group}
        anchors = {(r.get("locator") or {}).get("anchor", "") for r in group}
        if len(values) == 1 and len(anchors) == 1:
            flagged.append((ticker, field))
    return sorted(flagged)


def drop_labels(rows: list[dict], ticker: str, field: str | None = None,
                period: str | None = None) -> tuple[list[dict], list[dict]]:
    """Split rows into (kept, removed) for a targeted redo.

    `undo` pops the most recent label only, which is the wrong tool once a
    defect is noticed several filings later. Returns both halves rather than
    mutating, so the caller writes a backup before anything is lost.
    """
    kept, removed = [], []
    for row in rows:
        match = (row.get("ticker") == ticker
                 and (field is None or row.get("field") == field)
                 and (period is None or row.get("period") == period))
        (removed if match else kept).append(row)
    return kept, removed


def prior_anchor_key(anchor: str | None) -> str:
    """An anchor reduced to the part that repeats across an issuer's two years.

    Digits, currency and punctuation go; the caption survives. `Total assets
    $ 16,524` and `Total assets $ 17,102` both reduce to `total assets`, which
    is why a second-year hunt is avoidable at all.

    Stripping digits is not the privacy mechanism -- for `ceo_name` the anchor
    is the answer and no amount of stripping hides it. It is here so the key
    matches across years. The value is kept from the labeler by matching
    server-side and returning integers; see `prior_hint`.
    """
    if not anchor:
        return ""
    text = re.sub(r"[\d$,%()\[\]:;.–—-]+", " ", str(anchor))
    return re.sub(r"\s+", " ", text).strip().lower()


MARK_MATCH_FLOOR = 0.82


def best_mark_index(marks: list[str], key: str,
                    contexts: list[str] | None = None) -> int | None:
    """Which of this year's candidates sits where last year's anchor sat.

    The two strings being compared are **different shapes**, which is the thing
    that makes this non-obvious. `key` comes from an anchor the labeler
    selected, so it is a phrase; a mark is only what a pattern matched, so it
    is a keyword. Measured on AMCR: anchor `the company concluded that goodwill
    was not impaired` against mark `goodwill was not impair`, and anchor `peter
    konieczny interim chief executive officer` against mark `chief executive
    officer`. Direct similarity scores both of those as misses.

    So three levels of evidence, ordered by confidence:

    1. **An exact match on the mark** scores 1.0, which nothing can outrank.
       This is the balance-sheet case, where `Total assets` is a row caption in
       both years, and it is what keeps the hint off `Total current assets` and
       `Total assets and liabilities` -- rows that merely contain the key.
       Landing on a subtotal is the specific trap the field guidance warns of.
    2. **Containment either way** at 0.90: the phrase-versus-keyword case.
    3. **The mark's surrounding context**, the only thing that can separate 13
       marks that all read `chief executive officer`. The signature-page
       occurrence is the one whose context also carries the officer's name, and
       last year's anchor carries that name too.

    Two earlier drafts special-cased exact matches, first with an early return
    and then with a restricted candidate pool. Perturbation showed both were
    unreachable in effect -- scoring 1.0 already wins -- so both were deleted
    rather than kept as code no test could distinguish.

    Returns None rather than a best guess when nothing clears the floor. A
    wrong jump is worse than no jump -- it puts the labeler in front of a
    plausible row and calls it last year's location.
    """
    if not key or not marks:
        return None
    reduced = [prior_anchor_key(m) for m in marks]
    ctx = [prior_anchor_key(c) for c in (contexts or [])]

    scored = []
    for index in range(len(marks)):
        mark = reduced[index]
        if not mark:
            continue
        if mark == key:
            mark_score = 1.0
        elif mark in key or key in mark:
            mark_score = 0.90
        else:
            mark_score = _difflib.SequenceMatcher(None, key, mark).ratio()

        around = ctx[index] if index < len(ctx) else ""
        if around:
            context_score = (0.95 if key in around
                             else _difflib.SequenceMatcher(None, key, around).ratio())
        else:
            context_score = 0.0

        # Two levels, and the second is load-bearing. Containment saturates:
        # when thirteen marks all read `chief executive officer`, every one of
        # them is contained in the anchor and scores identically, so a single
        # combined score cannot separate them and the earliest index wins by
        # accident. Ranking on the context score second breaks exactly those
        # ties, while `max` on the first level keeps a strong mark ahead of a
        # merely suggestive context.
        scored.append((max(mark_score, context_score), context_score,
                       -abs(len(mark) - len(key)), -index))
    if not scored:
        return None
    score, _ctx, _penalty, negated = max(scored)
    return -negated if score >= MARK_MATCH_FLOOR else None


def prior_hint(prior: dict | None, marks: list[str],
               contexts: list[str] | None = None) -> dict | None:
    """Where to start in this year's filing, as integers and nothing else.

    The returned dict carries exactly `index`, `of` and `period`. No anchor, no
    value, no field text. That is deliberate and it is the whole safety
    argument: a scheme that ships last year's anchor to the browser and trusts
    the template not to render it is one edit away from displaying last year's
    answer, and for `ceo_name` the anchor is the answer verbatim.
    """
    if not prior:
        return None
    key = prior_anchor_key((prior.get("locator") or {}).get("anchor"))
    index = best_mark_index(marks, key, contexts)
    if index is None:
        return None
    return {"index": index, "of": len(marks), "period": prior.get("period", "")}


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

    # Blank counts as missing, not just None. The browser sends "" for an empty
    # input, and an `is None` check let three empty labels through. An empty
    # label is worse than a rejected one: matching treats "" as null, so it
    # scores the model wrong on that instance with nothing to reveal it.
    # `0` and `0.0` are real answers and must survive this check.
    value = record.get("value")
    if kind == "value" and (value is None
                            or (isinstance(value, str) and not value.strip())):
        raise ValueError(
            f"answer_kind 'value' with no value for {record.get('field')!r}: "
            f"type the value, or use stated_none / not_addressed instead")

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
