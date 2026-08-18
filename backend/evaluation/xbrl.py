"""Read the facts a filer tagged in their own inline-XBRL document.

Every 10-K in this corpus is iXBRL: the registrant marks up each reported
figure with the concept it represents, the period it covers, and any dimensions
that qualify it. AMCR FY2024 carries 2,281 such facts, DOW FY2024 carries 3,770.

That markup is the *registrant's* structured assertion, inside the document
being labeled, so reading it is reading the filing rather than consulting a
second extractor -- the distinction §5 records under "LLM-assisted labeling".

Three properties of a fact do the work here:

* **period** -- an instant (`as of 2024-12-31`) or a duration. This is what
  catches the defect that got through twice on DGX: `3.20` and `3.44` are real
  tagged numbers belonging to the *following* fiscal year.
* **dimensions** -- a fact with no dimensions is the consolidated figure for the
  primary registrant. Every segment total, every subsidiary in a combined
  filing, every forward-looking scenario carries an axis. DOW tags
  `us-gaap:Assets` for Dow Inc. *and* for The Dow Chemical Company at the same
  instant, and only the undimensioned one is the answer.
* **scale** -- `scale="3"` means the displayed number is thousands. EXR displays
  `3,256,902` and means $3,256,902,000.

Parsing is by regex rather than an XML parser, deliberately: these documents are
HTML with XBRL attributes, up to 3 MB, and the alternative costs seconds per
filing for facts that are exactly one attribute lookup away. `ix:nonFraction`
elements contain a single text value and never nested markup, which is what
makes that safe here -- see the same argument in scripts/label_server.py.
"""

import re

_CONTEXT = re.compile(r'<xbrli:context id="([^"]+)"(.*?)</xbrli:context>', re.I | re.S)
_FACT = re.compile(r"<ix:(nonFraction|nonNumeric)([^>]*)>(.*?)</ix:\1>", re.I | re.S)
_TAGS = re.compile(r"<[^>]+>")


def _attr(attrs: str, name: str, default: str = "") -> str:
    found = re.search(rf'{name}="([^"]*)"', attrs, re.I)
    return found.group(1) if found else default


def parse_contexts(raw: str) -> dict[str, dict]:
    """contextRef -> period and dimensions."""
    out: dict[str, dict] = {}
    for match in _CONTEXT.finditer(raw):
        body = match.group(2)
        instant = re.search(r"<xbrli:instant>\s*([^<\s]+)", body, re.I)
        start = re.search(r"<xbrli:startDate>\s*([^<\s]+)", body, re.I)
        end = re.search(r"<xbrli:endDate>\s*([^<\s]+)", body, re.I)
        dims = [d.split(":")[-1] for d in re.findall(r'dimension="([^"]+)"', body, re.I)]
        out[match.group(1)] = {
            "instant": instant.group(1) if instant else None,
            "start": start.group(1) if start else None,
            "end": end.group(1) if end else None,
            "dims": dims,
        }
    return out


def _number(text: str, scale: str, sign: str) -> float | None:
    cleaned = re.sub(r"[,\s ]", "", text)
    if not re.fullmatch(r"-?\d*\.?\d+", cleaned or ""):
        return None
    value = float(cleaned) * (10 ** int(scale or 0))
    return -value if sign == "-" else value


def parse_facts(raw: str) -> list[dict]:
    """Every tagged fact, with its context resolved.

    `value` is the number as reported -- scale applied, sign applied -- so a
    figure displayed as `3,256,902` under `scale="3"` comes back as
    3256902000.0 and needs no further interpretation.
    """
    contexts = parse_contexts(raw)
    facts = []
    for match in _FACT.finditer(raw):
        kind, attrs, inner = match.group(1), match.group(2), match.group(3)
        name = _attr(attrs, "name")
        if not name:
            continue
        text = re.sub(r"\s+", " ", _TAGS.sub("", inner)).strip()
        context = contexts.get(_attr(attrs, "contextRef"), {})
        facts.append({
            "name": name,
            "text": text,
            "value": (_number(text, _attr(attrs, "scale"), _attr(attrs, "sign"))
                      if kind.lower() == "nonfraction" else None),
            "unit": _attr(attrs, "unitRef"),
            "instant": context.get("instant"),
            "start": context.get("start"),
            "end": context.get("end"),
            "dims": context.get("dims", []),
            "offset": match.start(),
        })
    return facts


def period_label(fact: dict) -> str:
    if fact.get("instant"):
        return f"as of {fact['instant']}"
    if fact.get("start") and fact.get("end"):
        return f"{fact['start']} to {fact['end']}"
    return "unresolved"


def duration_days(fact: dict) -> int | None:
    """Length of a duration fact, for telling a year from a quarter."""
    import datetime

    if not (fact.get("start") and fact.get("end")):
        return None
    try:
        start = datetime.date.fromisoformat(fact["start"])
        end = datetime.date.fromisoformat(fact["end"])
    except ValueError:
        return None
    return (end - start).days


def facts_named(facts: list[dict], concepts) -> list[dict]:
    wanted = {c.lower() for c in concepts}
    return [f for f in facts if f["name"].lower() in wanted]
