-- Migration 003 — the chunk table and the two retrieval indexes.
--
-- Additive. Nothing existing is dropped or altered, so this is safe to run on a
-- database holding the Phase 2 extraction rows.
--
-- Rationale (see HYBRID-RETRIEVAL-SEC-PLAN.md §4, and the parameters
-- pre-registered 2026-08-19 and published in EVALUATION-SPEC.md): Phase 3 needs
-- one row per chunk, a GIN index over a `tsvector` for the sparse arm, and an
-- HNSW index over a pgvector column for the dense arm. Both arms read the same
-- `text` column, which is the whole reason scripts/build_chunks.py materialises
-- a store instead of letting each index parse the filings for itself.
--
-- The rows are DATA, not source. chunks.text is filing text — 22.9 MB of it
-- across 11,621 rows — and is never committed to this repo, exactly as the
-- filings, labels and predictions are not. This file creates the shape; the
-- loader fills it.
--
-- Apply via Dashboard > SQL Editor, or:
--   psql "$DATABASE_URL" -f migrations/003_chunks_and_retrieval_indexes.sql

begin;

-- Supabase's dashboard toggle installs pgvector into the `extensions` schema,
-- while a hand-run `create extension vector` lands it in `public`. Naming both
-- on the search_path resolves the `vector` type either way, so this migration
-- does not depend on which route enabled it. `set local` reverts at commit.
set local search_path = public, extensions;

create extension if not exists vector;

-- One row per retrieval unit. The columns are exactly the fields
-- scripts/build_chunks.py writes to <data>/chunks/chunks.jsonl, under the same
-- names: the loader is then a straight mapping, and a rename is one of the
-- places a silent mismatch could hide.
create table if not exists chunks (
    -- The 16-hex truncated sha256 of (accession|item|index) that
    -- services/chunk_assembly.py assigns. Primary key rather than a surrogate
    -- id: the store already guards its uniqueness, and making the database
    -- enforce the same thing means a re-load cannot quietly double a passage.
    chunk_id   text primary key,

    -- Citation identity. `item` and `title` are empty strings, never NULL, for
    -- the front matter and the post-signature tail — AMENDMENT 2 gives those
    -- their own sections with an empty item label deliberately, and an empty
    -- label is a fact about the chunk rather than missing information.
    accession  text not null,
    ticker     text not null,
    period     text not null,
    item       text not null default '',
    title      text not null default '',

    -- Position within the filing, and the page range the text falls on. The
    -- page is what §4 names as the attribution the QA layer cites.
    chunk_index integer not null,
    first_page  integer not null,
    last_page   integer not null,

    -- Measured with the model's own encoding by chunk_assembly.count_tokens and
    -- carried through rather than recomputed, so the store, the indexes and any
    -- later reader quote one number instead of three that might disagree.
    tokens integer not null,

    text text not null,

    -- Dense arm. 1,536 is text-embedding-3-small's native width, pre-registered
    -- 2026-08-19. Nullable because the load is two-phase — rows first, vectors
    -- second — and the loader refuses to finish while any row is still NULL.
    embedding vector(1536),

    -- Sparse arm. Generated rather than maintained by the loader so it cannot
    -- drift from `text`: there is no code path that updates one without the
    -- other. The explicit 'english' regconfig is what makes the expression
    -- immutable, which a generated column requires; the bare two-argument
    -- to_tsvector(text) depends on default_text_search_config and is rejected.
    -- "text" is quoted because the column shares its name with a type.
    tsv tsvector generated always as (to_tsvector('english', "text")) stored
);

-- Sparse index. GIN over the generated tsvector.
create index if not exists chunks_tsv_idx on chunks using gin (tsv);

-- Dense index. HNSW with pgvector's default build parameters (m=16,
-- ef_construction=64), pre-registered 2026-08-19 along with the query-time
-- hnsw.ef_search = 100.
--
-- vector_cosine_ops is not a free choice: cosine is the pre-registered distance,
-- and an index built on one opclass is silently ignored by a query written with
-- another operator — the query still returns correct rows, by sequential scan,
-- so the failure shows up as latency rather than as an error.
create index if not exists chunks_embedding_idx
    on chunks using hnsw (embedding vector_cosine_ops);

-- Per-filing lookups: the coverage checks and any per-issuer reporting filter
-- on accession. Postgres does not create this automatically.
create index if not exists chunks_accession_idx on chunks (accession);

-- Row Level Security, ON for this table only.
--
-- A deliberate deviation from the project-wide default, decided 2026-08-19 and
-- recorded rather than slipped in. schema.sql documents RLS as DISABLED on
-- every table, which was safe while the backend was the only client and each
-- table held a few dozen extraction rows. This table holds 22.9 MB of filing
-- text across 11,621 rows, and the anon key would read all of it.
--
-- Nothing in the pipeline changes: the service role key bypasses RLS, and it is
-- what services/supabase_client.py and every script use. No policy is created,
-- so anon and authenticated see an empty table rather than an error -- which is
-- why tests/test_schema_drift.py checks this by comparing what the two keys
-- return rather than by expecting a permission failure.
--
-- The content is public SEC filing text, so this is about egress rather than
-- disclosure. It costs one line and removes the question.
alter table chunks enable row level security;

commit;

-- After applying, update backend/schema.sql in the SAME commit —
-- tests/test_schema_drift.py diffs that file against the live database and
-- fails otherwise. It is a live-marked test and skips cleanly without
-- credentials.
