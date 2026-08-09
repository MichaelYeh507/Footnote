"""Field-by-field comparison of a label against a prediction.

This module *is* the accuracy number. Every rule here was pre-registered in
HYBRID-RETRIEVAL-SEC-PLAN.md §5 before any label was recorded and before any
filing was extracted, because matcher tuning is invisible: loosening the name
rule or widening the tolerance moves the result with no trace a reader could
find.

Do not change a rule here to make a number look better. Changing one after
results exist invalidates the result.
"""

import re

from dateutil import parser as date_parser

# Relative tolerance for monetary comparison. Exists for unit conversion: a
# filing reporting thousands does not convert to millions on an exact float.
RELATIVE_TOLERANCE = 0.001

# Stripped from company names before comparison. Deliberately excludes words
# like "holdings" and "group", which are frequently part of the actual name
# rather than a legal-form suffix.
_COMPANY_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "plc",
    "ltd", "limited", "llc", "lp", "sa", "nv", "ag", "the",
}

_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}

# EDGAR appends a state or disambiguator to conformed names: PROGRESSIVE CORP/OH/
_EDGAR_QUALIFIER = re.compile(r"/[a-z]{2,4}/")


def _is_nullish(value) -> bool:
    """Models return '' about as often as null. Treating them differently would
    split the abstention counts across two buckets for no reason."""
    return value is None or (isinstance(value, str) and not value.strip())


def _normalize_company(value) -> str:
    text = _EDGAR_QUALIFIER.sub(" ", str(value).lower())
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return " ".join(t for t in text.split() if t not in _COMPANY_SUFFIXES)


def _match_company(label, prediction) -> bool:
    return _normalize_company(label) == _normalize_company(prediction)


def _match_ticker(label, prediction) -> bool:
    return str(label).strip().lower() == str(prediction).strip().lower()


def _match_date(label, prediction) -> bool:
    try:
        return (date_parser.parse(str(label)).date()
                == date_parser.parse(str(prediction)).date())
    except (ValueError, OverflowError, TypeError):
        return False


def _leading_integer(value):
    match = re.search(r"\d[\d,]*", str(value))
    return int(match.group(0).replace(",", "")) if match else None


def _match_employees(label, prediction) -> bool:
    """Compared as an integer, discarding qualifiers like 'approximately' and
    'full-time equivalent'. Note this correctly fails a units error: a filing
    stating 57.9 under a '(thousands)' header parses to 57, not 57,900."""
    a, b = _leading_integer(label), _leading_integer(prediction)
    return a is not None and a == b


def _to_float(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[,$\s]", "", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _match_number(label, prediction) -> bool:
    a, b = _to_float(label), _to_float(prediction)
    if a is None or b is None:
        return False
    if a == 0.0 or b == 0.0:
        # Relative tolerance is undefined against zero, and zero is a
        # meaningful answer here ("the filing stated none"), not a near-miss.
        return a == b
    return abs(a - b) <= RELATIVE_TOLERANCE * max(abs(a), abs(b))


def _name_tokens(value) -> list[str]:
    text = re.sub(r"[^a-z ]", " ", str(value).lower())
    return [t for t in text.split() if t and t not in _NAME_SUFFIXES]


def _match_person(label, prediction) -> bool:
    """Surname must match, and the two must share at least one given-name
    initial.

    The shared-initial form rather than strict first-initial is deliberate:
    signature pages routinely render a middle-name-preferred officer as
    "W. Rodney McMullen" while the body of the filing says "Rodney McMullen".
    Requiring the *first* initial would score that substantively-correct answer
    as wrong.

    Known limitation, accepted and documented: "Bob"/"Robert" fails, and two
    officers sharing a surname and an initial would pass. Name-field mismatches
    are reported separately so a reader can judge them.
    """
    a, b = _name_tokens(label), _name_tokens(prediction)
    if not a or not b:
        return False
    if a[-1] != b[-1]:
        return False
    if len(a) == 1 or len(b) == 1:
        return True
    return bool({t[0] for t in a[:-1]} & {t[0] for t in b[:-1]})


RULES = {
    "company_name": _match_company,
    "ticker": _match_ticker,
    "fiscal_year_end": _match_date,
    "employees": _match_employees,
    "total_assets": _match_number,
    "revenue_most_recent_fy": _match_number,
    "ceo_name": _match_person,
    "dividends_declared_per_share": _match_number,
    "goodwill_impairment": _match_number,
}


def matches(field: str, label, prediction) -> bool:
    """True when `prediction` is the same answer as `label` for `field`.

    Raises KeyError on an unknown field, rather than falling through to a
    default comparison that would quietly score a mistyped field as all-wrong.
    """
    rule = RULES[field]

    label_null, prediction_null = _is_nullish(label), _is_nullish(prediction)
    if label_null or prediction_null:
        return label_null and prediction_null

    return rule(label, prediction)
