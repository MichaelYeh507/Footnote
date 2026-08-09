-- Database schema — captured from the live Supabase project, not hand-written.
--
-- Every column, type, default, and constraint below was read from the running
-- database (PostgREST OpenAPI spec + information_schema), then verified against
-- it by tests/test_schema_drift.py. This file is a faithful snapshot of the
-- schema as it exists today.
--
-- Current state: after migrations/002_trim_to_sec_schema.sql.
-- Change history lives in migrations/. If you alter a table in the Supabase
-- dashboard, add a migration AND update this file in the same commit — the live
-- drift test fails otherwise.
--
-- Apply to a fresh Supabase project via Dashboard > SQL Editor, or:
--   psql "$DATABASE_URL" -f schema.sql
--
-- Order matters: reports -> extractions -> child tables (FK dependency order).
--
-- Two properties reproduced deliberately from the original project:
--   * Row Level Security is DISABLED on all tables. Safe while the backend is
--     the only client (the service role key bypasses RLS regardless). Enable it
--     before any browser-side Supabase access with the anon key.
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
