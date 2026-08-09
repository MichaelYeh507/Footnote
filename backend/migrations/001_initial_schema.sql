-- Database schema — captured from the live Supabase project, not hand-written.
--
-- Every column, type, default, and constraint below was read from the running
-- database (PostgREST OpenAPI spec + information_schema), then verified against
-- it. This file is a faithful snapshot of the schema as it exists today.
--
-- Apply to a fresh Supabase project via Dashboard > SQL Editor, or:
--   psql "$DATABASE_URL" -f schema.sql
--
-- Order matters: reports -> companies -> child tables (FK dependency order).
--
-- Notes on what is deliberately reproduced as-is:
--   * Row Level Security is DISABLED on all six tables, matching the live
--     project. Safe while the backend is the only client (the service role key
--     bypasses RLS regardless). Enable RLS before any browser-side Supabase
--     access with the anon key, or every row becomes world-readable.
--   * The only indexes are the primary keys. Postgres does not auto-index
--     foreign key columns, so report_id / company_id are unindexed even though
--     every child query filters on them. Faithful to the live database; revisit
--     if the corpus grows past a few thousand rows.

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

create table if not exists companies (
    id               uuid primary key default gen_random_uuid(),
    report_id        uuid references reports (id) on delete cascade,
    name             text not null,
    ticker           text,
    sector           text,
    headquarters     text,
    ceo              text,
    description      text,
    founded          text,
    employees        text,
    market_cap       text,
    enterprise_value text,
    rating           text,
    price_target     numeric,
    current_price    numeric,
    created_at       timestamptz default now()
);

create table if not exists financials (
    id             uuid primary key default gen_random_uuid(),
    company_id     uuid references companies (id) on delete cascade,
    fiscal_year    text not null,
    is_estimate    boolean default false,
    revenue        numeric,
    gross_profit   numeric,
    gross_margin   numeric,
    ebitda         numeric,
    ebitda_margin  numeric,
    net_income     numeric,
    eps            numeric,
    free_cash_flow numeric,
    fcf_margin     numeric,
    -- Live columns the extraction layer never populates. Frontend types declare
    -- them; models/schemas.py and the OpenAI prompt do not.
    total_debt     numeric,
    net_debt       numeric
);

create table if not exists risks (
    id          uuid primary key default gen_random_uuid(),
    company_id  uuid references companies (id) on delete cascade,
    risk_name   text,
    description text,
    -- likelihood and impact are model inferences, not values stated by source
    -- documents. Rendered in the UI as if extracted.
    likelihood  text,
    impact      text,
    mitigation  text
);

create table if not exists management (
    id         uuid primary key default gen_random_uuid(),
    company_id uuid references companies (id) on delete cascade,
    name       text,
    title      text,
    tenure     text,
    background text
);

create table if not exists valuations (
    id               uuid primary key default gen_random_uuid(),
    company_id       uuid references companies (id) on delete cascade,
    metric_name      text,
    company_value    text,
    peer_avg         text,
    -- Live column the extraction layer never populates (see financials above).
    premium_discount text
);
