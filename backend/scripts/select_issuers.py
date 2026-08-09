"""Select the eval corpus issuers, mechanically and reproducibly.

Implements the selection rule pre-registered in HYBRID-RETRIEVAL-SEC-PLAN.md §2.
The rule was written down before this script ran, so the resulting list is
auditable rather than a judgment call made after seeing results.

    python scripts/select_issuers.py --out corpus/issuers.json

Universe is the committed S&P 500 snapshot, not the live index and not the full
set of SEC registrants. Two consequences worth stating plainly:

  * Index membership IS the substance filter. An earlier revision drew from all
    ~10,000 SEC registrants and needed a size heuristic to exclude shells, SPACs,
    and pre-revenue nano-caps, whose filings are null in five or more of the nine
    fields by construction. Constituent membership does that job precisely, with
    no threshold to tune.
  * The measured number becomes a claim about large-cap filings, not about SEC
    filings generally. That is a scope statement for the README, not a defect --
    and large-caps are the harder end of the distribution, so the number errs
    conservative.

Writes only identifiers: CIK, ticker, name, sector, accession numbers. No filing
content. Fetching is a separate step.
"""

import argparse
import json
import pathlib
import random
import sys
import time
from typing import Iterator

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = "RAG-pipeline-prototype you@example.org"

# One session for the whole run. Opening a fresh TCP connection per request gets
# the connection reset by SEC -- hundreds of handshakes in a row look like abuse
# regardless of how politely they are paced.
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=4,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
        ),
        pool_connections=4,
        pool_maxsize=4,
    ),
)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DEFAULT_UNIVERSE = pathlib.Path("corpus/sp500_snapshot.json")

SEED = 20260809
PER_SECTOR = 2           # 2 issuers x 11 GICS sectors = 22 issuers
FILINGS_PER_ISSUER = 2   # 22 x 2 = 44 filings, 396 labeled instances
MAX_CANDIDATES = 400
REQUEST_PAUSE = 0.15     # SEC allows 10 req/s; stay well under

# These eight filings were read closely while calibrating the extraction prompt
# (see services/openai_structurer.py). They are the dev set; including them in
# the eval corpus would leak. All eight are index constituents.
CALIBRATION_CIKS = {320193, 909832, 1058090, 19617, 200406, 34088, 18230, 753308}


def get_json(url: str) -> dict:
    response = SESSION.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def load_universe(path: pathlib.Path) -> tuple[list[dict], dict]:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    return snapshot["constituents"], {
        "source": snapshot["source"],
        "fetched": snapshot["fetched"],
        "count": snapshot["count"],
    }


def eligible_10ks(recent: dict) -> Iterator[dict]:
    """10-K filings whose primary document is HTML, newest first."""
    for i, form in enumerate(recent["form"]):
        if form != "10-K":
            continue
        primary = recent["primaryDocument"][i]
        if not primary.lower().endswith((".htm", ".html")):
            continue
        yield {
            "accession": recent["accessionNumber"][i],
            "filed": recent["filingDate"][i],
            "period": recent["reportDate"][i],
            "primary_document": primary,
            "submission_bytes": recent["size"][i],
            "url": (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(recent['accessionNumber'][i][:10])}/"
                f"{recent['accessionNumber'][i].replace('-', '')}/{primary}"
            ),
        }


def evaluate(candidate: dict) -> dict | None:
    """Confirm the issuer has two consecutive HTML 10-Ks available."""
    try:
        subs = get_json(SUBMISSIONS_URL.format(cik=candidate["cik"]))
    except (requests.HTTPError, requests.ConnectionError, ValueError):
        return None
    finally:
        time.sleep(REQUEST_PAUSE)

    filings = list(eligible_10ks(subs["filings"]["recent"]))
    if len(filings) < FILINGS_PER_ISSUER:
        return None

    # The accession-derived URL uses the CIK from the accession prefix, which is
    # the filer agent, not always the registrant. Rebuild against the real CIK.
    for filing in filings[:FILINGS_PER_ISSUER]:
        filing["url"] = (
            f"https://www.sec.gov/Archives/edgar/data/{candidate['cik']}/"
            f"{filing['accession'].replace('-', '')}/{filing['primary_document']}"
        )

    return {
        "cik": candidate["cik"],
        "ticker": candidate["ticker"],
        "name": subs.get("name", candidate["name"]),
        "sector": candidate["gics_sector"],
        "sic": subs.get("sic"),
        "sic_description": subs.get("sicDescription"),
        "filings": filings[:FILINGS_PER_ISSUER],
    }


def select(universe_path: pathlib.Path, verbose: bool = True) -> dict:
    """Walk a seeded shuffle, taking up to PER_SECTOR issuers per GICS sector."""
    pool, provenance = load_universe(universe_path)
    sectors = sorted({row["gics_sector"] for row in pool})
    wanted = PER_SECTOR * len(sectors)

    rng = random.Random(SEED)
    rng.shuffle(pool)

    accepted: list[dict] = []
    per_sector: dict[str, int] = {}
    examined = 0
    skipped_calibration: list[str] = []
    rejected_ineligible: list[str] = []

    for candidate in pool:
        if len(accepted) >= wanted or examined >= MAX_CANDIDATES:
            break
        if candidate["cik"] in CALIBRATION_CIKS:
            skipped_calibration.append(candidate["ticker"])
            continue
        if per_sector.get(candidate["gics_sector"], 0) >= PER_SECTOR:
            continue

        examined += 1
        enriched = evaluate(candidate)
        if enriched is None:
            rejected_ineligible.append(candidate["ticker"])
            continue

        per_sector[enriched["sector"]] = per_sector.get(enriched["sector"], 0) + 1
        accepted.append(enriched)
        if verbose:
            print(
                f"  [{len(accepted):>2}/{wanted}] {enriched['ticker']:<6}"
                f"{enriched['sector']:<24}{enriched['name'][:40]}",
                file=sys.stderr,
            )

    return {
        "generated_by": "backend/scripts/select_issuers.py",
        "rule": "HYBRID-RETRIEVAL-SEC-PLAN.md §2, pre-registered 2026-08-09",
        "seed": SEED,
        "universe": provenance,
        "parameters": {
            "per_sector": PER_SECTOR,
            "sectors": len(sectors),
            "issuers_wanted": wanted,
            "filings_per_issuer": FILINGS_PER_ISSUER,
            "max_candidates": MAX_CANDIDATES,
        },
        "excluded_calibration_ciks": sorted(CALIBRATION_CIKS),
        "excluded_calibration_tickers": sorted(skipped_calibration),
        "candidates_examined": examined,
        "rejected_ineligible": sorted(rejected_ineligible),
        "issuer_count": len(accepted),
        "filing_count": len(accepted) * FILINGS_PER_ISSUER,
        "issuers": accepted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    parser.add_argument("--universe", type=pathlib.Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    args = parser.parse_args()

    result = select(args.universe)

    by_sector: dict[str, list[str]] = {}
    for issuer in result["issuers"]:
        by_sector.setdefault(issuer["sector"], []).append(issuer["ticker"])

    print(f"\nexamined {result['candidates_examined']} candidates -> "
          f"{result['issuer_count']} issuers, "
          f"{result['filing_count']} filings", file=sys.stderr)
    for sector in sorted(by_sector):
        print(f"  {sector:<26}{', '.join(by_sector[sector])}", file=sys.stderr)
    if result["excluded_calibration_tickers"]:
        print(f"  excluded (dev set): "
              f"{', '.join(result['excluded_calibration_tickers'])}", file=sys.stderr)
    if result["rejected_ineligible"]:
        print(f"  rejected (no two HTML 10-Ks): "
              f"{', '.join(result['rejected_ineligible'])}", file=sys.stderr)

    if result["issuer_count"] < result["parameters"]["issuers_wanted"]:
        print("\nWARNING: short of target. Raise MAX_CANDIDATES rather than "
              "loosening the rule.", file=sys.stderr)

    if args.dry_run:
        print("\n(dry run, nothing written)", file=sys.stderr)
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
