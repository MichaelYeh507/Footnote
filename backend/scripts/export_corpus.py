"""Export every row of the current schema to JSON, for use as a restore point.

Written for the pre-trim backup: the synthetic corpus exists only as rows in
Supabase (reports.raw_text is the sole copy of those documents), so it must be
exported before any destructive migration.

Usage:
    python scripts/export_corpus.py <output-dir>

Write the output somewhere OUTSIDE the repo. Datasets are never committed.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
TABLES = ["reports", "companies", "financials", "risks", "management", "valuations"]


def main(out_dir: Path) -> int:
    env = dotenv_values(BACKEND / ".env")
    url = (env.get("SUPABASE_URL") or "").strip().rstrip("/")
    key = (env.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not url or not key:
        print("error: SUPABASE_URL / SUPABASE_SERVICE_KEY missing from backend/.env")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    manifest = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "source_project": url,
        "tables": {},
    }

    for table in TABLES:
        response = httpx.get(
            f"{url}/rest/v1/{table}",
            params={"select": "*"},
            headers=headers,
            timeout=60.0,
        )
        response.raise_for_status()
        rows = response.json()
        path = out_dir / f"{table}.json"
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest["tables"][table] = len(rows)
        print(f"  {table:12s} {len(rows):>4} rows -> {path.name}")

    # Keep the schema that produced these rows next to them.
    schema = BACKEND / "schema.sql"
    if schema.exists():
        (out_dir / "schema.sql").write_text(schema.read_text(encoding="utf-8"), encoding="utf-8")

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(f"\nexported {sum(manifest['tables'].values())} rows to {out_dir}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]).expanduser().resolve()))
