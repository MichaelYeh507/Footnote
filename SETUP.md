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

## 3a. The frozen query set

`backend/corpus/query-set-freeze.json` is the retrieval query set's freeze, written
2026-08-20 before any arm ran. It holds one sha256 per query and one digest over all 65,
plus strata, accessions and items — **identifiers and hashes, never query text and never a
gold span**, because gold is `(accession, quoted span)` and the set itself is data that
lives beside the filings rather than in this repo. `EVALUATION-SPEC.md` records what the
hashes cover and why.

The file is self-checkable without the query set: re-digest its own rows — one
`query_id + "  " + sha256` line each, sorted by `query_id`, then sha256 of the result —
and compare against `set_sha256`.

With the query set present (`RAG_FILINGS_DIR` set), the full check is:

```bash
cd backend
python scripts/freeze_queries.py --verify
```

It exits non-zero and names any query whose text has changed since the freeze. The same
check is called by `query_freeze.refuse_unless_frozen`, which every retrieval arm runs
before retrieving anything — so a set edited after the freeze fails loudly instead of
quietly changing what was measured. `scripts/run_retrieval.py` calls it first, before it
even opens the database.

## 3b. The three retrieval arms

`services/retrieval.py` holds the sparse, dense and hybrid arms at the parameters
`EVALUATION-SPEC.md` pre-registered on 2026-08-19, before either index existed. Running
them is two steps, deliberately: the first produces ranked lists, the second turns them
into numbers.

```bash
cd backend
python scripts/run_retrieval.py --dry-run   # every refusal, retrieves nothing
python scripts/run_retrieval.py             # all 65 queries, both indexes
python scripts/score_retrieval.py           # recall@1/@5 per arm per stratum
```

The split is not cosmetic. Scoring code written while the rankings are on screen gets
shaped by them one judgement call at a time, so the scorer was written and tested before
any arm ran — the same reason the query-set validator was built before the first query.

`run_retrieval.py` needs `RAG_FILINGS_DIR`, `DATABASE_URL` and `OPENAI_API_KEY`. It writes
ranked lists and a provenance record — every parameter, the frozen set digest, a digest
over the query embeddings, and a sha256 of the rankings file — to a `retrieval/` directory
beside the filings, **never into this repo**.

`score_retrieval.py` needs only `RAG_FILINGS_DIR`: no database and no API, so any published
retrieval number can be recomputed offline from the artifacts. It refuses a partial run, a
run made against a different query-set digest, and a rankings file whose bytes no longer
match the sha256 its provenance recorded.

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
| `RAG_FILINGS_DIR` | shell / User scope | No — defaults to `backend/corpus/filings` | `backend/corpus_paths.py` |
| `RAG_CALIBRATION_DIR` | shell / User scope | No — defaults to `backend/corpus/calibration` | `backend/corpus_paths.py` |
| `SEC_CONTACT_EMAIL` | shell / User scope | Yes, for any SEC fetch — **no default, by design** | `backend/sec_contact.py` |
| `DATABASE_URL` | `backend/.env` | To apply a migration, rebuild the retrieval indexes, or run the arms — **no default, by design**. Scoring does not need it. | `backend/database.py` |

The SEC corpus itself is not committed (only its manifest is). Every corpus reader — the
fetch scripts, the labeling app, the label checkers — resolves the filings directory through
`backend/corpus_paths.py`: set the two `RAG_*` variables to keep the data outside the repo,
or place the filings at the repo-relative defaults. `scripts/fetch_filings.py` reproduces the
corpus from the committed manifest into whichever location is configured.

### `DATABASE_URL`

`services/supabase_client.py` talks PostgREST, which can neither issue DDL nor
`COPY`. Migration 003 creates the chunk table with its GIN and HNSW indexes, and
`scripts/load_chunks.py` bulk-loads 11,621 rows, so both need a direct
connection. The API itself does not — leave this unset to run the app.

Two traps, and they report the *same* misleading error:

- **Use the Session pooler string** (Supabase → Connect), not "Direct
  connection". The direct host `db.<ref>.supabase.co` has no A record — IPv6
  only, unless you buy the IPv4 add-on — so on an IPv4-only machine libpq
  reports `getaddrinfo failed`, which reads as DNS and is not. The pooler host
  contains `pooler.supabase.com`, and its username is `postgres.<project-ref>`
  rather than bare `postgres`.
- **Percent-encode the password.** Supabase generates passwords containing `@`,
  which a URI reserves: libpq splits the userinfo at the *first* `@`, so part of
  the password becomes the hostname — and the error is `getaddrinfo failed`
  again. Write it as `%40` (likewise `%5B %5D %2F %3F %23`). The identical
  credentials connect fine when passed as keyword arguments, which is what makes
  this look like a driver bug rather than an encoding one.

`backend/database.py` refuses both cases with a message naming the substitution,
and never echoes the password. There is deliberately no default: a plausible one
like `postgresql://localhost/postgres` would connect on any machine running a
local Postgres and build both indexes over an empty table, and every recall
number computed afterwards would be zero with nothing saying why.

### `SEC_CONTACT_EMAIL`

SEC's fair-access policy requires automated requests to carry a real contact address in
their `User-Agent`; requests without one are throttled or refused. Set it before running
any fetch script:

```powershell
$env:SEC_CONTACT_EMAIL = "you@example.org"     # this shell only
[Environment]::SetEnvironmentVariable("SEC_CONTACT_EMAIL", "you@example.org", "User")
```

```bash
export SEC_CONTACT_EMAIL=you@example.org
```

There is deliberately **no default and no placeholder**. A fabricated contact on a request
to a federal system is worse than no request, so `sec_contact.py` raises instead of
substituting one — and it raises on the request path, not at import, so the modules stay
importable (the tests read pure functions out of them). The address is an environment
variable rather than a constant because this repository is public and a committed address
is permanent; `tests/test_sec_contact.py` fails the build if any published file carries
one.

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
