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

# schema.sql spelling -> Postgres type as PostgREST reports it.
#
# `vector` cannot shadow `tsvector` in the alternation below because the two
# differ at the first character, and neither can shadow `text`. The live test
# prints the real format string whenever a mapping here is wrong, which is how
# the three added with migration 003 were pinned rather than guessed.
TYPE_MAP = {
    "uuid": "uuid",
    "text": "text",
    "numeric": "numeric",
    "jsonb": "jsonb",
    "boolean": "boolean",
    "timestamptz": "timestamp with time zone",
    # Added with migration 003.
    "integer": "integer",
    "tsvector": "tsvector",
    "vector": "vector",
}
COLUMN_RE = re.compile(
    r"^(\w+)\s+(" + "|".join(TYPE_MAP) + r")\b(\(\d+\))?"
)


def normalize_live_type(reported: str | None) -> str | None:
    """PostgREST's format string, reduced to what schema.sql can express.

    Measured against the live database rather than guessed: a pgvector column
    comes back as `extensions.vector(1536)` -- schema-qualified, because
    Supabase installs the extension into `extensions`, and carrying its
    typmod. The schema prefix is a deployment detail (a hand-run
    `create extension vector` puts it in `public` instead), so it is stripped.

    The typmod is deliberately KEPT. It is the only place the live database
    states the embedding width, so comparing `vector(1536)` against
    `vector(1536)` is what makes a dimension change fail this test instead of
    failing later, one rejected insert at a time, after the embedding spend.
    """
    if reported is None:
        return None
    return reported.split(".")[-1] if "." in reported else reported


# The pre-registered dense width (EVALUATION-SPEC.md, 2026-08-19). Asserted
# against schema.sql so the database and the pre-registration cannot drift. A
# column of the wrong width is not a loud failure: the wrong-sized insert is
# rejected row by row, long after the embedding spend.
EMBEDDING_DIMENSIONS = 1536


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
                # The typmod is carried through when schema.sql states one, so
                # `vector(1536)` is compared as a width rather than as a bare
                # type. Only pgvector declares one here; everything else has
                # group(3) None and is unaffected.
                columns[match.group(1)] = TYPE_MAP[match.group(2)] + (
                    match.group(3) or ""
                )
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
    """Offline: the parser works and schema.sql defines the post-003 tables."""
    tables = parse_schema_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
    assert set(tables) == {"reports", "extractions", "risks", "management",
                           "chunks"}
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


def test_schema_sql_declares_the_chunk_store_columns():
    """Offline: the chunk table carries every field the store writes.

    scripts/build_chunks.py emits eleven fields per chunk. A column missing here
    is not a loud failure — the loader would drop that field and the store and
    the database would disagree about what a chunk is, with the citation
    metadata the likeliest casualty because nothing else reads it until the QA
    layer does.
    """
    tables = parse_schema_sql(SCHEMA_SQL.read_text(encoding="utf-8"))
    store_fields = {
        "chunk_id": "text",
        "accession": "text",
        "ticker": "text",
        "period": "text",
        "item": "text",
        "title": "text",
        # `index` in the JSONL; renamed here because bare `index` reads as the
        # SQL object, not as a position.
        "chunk_index": "integer",
        "first_page": "integer",
        "last_page": "integer",
        "tokens": "integer",
        "text": "text",
    }
    for field, expected in store_fields.items():
        assert tables["chunks"].get(field) == expected, (
            f"chunks.{field} should be {expected}, "
            f"got {tables['chunks'].get(field)}"
        )


def test_schema_sql_declares_both_retrieval_columns():
    """Offline: the two arms' columns exist, at the pre-registered width."""
    raw = SCHEMA_SQL.read_text(encoding="utf-8")
    sql = strip_sql_comments(raw)
    tables = parse_schema_sql(raw)
    assert tables["chunks"].get("embedding") == f"vector({EMBEDDING_DIMENSIONS})"
    assert tables["chunks"].get("tsv") == "tsvector"
    assert f"vector({EMBEDDING_DIMENSIONS})" in sql, (
        f"schema.sql must declare the pre-registered "
        f"{EMBEDDING_DIMENSIONS}-dimension embedding column"
    )


def strip_sql_comments(sql: str) -> str:
    """schema.sql with `--` comments removed.

    Not decoration. The first version of the index test below searched the raw
    file, and perturbation showed it passing against a schema whose GIN index
    had been commented out — the assertion was matching its own explanation in
    the comment above the statement. Every offline assertion about what the
    schema *does* runs against this, never against the raw text.
    """
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_schema_sql_builds_the_indexes_the_arms_actually_use():
    """Offline: opclass and method, which are silent when wrong.

    An HNSW index built on the wrong opclass is not used by a `<=>` query at
    all. The query still returns correct rows, by sequential scan, so the defect
    surfaces as latency on 11,621 rows rather than as an error — which is to say
    it does not surface at all at this corpus size. Same for the sparse arm: no
    GIN index means `@@` still works, slowly.
    """
    sql = strip_sql_comments(SCHEMA_SQL.read_text(encoding="utf-8"))
    assert "using gin (tsv)" in sql
    assert "using hnsw (embedding vector_cosine_ops)" in sql


def test_schema_sql_enables_rls_on_the_chunk_table():
    """Offline: the one table that carries bulk filing text is not anon-readable.

    Decided 2026-08-19, against the project-wide default, because `chunks` holds
    22.9 MB across 11,621 rows while the other four hold a few dozen extraction
    rows each. The live counterpart below checks the behaviour; this checks that
    the declaration did not get dropped in a later edit.
    """
    sql = strip_sql_comments(SCHEMA_SQL.read_text(encoding="utf-8"))
    assert "alter table chunks enable row level security;" in sql


@pytest.mark.live
def test_rls_hides_the_chunk_text_from_the_anon_key():
    """Live: what RLS actually does, not what schema.sql says about it.

    Compares the two keys rather than expecting a permission error, because a
    table with RLS on and no policy returns an empty result to anon -- HTTP 200,
    zero rows. A test asserting "no error" would pass with RLS off.

    Skips while the table is empty: before the load both keys legitimately see
    zero rows, and a test that cannot distinguish the states would only
    reassure.
    """
    credentials = _live_credentials()
    if credentials is None:
        pytest.skip("no live Supabase credentials in backend/.env")
    url, service_key = credentials
    anon_key = (dotenv_values(ENV_FILE).get("SUPABASE_ANON_KEY") or "").strip()
    if not anon_key:
        pytest.skip("no SUPABASE_ANON_KEY in backend/.env")

    def count(key):
        response = httpx.get(
            f"{url.rstrip('/')}/rest/v1/chunks",
            params={"select": "chunk_id", "limit": "1"},
            headers={"apikey": key, "Authorization": f"Bearer {key}"},
            timeout=15.0,
        )
        if response.status_code == 404:
            pytest.skip("chunks table does not exist yet (migration 003)")
        response.raise_for_status()
        return len(response.json())

    if count(service_key) == 0:
        pytest.skip("chunks table is empty; RLS is untestable until it is loaded")
    assert count(anon_key) == 0, (
        "the anon key can read chunks.text -- RLS is off, or a policy grants it"
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
        table: {c: normalize_live_type(p.get("format"))
                for c, p in body["properties"].items()}
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
