-- Migration 002 — trim the equity-research schema to the SEC 10-K field set.
--
-- DESTRUCTIVE. Run 001_initial_schema.sql's snapshot (backend/schema.sql) and
-- scripts/export_corpus.py first. The synthetic corpus exists only as rows in
-- this database; reports.raw_text is the sole copy of those documents.
--
-- Rationale (see HYBRID-RETRIEVAL-SEC-PLAN.md §3): rating, price_target,
-- current_price, investment_thesis, and peer valuation tables do not exist in
-- any SEC filing. They are why the original corpus had to be synthetic.
--
-- reports.raw_text and reports.structured_json are deliberately NOT touched:
-- they are the retained control corpus for the clean-vs-real gap in §5.

begin;

-- 1. valuations: peer-comparison tables are sell-side artifacts. Nothing in a
--    10-K populates them, so the table goes rather than leaving two dead columns.
drop table if exists valuations;

-- 2. financials: superseded by flat per-filing scalars below. The multi-year
--    child table made the eval unit ambiguous (one value per field per document
--    is what the Wilson math assumes).
drop table if exists financials;

-- 3. risks: likelihood and impact are model inferences rendered in the UI as
--    extracted fact. A filing states risks; it does not rate them. They have no
--    ground truth and cannot appear in an eval.
alter table risks drop column if exists likelihood;
alter table risks drop column if exists impact;

-- 4. companies -> extractions.
--    With ~40 filings from 3-4 issuers, this table holds ~10 rows per issuer.
--    "companies" invites treating those rows as entities during labeling; each
--    row is one extraction from one filing. Entity resolution is explicitly out
--    of scope (§8).
alter table companies rename to extractions;

-- Cut: sell-side fields with no counterpart in a filing.
alter table extractions drop column if exists rating;
alter table extractions drop column if exists price_target;
alter table extractions drop column if exists current_price;
-- Cut: market data, not filing data.
alter table extractions drop column if exists market_cap;
alter table extractions drop column if exists enterprise_value;

-- Align column names with the eval field names so labeling cannot drift from
-- the schema (§3 tier table).
alter table extractions rename column name to company_name;
alter table extractions rename column ceo to ceo_name;

-- Add the fields that do not exist yet. NULL means the model asserted absence
-- (the prompt instructs null when a field is not present), which is what the
-- false-extraction rate on the absence-prone fields measures.
alter table extractions add column if not exists fiscal_year_end              text;
alter table extractions add column if not exists total_assets                 numeric;
alter table extractions add column if not exists revenue_most_recent_fy       numeric;
alter table extractions add column if not exists dividends_declared_per_share numeric;
alter table extractions add column if not exists goodwill_impairment          numeric;

-- 5. The child FK columns still read company_id but now reference extractions.
--    Rename so the schema does not lie about what it points at. (Renaming a
--    table preserves its foreign keys, so the constraints themselves are fine.)
alter table risks      rename column company_id to extraction_id;
alter table management rename column company_id to extraction_id;

-- 6. Index the foreign keys. Postgres does not create these automatically, and
--    every child query filters on them. Immaterial at 3 rows, not at 40 filings
--    with chunked sections.
create index if not exists extractions_report_id_idx     on extractions (report_id);
create index if not exists risks_extraction_id_idx       on risks (extraction_id);
create index if not exists management_extraction_id_idx  on management (extraction_id);

commit;

-- Resulting nine eval fields on `extractions`:
--   Surface        company_name, ticker
--   Located        fiscal_year_end, employees, total_assets
--   Disambiguated  revenue_most_recent_fy, ceo_name
--   Absence-prone  dividends_declared_per_share, goodwill_impairment
--
-- Retained for the product but excluded from the v0 eval: description, sector,
-- headquarters, founded, risks[], management[].
