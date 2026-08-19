-- Database schema — captured from the live Supabase project, not hand-written.
--
-- Every column, type, default, and constraint below was read from the running
-- database (PostgREST OpenAPI spec + information_schema), then verified against
-- it by tests/test_schema_drift.py. This file is a faithful snapshot of the
-- schema as it exists today.
--
-- Current state: after migrations/003_chunks_and_retrieval_indexes.sql.
-- Change history lives in migrations/. If you alter a table in the Supabase
-- dashboard, add a migration AND update this file in the same commit — the live
-- drift test fails otherwise.
--
-- Apply to a fresh Supabase project via Dashboard > SQL Editor, or:
--   psql "$DATABASE_URL" -f schema.sql
--
-- Order matters: reports -> extractions -> child tables (FK dependency order).
-- `chunks` stands alone: it is keyed by the filing's accession, not by a
-- reports row, because the 44 corpus filings are on disk rather than in this
-- database.
--
-- Two properties reproduced deliberately from the original project:
--   * Row Level Security is DISABLED on the four extraction tables. Safe while
--     the backend is the only client (the service role key bypasses RLS
--     regardless). Enable it before any browser-side Supabase access with the
--     anon key. `chunks` is the exception, added in 003 and enabled there --
--     see the note beside it.
--   * Numeric money fields are unconstrained `numeric`. Units are documented by
--     the extraction prompt (millions), not enforced by the database.

-- The filing/document. raw_text and structured_json are the retained synthetic
-- control corpus for the clean-vs-real gap; do not drop them.
create table if not exists reports (
    id              uuid primary key default gen_random_uuid(),
    filename        text not null,
    upload_date     timestamptz default now(),
    status          text default 'processing',
    raw_text        text,
    structured_json jsonb,
    report_type     text,
    error_message   text
);

-- One row per extraction from one filing. Not an entity table: with ~40 filings
-- from 3-4 issuers this holds ~10 rows per issuer. Entity resolution is out of
-- scope.
create table if not exists extractions (
    id         uuid primary key default gen_random_uuid(),
    report_id  uuid references reports (id) on delete cascade,

    -- Eval field, Surface tier
    company_name text not null,
    ticker       text,
    -- Eval field, Located tier
    fiscal_year_end text,
    employees       text,
    total_assets    numeric,
    -- Eval field, Disambiguated tier
    revenue_most_recent_fy numeric,
    ceo_name               text,
    -- Eval field, Absence-prone tier. NULL means the model asserted absence,
    -- which is what the false-extraction rate measures. Distinct from 0.
    dividends_declared_per_share numeric,
    goodwill_impairment          numeric,

    -- Retained for the product, excluded from the v0 eval.
    sector       text,
    headquarters text,
    description  text,
    founded      text,

    created_at timestamptz default now()
);

-- Excluded from the v0 eval: set-valued fields need partial credit rules that
-- are their own design problem. likelihood/impact were removed in 002 — a
-- filing states risks, it does not rate them.
create table if not exists risks (
    id            uuid primary key default gen_random_uuid(),
    extraction_id uuid references extractions (id) on delete cascade,
    risk_name     text,
    description   text,
    mitigation    text
);

create table if not exists management (
    id            uuid primary key default gen_random_uuid(),
    extraction_id uuid references extractions (id) on delete cascade,
    name          text,
    title         text,
    tenure        text,
    background    text
);

create index if not exists extractions_report_id_idx    on extractions (report_id);
create index if not exists risks_extraction_id_idx      on risks (extraction_id);
create index if not exists management_extraction_id_idx on management (extraction_id);

-- Phase 3 retrieval. One row per chunk, both indexes over the same `text`
-- column. Added by migrations/003_chunks_and_retrieval_indexes.sql; see that
-- file for why each parameter is what it is, and EVALUATION-SPEC.md for the
-- pre-registration that fixed them on 2026-08-19 before either index existed.
--
-- The `vector` type resolves from either schema pgvector may have been
-- installed into. `set local` reverts at the end of the transaction.
set local search_path = public, extensions;

create extension if not exists vector;

-- Columns mirror the fields scripts/build_chunks.py writes to
-- <data>/chunks/chunks.jsonl, under the same names. The rows are data and are
-- never committed: 11,621 of them holding 22.9 MB of filing text.
create table if not exists chunks (
    chunk_id   text primary key,

    accession  text not null,
    ticker     text not null,
    period     text not null,
    -- Empty string, never NULL, for the front matter and post-signature tail.
    -- An empty item label is a fact about the chunk, not missing information.
    item       text not null default '',
    title      text not null default '',

    chunk_index integer not null,
    first_page  integer not null,
    last_page   integer not null,
    tokens      integer not null,

    text text not null,

    -- text-embedding-3-small's native width, pre-registered. Nullable because
    -- the load is two-phase; the loader refuses to finish on any NULL.
    embedding vector(1536),

    -- Generated, so it cannot drift from `text`. The explicit 'english'
    -- regconfig is what makes the expression immutable, which a generated
    -- column requires. "text" is quoted: the column shares its name with a type.
    tsv tsvector generated always as (to_tsvector('english', "text")) stored
);

create index if not exists chunks_tsv_idx on chunks using gin (tsv);
create index if not exists chunks_embedding_idx
    on chunks using hnsw (embedding vector_cosine_ops);
create index if not exists chunks_accession_idx on chunks (accession);

-- RLS is ON for this table and OFF for every other one, which is a deliberate
-- deviation from the note at the top of this file. 11,621 rows of filing text
-- is a different exposure from a few dozen extraction rows, and the service
-- role key the backend uses bypasses RLS regardless, so nothing in the
-- pipeline changes. No policy is created: anon sees an empty table.
alter table chunks enable row level security;
