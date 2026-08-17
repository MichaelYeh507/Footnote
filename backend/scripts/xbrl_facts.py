"""Show the filer's own tagged facts for a field, with their reporting periods.

Why this exists: `dividends_declared_per_share` is slow to find by eye, and the
tempting shortcut is to ask an LLM for it. That shortcut would end the
measurement -- the label would be model output, extraction accuracy on that
field would compare a model against a model, and it is the field carrying the
false-extraction rate, the most fragile number in the project.

These filings are inline XBRL. AMCR FY2024 carries 2,044 tagged facts, and the
dividend is among them as `us-gaap:CommonStockDividendsPerShareDeclared` with
`unitRef="usdPerShare"`. That tag is the **registrant's own** structured
assertion, present in the very document being labeled. Reading it is reading
the filing, not consulting a second extractor.

It is a finder, not an oracle, and the difference is the whole point. AMCR
FY2024 tags the dividend four times: FY2022, FY2023, FY2024 comparatives plus a
subsequent-event quarterly declaration. Picking the fiscal year under label is
still the labeler's call, which is why this prints every fact with its resolved
period and never a single answer.

Deliberately a separate script, not wired into the labeling app. That app's
isolation from model output is enforced by tests, it is in active use, and this
does not need to be inside it to be useful.

    cd backend
    .\\venv\\Scripts\\python.exe scripts\\xbrl_facts.py AMCR 2024-06-30
    .\\venv\\Scripts\\python.exe scripts\\xbrl_facts.py AMCR 2024-06-30 --field total_assets
    .\\venv\\Scripts\\python.exe scripts\\xbrl_facts.py --list-fields
"""

import argparse
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import corpus_paths  # noqa: E402

# us-gaap concepts per field. Several fields have more than one accepted tag
# because filers choose between them; all are shown rather than guessed at.
# `ceo_name` has no numeric concept and is absent on purpose -- a field with no
# tag must print nothing rather than something adjacent.
FIELD_TAGS = {
    "company_name": ("dei:EntityRegistrantName",),
    "ticker": ("dei:TradingSymbol",),
    "fiscal_year_end": ("dei:CurrentFiscalYearEndDate", "dei:DocumentPeriodEndDate"),
    "employees": ("dei:EntityNumberOfEmployees",),
    "total_assets": ("us-gaap:Assets",),
    "revenue_most_recent_fy": (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerMember",
    ),
    "dividends_declared_per_share": (
        "us-gaap:CommonStockDividendsPerShareDeclared",
        "us-gaap:CommonStockDividendsPerShareCashPaid",
        "us-gaap:DistributionsMade",
    ),
    "goodwill_impairment": (
        "us-gaap:GoodwillImpairmentLoss",
        "us-gaap:ImpairmentOfIntangibleAssetsIncludingGoodwill",
    ),
}

_TAGGED = re.compile(
    r"<ix:(?:nonFraction|nonNumeric)([^>]*)>(.*?)</ix:(?:nonFraction|nonNumeric)>",
    re.I | re.S)
_CONTEXT = re.compile(r'<xbrli:context id="([^"]+)"(.*?)</xbrli:context>', re.I | re.S)


def contexts(raw: str) -> dict[str, tuple[str, str]]:
    """contextRef -> (period, dimensions). Dimensions matter: a fact carrying
    a SubsequentEventTypeAxis is a later declaration, not the year's total."""
    found = {}
    for match in _CONTEXT.finditer(raw):
        body = match.group(2)
        start = re.search(r"<xbrli:startDate>([^<]+)", body, re.I)
        end = re.search(r"<xbrli:endDate>([^<]+)", body, re.I)
        instant = re.search(r"<xbrli:instant>([^<]+)", body, re.I)
        dims = re.findall(r'dimension="([^"]+)"', body, re.I)
        if start and end:
            period = f"{start.group(1)} to {end.group(1)}"
        elif instant:
            period = f"as of {instant.group(1)}"
        else:
            period = "unresolved"
        found[match.group(1)] = (
            period, ", ".join(d.split(":")[-1] for d in dims))
    return found


def attr(attrs: str, name: str) -> str:
    match = re.search(rf'{name}="([^"]*)"', attrs, re.I)
    return match.group(1) if match else ""


def facts_for(raw: str, tags: tuple[str, ...]) -> list[dict]:
    ctx = contexts(raw)
    wanted = {t.lower() for t in tags}
    out = []
    for match in _TAGGED.finditer(raw):
        attrs, inner = match.group(1), match.group(2)
        name = attr(attrs, "name")
        if name.lower() not in wanted:
            continue
        text = re.sub(r"<[^>]+>", "", inner)
        text = re.sub(r"\s+", " ", text).strip()
        period, dims = ctx.get(attr(attrs, "contextRef"), ("unresolved", ""))
        out.append({
            "name": name, "value": text, "period": period, "dims": dims,
            "scale": attr(attrs, "scale"), "sign": attr(attrs, "sign"),
            "unit": attr(attrs, "unitRef"), "decimals": attr(attrs, "decimals"),
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker", nargs="?", help="e.g. AMCR")
    parser.add_argument("period", nargs="?", help="e.g. 2024-06-30")
    parser.add_argument("--field", default="dividends_declared_per_share",
                        choices=sorted(FIELD_TAGS), metavar="FIELD")
    parser.add_argument("--filings-dir", type=pathlib.Path,
                        default=corpus_paths.filings_dir())
    parser.add_argument("--list-fields", action="store_true",
                        help="print the fields that have a tag and exit")
    args = parser.parse_args()

    if args.list_fields:
        for field, tags in sorted(FIELD_TAGS.items()):
            print(f"{field}\n    " + "\n    ".join(tags))
        print("\nceo_name has no XBRL concept and must be read from the filing.")
        return 0

    if not args.ticker or not args.period:
        parser.error("ticker and period are required unless --list-fields")

    document = args.filings_dir / f"{args.ticker}_{args.period}.htm"
    if not document.exists():
        print(f"not fetched: {document}", file=sys.stderr)
        return 1

    raw = document.read_bytes().decode("utf-8", "replace")
    total = len(re.findall(r"<ix:(?:nonFraction|nonNumeric)", raw, re.I))
    if total == 0:
        # Not an error. Older or non-XBRL primary documents exist, and saying
        # "no facts" when the reason is "no tags at all" would read as an
        # answer about the dividend rather than about the document.
        print(f"{document.name} carries no inline XBRL tags. Read it by eye.")
        return 0

    facts = facts_for(raw, FIELD_TAGS[args.field])
    print(f"{document.name}  ·  {total:,} tagged facts in the document")
    print(f"field: {args.field}")
    print(f"tags:  {', '.join(FIELD_TAGS[args.field])}\n")

    if not facts:
        print("  no fact carries any of those tags.")
        print("  That is NOT evidence the filing omits the item -- filers tag")
        print("  inconsistently, and a value stated only in prose is untagged.")
        print("  Read the filing before recording an absence.")
        return 0

    for fact in facts:
        bits = [f"unit={fact['unit'] or '-'}"]
        if fact["scale"] and fact["scale"] != "0":
            bits.append(f"scale=10^{fact['scale']}")
        if fact["sign"]:
            bits.append(f"sign={fact['sign']}")
        if fact["dims"]:
            bits.append(f"dims={fact['dims']}")
        print(f"  {fact['value']:>18}   {fact['period']:<26} {'  '.join(bits)}")

    print("\n  Pick the period under label yourself. A comparative column and a")
    print("  subsequent-event declaration are both tagged the same way, and the")
    print("  anchor still has to come from text you selected in the filing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
