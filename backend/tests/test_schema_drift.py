"""Guards schema.sql against drift from the live database.

The failure this prevents: someone changes a table in the Supabase dashboard and
does not update schema.sql, so a fresh clone builds a database that silently
differs from the one the app was developed and measured against.

The live check is opt-in. It skips cleanly when backend/.env holds no real
credentials or the database is unreachable, so the suite stays runnable offline
and in CI without secrets.
"""

import re
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
SCHEMA_SQL = BACKEND / "schema.sql"
ENV_FILE = BACKEND / ".env"

# schema.sql spelling -> Postgres type as PostgREST reports it
TYPE_MAP = {
    "uuid": "uuid",
    "text": "text",
    "numeric": "numeric",
    "jsonb": "jsonb",
    "boolean": "boolean",
    "timestamptz": "timestamp with time zone",
}
COLUMN_RE = re.compile(r"^(\w+)\s+(" + "|".join(TYPE_MAP) + r")\b")


def parse_schema_sql(sql: str) -> dict[str, dict[str, str]]:
    """Map each `create table` block to {column: postgres_type}."""
    tables: dict[str, dict[str, str]] = {}
    for table, body in re.findall(
        r"create table if not exists (\w+)\s*\((.*?)\n\);", sql, re.S
    ):
        columns: dict[str, str] = {}
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            match = COLUMN_RE.match(line)
            if match:
                columns[match.group(1)] = TYPE_MAP[match.group(2)]
        tables[table] = columns
    return tables


def _live_credentials() -> tuple[str, str] | None:
    """Real Supabase credentials from .env, or None. Never reads os.environ,
    which conftest has deliberately seeded with dummies."""
    if not ENV_FILE.exists():
        return None
    values = dotenv_values(ENV_FILE)
    url = (values.get("SUPABASE_URL") or "").strip()
    key = (values.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not url or not key or "test-project" in url or "your-project-ref" in url:
        return None
    return url, key


def test_schema_sql_parses_and_covers_expected_tables():
    """Offline: the parser works and schema.sql defines the post-002 tables."""
    tables = parse_schema_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
    assert set(tables) == {"reports", "extractions", "risks", "management"}
    # Spot-check the relationships the delete cascade depends on.
    assert tables["extractions"]["report_id"] == "uuid"
    assert tables["risks"]["extraction_id"] == "uuid"
    # The retained synthetic control corpus lives here; must not be dropped.
    assert tables["reports"]["raw_text"] == "text"
    assert tables["reports"]["structured_json"] == "jsonb"


def test_schema_sql_declares_all_nine_eval_fields():
    """Offline: the schema and the Pydantic contract cannot drift apart."""
    tables = parse_schema_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
    eval_fields = {
        "company_name": "text",
        "ticker": "text",
        "fiscal_year_end": "text",
        "employees": "text",
        "total_assets": "numeric",
        "revenue_most_recent_fy": "numeric",
        "ceo_name": "text",
        "dividends_declared_per_share": "numeric",
        "goodwill_impairment": "numeric",
    }
    for field, expected in eval_fields.items():
        assert tables["extractions"].get(field) == expected, (
            f"extractions.{field} should be {expected}, "
            f"got {tables['extractions'].get(field)}"
        )


@pytest.mark.live
def test_schema_sql_matches_live_database():
    """Live: every table/column/type in schema.sql matches the real database."""
    credentials = _live_credentials()
    if credentials is None:
        pytest.skip("no live Supabase credentials in backend/.env")
    url, key = credentials

    try:
        response = httpx.get(
            f"{url.rstrip('/')}/rest/v1/",
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
        response.raise_for_status()
    except Exception as exc:  # network down, project paused, key rotated
        pytest.skip(f"live database unreachable: {type(exc).__name__}: {exc}")

    live = {
        table: {c: p.get("format") for c, p in body["properties"].items()}
        for table, body in response.json()["definitions"].items()
    }
    declared = parse_schema_sql(SCHEMA_SQL.read_text(encoding="utf-8"))

    assert set(declared) == set(live), (
        f"table drift — only in schema.sql: {sorted(set(declared) - set(live))}, "
        f"only in database: {sorted(set(live) - set(declared))}"
    )
    for table in sorted(live):
        assert declared[table] == live[table], (
            f"column drift in {table!r} — "
            f"only in schema.sql: {sorted(set(declared[table]) - set(live[table]))}, "
            f"only in database: {sorted(set(live[table]) - set(declared[table]))}, "
            f"type mismatches: "
            f"{ {c: (declared[table][c], live[table][c]) for c in set(declared[table]) & set(live[table]) if declared[table][c] != live[table][c]} }"
        )
