"""Fetch the calibration (dev-set) filings used by the stability check.

    python scripts/fetch_calibration_filings.py

Writes `<slug>.txt` -- extracted text, the same form the extractor sees -- into
corpus/calibration/, which is gitignored. check_extraction_stability.py reads
from there.

These are DEV-SET filings. The eight calibration issuers were read while writing
the extraction prompt and are excluded from the eval corpus, so fetching them
here does not touch any filing whose accuracy will be measured. Nothing in
corpus/filings/ is involved.

Provenance is written alongside as provenance.json, recording the exact
accession fetched for each slug. The original calibration run recorded only the
slugs "costco" and "apple", so which specific documents it used cannot be
recovered -- a re-run resolves the most recent 10-K, which may differ once a
newer one is filed. Pass --accession to pin a specific document instead.
"""

import argparse
import json
import pathlib
import sys
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from services.html_parser import extract_text_from_html  # noqa: E402

UA = "RAG-pipeline-prototype you@example.org"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})
SESSION.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(total=4, backoff_factor=1.5,
                          status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET",)),
        pool_connections=2,
        pool_maxsize=2,
    ),
)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
REQUEST_PAUSE = 0.2

# The two filings check_extraction_stability.py runs on, by slug. Both are in
# the calibration set declared in services/openai_structurer.py.
CALIBRATION = {
    "costco": 909832,
    "apple": 320193,
}


def latest_10k(cik: int, accession: str | None) -> dict:
    """Resolve a 10-K with an HTML primary document, newest first."""
    response = SESSION.get(SUBMISSIONS_URL.format(cik=cik), timeout=60)
    response.raise_for_status()
    time.sleep(REQUEST_PAUSE)
    recent = response.json()["filings"]["recent"]

    for i, form in enumerate(recent["form"]):
        if form != "10-K":
            continue
        primary = recent["primaryDocument"][i]
        if not primary.lower().endswith((".htm", ".html")):
            continue
        if accession and recent["accessionNumber"][i] != accession:
            continue
        return {
            "accession": recent["accessionNumber"][i],
            "filed": recent["filingDate"][i],
            "period": recent["reportDate"][i],
            "primary_document": primary,
            # Built against the real CIK, not the accession prefix: the prefix
            # is the filer agent and is not always the registrant.
            "url": (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/"
                f"{recent['accessionNumber'][i].replace('-', '')}/{primary}"
            ),
        }
    raise LookupError(
        f"no HTML 10-K for CIK {cik}"
        + (f" with accession {accession}" if accession else "")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path,
                        default=pathlib.Path("corpus/calibration"))
    parser.add_argument("--accession", action="append", default=[],
                        metavar="SLUG=ACCESSION",
                        help="pin a slug to an exact accession, e.g. "
                             "apple=0000320193-25-000079. Repeatable.")
    args = parser.parse_args()

    pinned = dict(pair.split("=", 1) for pair in args.accession)
    unknown = set(pinned) - set(CALIBRATION)
    if unknown:
        print(f"unknown slug(s): {sorted(unknown)}; "
              f"known: {sorted(CALIBRATION)}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    provenance = {"generated_by": "backend/scripts/fetch_calibration_filings.py",
                  "filings": {}}

    for slug, cik in CALIBRATION.items():
        try:
            filing = latest_10k(cik, pinned.get(slug))
            raw = SESSION.get(filing["url"], timeout=180)
            raw.raise_for_status()
            time.sleep(REQUEST_PAUSE)
        except (requests.RequestException, LookupError) as exc:
            print(f"  !! {slug}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        text = extract_text_from_html(raw.content)
        destination = args.out / f"{slug}.txt"
        destination.write_text(text, encoding="utf-8")

        provenance["filings"][slug] = {
            "cik": cik, **filing,
            "text_chars": len(text),
            "pinned": slug in pinned,
        }
        print(f"  {slug:<8} {filing['accession']}  period {filing['period']}  "
              f"{len(text):>9,} chars -> {destination}", file=sys.stderr)

    (args.out / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {len(provenance['filings'])} filings and provenance.json "
          f"to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
