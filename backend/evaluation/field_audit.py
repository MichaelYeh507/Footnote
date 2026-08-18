"""Cross-check a hand label against the facts the filer tagged themselves.

`verify_labels.py` checks that a record is well-formed and that its anchor
occurs in its own filing. Neither can tell that a value is the **wrong year's
figure**, which is the defect that got through twice on DGX: `3.20` in the
FY2024 filing and `3.44` in FY2025 are both real tagged numbers, both anchored
in real text, and both belong to the *following* fiscal year.

Inline XBRL carries a period and dimensions on every fact, so that class of
error is mechanically detectable. This module does the comparison and
classifies the outcome.

**It never proposes a value.** A verdict is a prompt to look, not a correction,
and three of the codes exist precisely because a mismatch is often the tagging
being unusual rather than the label being wrong. `UNVERIFIED` is the common
case, not a failure: filers tag inconsistently, and a figure stated only in
prose is untagged.

Scale convention: monetary labels are stored in MILLIONS (see the scale
declaration in the labeling app), while XBRL reports actual currency units. The
comparison multiplies the label by 1e6 for those fields and nothing else -- a
per-share amount, a headcount and a date are all compared as reported.
"""

import re

# Concepts a field may legitimately be tagged as, in preference order, plus how
# the fiscal-year fact is recognised. `ceo_name` has no XBRL concept at all: US
# GAAP does not tag officer names, so it is unverifiable here by construction
# and says so rather than guessing at something adjacent.
FIELD_CONCEPTS = {
    "company_name": {
        "concepts": ("dei:EntityRegistrantName",),
        "kind": "text",
    },
    "ticker": {
        "concepts": ("dei:TradingSymbol",),
        "kind": "text",
    },
    "fiscal_year_end": {
        "concepts": ("dei:DocumentPeriodEndDate",),
        "kind": "date",
    },
    "employees": {
        "concepts": ("dei:EntityNumberOfEmployees",),
        "kind": "count",
    },
    "total_assets": {
        "concepts": ("us-gaap:Assets",),
        "kind": "money", "period": "instant",
    },
    "revenue_most_recent_fy": {
        "concepts": ("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                     "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
                     "us-gaap:Revenues",
                     "us-gaap:RevenuesNetOfInterestExpense"),
        "kind": "money", "period": "duration",
    },
    "ceo_name": {
        "concepts": (),
        "kind": "text",
    },
    "dividends_declared_per_share": {
        "concepts": ("us-gaap:CommonStockDividendsPerShareDeclared",
                     "us-gaap:CommonStockDividendsPerShareCashPaid"),
        "kind": "pershare", "period": "duration", "sum_quarters": True,
    },
    "goodwill_impairment": {
        "concepts": ("us-gaap:GoodwillImpairmentLoss",),
        "kind": "money", "period": "duration",
    },
}

MONEY_FIELDS = {"total_assets", "revenue_most_recent_fy", "goodwill_impairment"}
TOLERANCE = 0.001          # the matching spec's 0.1%, reused deliberately


# Shown to the labeler in the app after a label is saved. Deliberately free of
# any figure: the verdict says "go back and re-read", never "the answer is X".
#
# The distinction is the whole reason this can live in the labeling app at all.
# A banner carrying the tagged value would turn every instance into "type
# something, let the app correct it", and the labels would become XBRL-derived
# rather than read -- which is the LLM-assisted labeling §5 rejects, arriving
# through a side door. A banner carrying only the verdict sends the labeler back
# to the filing, where the answer still has to come from reading it.
AUDIT_HINTS = {
    "PERIOD": "that figure is tagged to a different period in this filing",
    "DIMS": "that figure is tagged as a segment, subsidiary or scenario "
            "rather than the consolidated total",
    "DIFFERS": "the filing tags a different figure for this fiscal year",
    "ABSENT-BUT-TAGGED": "the filing tags a value for this fiscal year",
}


def audit_hint(code: str) -> str | None:
    """A value-free prompt to look again, or None when nothing is wrong."""
    return AUDIT_HINTS.get(code)


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(re.sub(r"[,$\s]", "", str(value)))
    except (TypeError, ValueError):
        return None


def close(a: float, b: float, tolerance: float = TOLERANCE) -> bool:
    if a == b:
        return True
    scale = max(abs(a), abs(b))
    return scale > 0 and abs(a - b) / scale <= tolerance


def is_fiscal_year(fact: dict, period_end: str, wants: str) -> bool:
    """Does this fact cover exactly the fiscal year the filing reports?

    A duration must END on the period end and run roughly a year -- that rules
    out both quarterly facts and the next year's annualised scenario, which
    starts after the period end. An instant must fall ON the period end.
    """
    from evaluation.xbrl import duration_days

    if wants == "instant":
        return fact.get("instant") == period_end
    if fact.get("instant"):
        return False
    if fact.get("end") != period_end:
        return False
    days = duration_days(fact)
    return days is not None and 350 <= days <= 380


def is_quarter_within(fact: dict, period_end: str) -> bool:
    """A quarterly duration ending inside the fiscal year that ends period_end."""
    from evaluation.xbrl import duration_days

    days = duration_days(fact)
    if days is None or not 80 <= days <= 100:
        return False
    end = fact.get("end") or ""
    return bool(end) and end <= period_end and end > _year_before(period_end)


def _year_before(date: str) -> str:
    try:
        year, rest = date.split("-", 1)
        return f"{int(year) - 1}-{rest}"
    except (ValueError, AttributeError):
        return ""


def undimensioned(facts: list[dict]) -> list[dict]:
    """Facts with no axis: the consolidated figure for the primary registrant.

    Load-bearing for combined filings. DOW tags `us-gaap:Assets` for Dow Inc.
    and again for The Dow Chemical Company at the same instant, and only the
    undimensioned one belongs to the issuer the corpus selected.
    """
    return [f for f in facts if not f.get("dims")]


def _plain(text) -> str:
    """Tagged text as a reader sees it.

    Fact text is lifted straight out of HTML, so it still carries entities --
    `Domino&#8217;s Pizza, Inc.` for `Domino's Pizza, Inc.`. Comparing without
    unescaping reports a difference that exists only in the markup.
    """
    import html

    return re.sub(r"\s+", " ", html.unescape(str(text or ""))).strip()


def _agrees(field: str, label_value, tagged) -> bool:
    """Compare using the project's own pre-registered matching rules.

    Deliberately not a second comparison of my own. The spec already decides
    that `DEVON ENERGY CORP/DE` and `Devon Energy Corporation` are the same
    registrant -- it strips corporate suffixes and the EDGAR `/DE/` qualifier --
    and an audit that disagreed with the matcher would report differences that
    will never affect a score, which is the fastest way to make a review tool
    ignored.
    """
    from evaluation.matching import matches

    candidates = [tagged]
    if field == "company_name":
        # `dei:EntityRegistrantName` is EDGAR's *conformed* name, which is not
        # the cover-page name the field guidance asks for: Devon files as
        # `DEVON ENERGY CORP/DE` and its cover page reads `Devon Energy
        # Corporation`. The matcher strips `/DE/` between slashes; the tagged
        # form has no trailing slash, so it survives. Stripped here rather than
        # in matching.py, which is pre-registered and must not move to make an
        # audit quieter.
        candidates.append(re.sub(r"/[A-Za-z]{2,4}/?\s*$", "", str(tagged)).strip())

    for candidate in candidates:
        try:
            if matches(field, label_value, candidate):
                return True
        except Exception:                                # noqa: BLE001
            continue
    return False


def audit_verdict(label: dict | None, facts: list[dict], period_end: str,
                  field: str = "dividends_declared_per_share") -> tuple[str, str]:
    """Classify one (label, filing, field). Returns (code, detail).

    Codes, and what each is asking the reviewer to do:

    ``OK``                 matches the undimensioned fiscal-year fact.
    ``OK-SUM``             matches four times a stated quarterly rate --
                           RESOLUTION 1's case.
    ``PERIOD``             matches a fact from a different period. Look: this
                           is the DGX defect.
    ``DIMS``               matches a *dimensioned* fact -- a segment, a
                           subsidiary, or a forward scenario -- rather than the
                           consolidated one.
    ``DIFFERS``            a fiscal-year fact exists and the label matches none.
    ``ABSENT-BUT-TAGGED``  labeled absent while the filing tags a value. The
                           worst case: it moves a real value into the
                           false-extraction denominator.
    ``ABSENT-OK``          labeled absent, nothing tagged. Consistent.
    ``UNVERIFIED``         nothing tagged for the year. Common and not a fault.
    ``NO-CONCEPT``         the field has no XBRL concept (ceo_name).
    ``MISSING``            not labeled yet.
    """
    spec = FIELD_CONCEPTS.get(field, {})
    if label is None:
        return "MISSING", ""
    if not spec.get("concepts"):
        return "NO-CONCEPT", "no XBRL concept exists for this field"

    wants = spec.get("period", "duration")
    kind = spec.get("kind", "money")
    year_facts = undimensioned([f for f in facts if is_fiscal_year(f, period_end, wants)])
    other_facts = [f for f in facts if f not in year_facts]

    answer_kind = label.get("answer_kind")
    if answer_kind in ("stated_none", "not_addressed"):
        live = [f for f in year_facts if (f.get("value") or 0) != 0]
        if live:
            return ("ABSENT-BUT-TAGGED",
                    f"filing tags {live[0]['text']} for the fiscal year")
        return "ABSENT-OK", ""

    # ---- the fiscal year end is the manifest's own period; the displayed text
    # varies ("December 31" with the year elsewhere) and is not the authority.
    if kind == "date":
        if _agrees(field, label.get("value"), period_end):
            return "OK", ""
        return "DIFFERS", f"the filing reports the year ended {period_end}"

    if kind == "text":
        pool = year_facts or undimensioned(facts) or facts
        for fact in pool:
            if _agrees(field, label.get("value"), _plain(fact["text"])):
                return "OK", ""
        candidates = [_plain(f["text"]) for f in pool][:3]
        if not candidates:
            return "UNVERIFIED", "no tagged value for the fiscal year"
        dimmed = [f for f in facts if f.get("dims")
                  and _agrees(field, label.get("value"), _plain(f["text"]))]
        if dimmed:
            return "DIMS", (f"matches a fact qualified by "
                            f"{', '.join(dimmed[0]['dims'])}")
        return "DIFFERS", f"filing tags {', '.join(repr(c) for c in candidates)}"

    # ---- numeric fields
    value = _as_float(label.get("value"))
    if value is None:
        return "DIFFERS", f"label value {label.get('value')!r} is not a number"
    if field in MONEY_FIELDS:
        value *= 1_000_000

    for fact in year_facts:
        if fact["value"] is not None and close(value, fact["value"]):
            return "OK", ""

    if spec.get("sum_quarters"):
        for fact in facts:
            if (fact.get("value") and not fact.get("dims")
                    and is_quarter_within(fact, period_end)
                    and close(value, fact["value"] * 4)):
                return "OK-SUM", f"4 x {fact['text']} stated for a quarter"

    from evaluation.xbrl import period_label

    for fact in other_facts:
        if fact["value"] is not None and close(value, fact["value"]):
            axis = f", {', '.join(fact['dims'])}" if fact.get("dims") else ""
            if fact in undimensioned(other_facts) and not fact.get("dims"):
                return "PERIOD", f"that figure is tagged {period_label(fact)}"
            return "DIMS", f"that figure is tagged {period_label(fact)}{axis}"

    if year_facts:
        shown = ", ".join(f["text"] for f in year_facts[:2])
        return "DIFFERS", f"filing tags {shown} for the fiscal year"
    return "UNVERIFIED", "nothing tagged for the fiscal year"
