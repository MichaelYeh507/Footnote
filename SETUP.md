# Setup

Mechanical run instructions. Deliberately kept out of `README.md` — per the project handoff,
the README is written last, from results. Fold this in at publish time.

Prerequisites: **Python 3.12+** and **Node.js 20+**. Neither is bundled; install both first.

## 1. Backend

```bash
cd backend
python -m venv venv
```

Activate it — Windows (Git Bash): `source venv/Scripts/activate`, Windows (PowerShell):
`.\venv\Scripts\Activate.ps1`, macOS/Linux: `source venv/bin/activate`.

```bash
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
```

Fill in `.env` — see the comments in `.env.example` for where each value comes from. The backend
**will not start** with `OPENAI_API_KEY` unset: the OpenAI client is constructed at import time in
`services/openai_structurer.py`.

```bash
uvicorn main:app --reload --port 8000
```

`GET http://localhost:8000/` should return `{"status":"ok","service":"document-pipeline"}`.

## 2. Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Opens on http://localhost:3000. `NEXT_PUBLIC_API_URL` is optional; it defaults to
`http://localhost:8000`.

## 3. Tests

```bash
cd backend
python -m pytest
```

Tests seed dummy credentials in `tests/conftest.py` before any app module is imported, so they do
not touch live services even when a real `.env` is present.

One test is the exception, marked `live`: `test_schema_sql_matches_live_database` reads
`backend/.env` directly and diffs `schema.sql` against the real database. It **skips** when
credentials are absent or the project is unreachable, so the suite stays green offline and in CI.
To exclude it explicitly:

```bash
python -m pytest -m "not live"
```

## 4. Database schema

`backend/schema.sql` recreates the current schema on a fresh Supabase project — apply it in
Dashboard > SQL Editor. It was captured from the live database rather than hand-written, and the
`live` test above fails if the two drift apart. **If you change a table in the Supabase dashboard,
add a migration under `backend/migrations/` and update `schema.sql` in the same commit.**

`backend/migrations/` holds the ordered change history (`001_initial_schema.sql` is the captured
pre-trim baseline; `002_trim_to_sec_schema.sql` trims to the SEC 10-K field set). `schema.sql` is
always the current state, not a migration.

To back up all rows before a destructive migration — the synthetic corpus exists only in the
database:

```bash
python scripts/export_corpus.py <output-dir-outside-the-repo>
```

## Required environment variables

| Variable | Where | Required | Read by |
|---|---|---|---|
| `OPENAI_API_KEY` | `backend/.env` | Yes — crashes at import if unset | `services/openai_structurer.py` |
| `SUPABASE_URL` | `backend/.env` | Yes | `services/supabase_client.py` |
| `SUPABASE_SERVICE_KEY` | `backend/.env` | Yes | `services/supabase_client.py` |
| `SUPABASE_ANON_KEY` | `backend/.env` | No — currently unread | — |
| `NEXT_PUBLIC_API_URL` | `frontend/.env.local` | No — defaults to `http://localhost:8000` | `app/lib/api.ts` |

## Schema notes

Four tables: `reports` → `extractions` → `risks` / `management`, with `ON DELETE CASCADE` on every
foreign key (verified against the live database, not assumed — `delete_report()` relies on it to
clean up children).

`extractions` holds one row per extraction from one filing. It is **not** an entity table: with
~40 filings from 3–4 issuers, several rows share an issuer. Entity resolution is out of scope.

`reports.raw_text` and `reports.structured_json` hold the retained synthetic control corpus used
for the clean-vs-real gap. Do not drop them.

Two properties are inherited from the original dashboard-built project and reproduced deliberately
in `schema.sql`:

- **Row Level Security is disabled** on all six tables. Safe while the backend is the only client,
  since the service role key bypasses RLS regardless. Enable it before any browser-side Supabase
  access with the anon key.
- **Foreign key columns are unindexed.** Postgres does not index them automatically, so
  `report_id` and `company_id` have no index despite every child query filtering on them.
  Immaterial at current row counts.
