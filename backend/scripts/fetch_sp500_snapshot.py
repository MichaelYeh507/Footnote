"""Capture a dated snapshot of S&P 500 constituents.

The eval corpus is drawn from index constituents, but SEC does not publish index
membership -- it has to come from outside. Membership also changes, so a script
that re-reads the live list would silently redefine the corpus on every run.

This captures the list once, with its source and fetch date, and commits the
result. Selection reads the snapshot, never the network. Regenerating the corpus
a year from now reproduces the same issuers because the snapshot is what is
under version control.

    python scripts/fetch_sp500_snapshot.py --out corpus/sp500_snapshot.json

Only identifiers are stored -- ticker, name, GICS sector, CIK. That is the same
category the corpus rules already permit committing (CIKs, accession numbers,
filing dates), and it is not filing content.
"""

import argparse
import datetime
import json
import pathlib
import sys

from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import sec_contact  # noqa: E402

SOURCE = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SESSION = sec_contact.session(pool=2)

EXPECTED_MIN_ROWS = 480  # the index holds ~503 tickers; well under is a parse failure


def scrape() -> list[dict]:
    response = SESSION.get(SOURCE, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    table = soup.find("table", {"id": "constituents"})
    if table is None:
        raise RuntimeError("constituents table not found -- page structure changed")

    headers = [th.get_text(strip=True) for th in table.find("tr").find_all("th")]

    def column(name: str) -> int:
        """Match ignoring whitespace -- get_text collapses 'GICS Sector' to
        'GICSSector', and the header wording drifts with page edits."""
        wanted = "".join(name.split()).lower()
        for i, header in enumerate(headers):
            if "".join(header.split()).lower().startswith(wanted):
                return i
        raise RuntimeError(f"column {name!r} not found in {headers}")

    i_symbol = column("Symbol")
    i_name = column("Security")
    i_sector = column("GICS Sector")
    i_cik = column("CIK")

    rows = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) <= max(i_symbol, i_name, i_sector, i_cik):
            continue
        cik_text = cells[i_cik].get_text(strip=True)
        if not cik_text.isdigit():
            continue
        rows.append({
            "ticker": cells[i_symbol].get_text(strip=True),
            "name": cells[i_name].get_text(strip=True),
            "gics_sector": cells[i_sector].get_text(strip=True),
            "cik": int(cik_text),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()

    rows = scrape()
    if len(rows) < EXPECTED_MIN_ROWS:
        print(f"ERROR: parsed only {len(rows)} rows, expected >= {EXPECTED_MIN_ROWS}. "
              f"Refusing to write a truncated snapshot.", file=sys.stderr)
        return 1

    sectors: dict[str, int] = {}
    for row in rows:
        sectors[row["gics_sector"]] = sectors.get(row["gics_sector"], 0) + 1

    snapshot = {
        "source": SOURCE,
        "fetched": datetime.date.today().isoformat(),
        "note": "Index membership changes. This snapshot, not the live page, "
                "defines the corpus universe.",
        "count": len(rows),
        "sectors": dict(sorted(sectors.items())),
        "constituents": sorted(rows, key=lambda r: r["ticker"]),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {args.out}: {len(rows)} constituents", file=sys.stderr)
    for sector, count in snapshot["sectors"].items():
        print(f"  {sector:<28}{count:>4}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
