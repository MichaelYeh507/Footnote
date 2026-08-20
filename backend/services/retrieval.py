"""The three arms: sparse, dense, and their fusion.

Every parameter in this module was pre-registered in `EVALUATION-SPEC.md` on
2026-08-19 -- before either index existed, before a single vector was computed
and before any query was written. Each one moves recall@k, so one chosen after
seeing recall@k would be a dial rather than a decision. **None of them may be
adjusted once a retrieval number exists.** The constants are named and gathered
at the top of this file so that a change to any of them is one line in a diff
rather than a literal buried in a query.

**Sparse.** `tsvector` configuration `english` over the chunk's `text`, stored
as a generated column so it cannot drift from what the dense arm embeds. The
query becomes an **OR** of its lexemes: `plainto_tsquery` ANDs every term, and
against a twelve-word conceptual query over 512-token passages the modal
outcome of an AND is zero rows -- which would publish a parser artifact as a
property of lexical retrieval. Ranked by `ts_rank_cd` at normalization 0: cover
density, no length normalization, because the chunker already fixed length at a
512-token target with a measured median of 460.

*Direction of effect, stated in the pre-registration and repeated here:* OR can
only raise the sparse arm's recall relative to AND. It moves the number in the
**baseline's** favour, against the hybrid arm this project is building.

**Dense.** `text-embedding-3-small` at its native 1,536 dimensions, cosine
distance against an HNSW index built `vector_cosine_ops`, `hnsw.ef_search = 100`
at query time. The query is embedded **exactly as the chunks were** -- the bare
text, no title, no ticker, no instruction prefix. `text-embedding-3-*` is
symmetric, so there is no asymmetric-prefix convention to follow, and a prefix
on one side would make the comparison measure the prefix.

**Hybrid.** RRF at k = 60 over the top 50 of each arm, in `services/fusion.py`.

**Why the lexemes are extracted rather than the operator replaced.** The obvious
implementation of "OR instead of AND" is a text substitution of `&` for `|` on
`plainto_tsquery(...)::text`. It is wrong, and wrong silently: the default
parser emits `url`, `file` and `email` tokens verbatim, so
`http://x.com/a?b=1&c=2` becomes the single lexeme `x.com/a?b=1&c=2` -- one
`&` inside a lexeme, which a substitution splits into two nonsense terms with no
error anywhere. The lexemes are therefore pulled out of tsquery's own output
form, where they are single-quoted with internal quotes doubled, and re-wrapped
in the same form. The result is cast `::tsquery` rather than passed through
`to_tsquery`, because `to_tsquery` re-runs the dictionaries and would stem
already-stemmed lexemes a second time.

**Why `ef_search` is set on every dense search** rather than once at connection
setup: a parameter applied once, somewhere else, is a parameter that one caller
eventually runs without -- and `hnsw.ef_search` is a pgvector GUC, so before
pgvector's library is loaded into the backend Postgres does not recognise the
name at all and will accept a `SET` as a placeholder that never takes effect.
`set_ef_search` therefore reads the value back and refuses a disagreement. The
cost of getting this wrong is not an error: it is fewer candidates, quietly,
which is exactly what the pre-registration says 100 exists to prevent.
"""

import pathlib
import re

import openai
from dotenv import dotenv_values

from services import fusion

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# PRE-REGISTERED 2026-08-19. Read the module docstring before changing any of
# these, and do not change any of them at all once an arm has run.
# ---------------------------------------------------------------------------

TS_CONFIG = "english"

# `ts_rank_cd`'s normalization flag. 0 is "no normalization". Not cosmetic: at
# flag 1 the same query returns a different ranking, because flag 1 divides by
# document length -- a correction the chunker already made by construction.
RANK_NORMALIZATION = 0

# The depth each arm retrieves to, and the depth fusion consumes. One constant
# for both: an arm retrieving deeper than the fusion it feeds would discard the
# surplus without a word.
DEPTH = fusion.FUSION_DEPTH

# pgvector's query-time search width. Twice the depth, so the requested 50 is
# never served from a candidate pool that ran out.
EF_SEARCH = 100

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIMENSIONS = 1536


# The lexemes of `plainto_tsquery`, re-joined with `|`.
#
# `regexp_matches(..., 'g')` with `WITH ORDINALITY` rather than a bare
# set-returning call in the select list: `string_agg` over an unordered
# subquery has no defined order, and the built tsquery is recorded in the
# provenance file, so it has to be the same string every run.
#
# Group 1 is captured from tsquery's own output, where a lexeme's internal
# single quotes are already doubled, so re-wrapping it in single quotes
# reproduces valid tsquery syntax with no un-escaping step to get wrong.
#
# `nullif(..., '')` turns "no lexemes at all" into SQL NULL, which Python reads
# as None. An all-stopword query is an ordinary outcome, not an error.
TSQUERY_SQL = f"""
with lexemes as (
    select m[1] as lexeme, ord
    from regexp_matches(plainto_tsquery('{TS_CONFIG}', %(text)s)::text,
                        $re$'((?:[^']|'')*)'$re$, 'g')
         with ordinality as t(m, ord)
)
select nullif(string_agg($q$'$q$ || lexeme || $q$'$q$, ' | ' order by ord),
              '')::tsquery::text
from lexemes
"""

# The tie-break is in SQL, and it is not a formality: measured on the live
# store, the top 50 for `goodwill | impairment | charge` holds twelve distinct
# `ts_rank_cd` scores and rank 1 is itself a two-way tie. Ordering by score
# alone would leave recall@1 to physical row order.
SPARSE_SQL = f"""
select chunk_id, ts_rank_cd(tsv, %(tsquery)s::tsquery, {RANK_NORMALIZATION})
       as score
from chunks
where tsv @@ %(tsquery)s::tsquery
order by score desc, chunk_id asc
limit %(depth)s
"""

# `order by distance, chunk_id` plans as an incremental sort over the HNSW
# index scan: the index supplies rows in distance order and the second key
# orders within each tie group, so the tie-break is applied where ties actually
# occur -- 448 chunks of the store are byte-identical to a chunk elsewhere and
# embed to distance exactly 0.
DENSE_SQL = """
select chunk_id, embedding <=> %(vector)s::vector as distance
from chunks
order by distance, chunk_id
limit %(depth)s
"""

_TSQUERY_LEXEME = re.compile(r"'((?:[^']|'')*)'")


def or_tsquery(cursor, text: str):
    """The query's lexemes, OR-ed, as tsquery text. None if it has none.

    Postgres does the parsing and stemming, because the arm has to see exactly
    the lexemes the index was built from -- a Python re-implementation of the
    `english` configuration would agree with it until the day it did not.
    """
    if not str(text).strip():
        raise ValueError(
            "query text is empty. An empty query has no lexemes, and an arm "
            "that returned nothing for one would score a query-set defect as "
            "a retrieval miss.")
    cursor.execute(TSQUERY_SQL, {"text": text})
    return cursor.fetchone()[0]


def sparse_search(cursor, tsquery, depth: int = DEPTH):
    """(chunk_id, ts_rank_cd score) pairs, best first, at most `depth`.

    A `None` tsquery returns an empty list without issuing a statement.
    `tsv @@ NULL` is NULL for every row, so the query would scan the table to
    produce the same nothing, and the empty ranking is an ordinary outcome that
    RRF is built to be handed.
    """
    _check_depth(depth)
    if tsquery is None:
        return []
    cursor.execute(SPARSE_SQL, {"tsquery": tsquery, "depth": depth})
    return [(row[0], row[1]) for row in cursor.fetchall()]


def set_ef_search(cursor, value: int = EF_SEARCH) -> int:
    """Apply `hnsw.ef_search` and confirm the server took it.

    `SET` accepts no parameters, so the value is formatted into the statement;
    it is checked to be a plain positive int first, and `bool` is excluded
    explicitly because `isinstance(True, int)` is True and `SET ... = True`
    would be a different statement than anyone intended.

    The read-back is the point. Postgres accepts `SET` on an unrecognised
    prefixed name as a placeholder, so a `SET` issued before pgvector's library
    is loaded can succeed and never take effect.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"ef_search must be a plain int, got {type(value).__name__}. It is "
            f"formatted into a SET statement, which takes no parameters.")
    if value < 1:
        raise ValueError(f"ef_search must be positive, got {value}")
    cursor.execute(f"set hnsw.ef_search = {value}")
    cursor.execute("show hnsw.ef_search")
    applied = int(cursor.fetchone()[0])
    if applied != value:
        raise RuntimeError(
            f"asked for hnsw.ef_search = {value}, the server reports "
            f"{applied}. pgvector's GUC is only real once its library is "
            f"loaded; until then a SET is accepted as a placeholder and never "
            f"applied, and the dense arm would run at the server default with "
            f"nothing in any output to say so.")
    return applied


def dense_search(cursor, vector, depth: int = DEPTH,
                 ef_search: int = EF_SEARCH):
    """(chunk_id, cosine distance) pairs, nearest first, at most `depth`.

    Sets `ef_search` on every call. See the module docstring for why it is not
    hoisted to connection setup.
    """
    _check_depth(depth)
    values = list(vector)
    if len(values) != EMBED_DIMENSIONS:
        raise ValueError(
            f"query vector has {len(values)} dimensions, the column is "
            f"vector({EMBED_DIMENSIONS}). A width mismatch means the query was "
            f"embedded by a different model than the chunks were.")
    set_ef_search(cursor, ef_search)
    cursor.execute(DENSE_SQL,
                   {"vector": to_pgvector(values), "depth": depth})
    return [(row[0], row[1]) for row in cursor.fetchall()]


def to_pgvector(values) -> str:
    """pgvector's text input format: a bracketed, comma-separated list.

    The same rendering `scripts/embed_chunks.py` used to store the vectors, so
    a query vector and a stored vector are written the same way.
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def embedding_client(env_file: pathlib.Path | None = None):
    """An OpenAI client built from `backend/.env`, never from `os.environ`.

    The same choice `scripts/embed_chunks.py` made, for the same reason:
    `tests/conftest.py` seeds a dummy `OPENAI_API_KEY` so that importing an
    app module cannot reach a live service. Reading the environment here would
    let a real run pick up that dummy and fail with an authentication error
    that looks like a credentials problem and is not one.
    """
    path = BACKEND / ".env" if env_file is None else env_file
    key = (dotenv_values(path).get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            f"no OPENAI_API_KEY in {path}. The dense arm has to embed each "
            f"query with the same model the chunks were embedded with; there "
            f"is no offline path to that vector.")
    return openai.OpenAI(api_key=key)


def embed_query(client, text: str):
    """One query's embedding, under the pre-registered model and width.

    Sends the text verbatim and sends no `dimensions` parameter: the chunks
    were embedded that way, and a query embedded differently is compared
    against vectors from a different space -- which cosine distance will do
    without complaint, returning fifty rows in confident order.
    """
    if not str(text).strip():
        raise ValueError("query text is empty; there is nothing to embed")
    response = client.embeddings.create(model=EMBED_MODEL, input=[text])
    vectors = [item.embedding for item in response.data]
    if len(vectors) != 1:
        raise ValueError(
            f"embedding response holds {len(vectors)} vectors for 1 input. "
            f"Vectors are matched to inputs by position, so anything but one "
            f"means this query would be handed someone else's embedding.")
    if len(vectors[0]) != EMBED_DIMENSIONS:
        raise ValueError(
            f"embedding is {len(vectors[0])} dimensions, expected "
            f"{EMBED_DIMENSIONS}. The stored vectors are that width and the "
            f"model was pre-registered at its native size.")
    return list(vectors[0])


def hybrid(sparse_ids, dense_ids):
    """The hybrid arm: RRF over the two rankings, at the pre-registered
    constants.

    A named function rather than a bare call to `fusion`, so that "the hybrid
    arm is exactly these two arms, at k = 60, to depth 50" is written down in
    one place instead of being whatever a caller happened to pass.
    """
    return fusion.reciprocal_rank_fusion(
        [list(sparse_ids), list(dense_ids)],
        k=fusion.RRF_K, depth=fusion.FUSION_DEPTH)


def ranked_ids(results):
    """Chunk ids in rank order, from (chunk_id, score) pairs.

    Refuses a repeated id. RRF would count it once, at its best rank, so
    fusion would survive it -- but recall@k counts positions, and a ranking
    holding the same chunk twice has an effective depth below the one that was
    pre-registered. A repeat here means a statement returned a row twice.
    """
    ids = [chunk_id for chunk_id, _ in results]
    if len(set(ids)) != len(ids):
        repeated = sorted({i for i in ids if ids.count(i) > 1})
        raise ValueError(
            f"ranking holds {repeated} twice. recall@k counts positions, so a "
            f"repeated chunk makes the effective depth smaller than the "
            f"pre-registered one.")
    return ids


def _check_depth(depth: int) -> None:
    if isinstance(depth, bool) or not isinstance(depth, int) or depth < 1:
        raise ValueError(f"depth must be a positive int, got {depth!r}")
