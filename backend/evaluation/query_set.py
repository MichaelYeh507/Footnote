"""The 65-query retrieval set: its schema, its strata, and its provenance rules.

This is the input to every number Phase 5 reports. A defect here is not a bug,
it is a wrong published result, so the checks below are about *how the set was
written* as much as whether it parses.

PRE-REGISTERED, and each rule is dated in `EVALUATION-SPEC.md`:

  * **AMENDMENT 3, 2026-08-19** — 25 exact-entity + 25 conceptual + 15
    unanswerable. The original 20/20/10 would have published a Mixed row at
    n=10, whose Wilson interval is +/-22.7 points, wider than the +/-15 that
    same paragraph cited when it rejected a 30-query design.
  * **AMENDMENT 4, 2026-08-19** — a query's gold set is capped at 5 chunks,
    counted as the union across its locations. Enforced in
    `evaluation/retrieval_gold.py`.
  * **AMENDMENT 5, 2026-08-19** — a gold span appearing in another filing draws
    a non-blocking advisory, and the count of flagged queries is reported.
  * **Disclosed 2026-08-19** — retrieval output was seen for one sparse query
    before this set was written, so no query may take its gold from a
    goodwill-impairment passage in MA, DOW or WYNN. `SMOKE_TICKERS` below.

**The conceptual rule is the one that makes the stratum checkable.** A query is
conceptual if it shares **no content word** with its gold span. Without a
mechanical rule, "conceptual" gets decided query by query by whoever writes it,
which is a dial in the definition of the stratum whose number gets published.
Stemming is applied because the sparse arm stems: "impaired" and "impairment"
are the same word to `ts_rank_cd`, and calling such a query conceptual would
file a lexical query under the wrong stratum.
"""

import re
import unicodedata

# AMENDMENT 3. Mixed is deliberately absent.
STRATA = ("exact_entity", "conceptual", "unanswerable")

TARGET_COUNTS = {
    "exact_entity": 25,
    "conceptual": 25,
    "unanswerable": 15,
}

REQUIRED_FIELDS = ("query_id", "stratum", "query", "gold")

# Disclosed 2026-08-19. Sorted, so the tuple has one canonical spelling.
SMOKE_TICKERS = ("DOW", "MA", "WYNN")
SMOKE_TERMS = ("goodwill", "impair")

# Function words carry no topical signal, and counting them as shared overlap
# would classify every query as lexical. Deliberately short: this is a
# stopword list for deciding topical overlap, not for indexing.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from
by with without as is are was were be been being do does did doing have has had
having it its it's we our us they their them he she his her you your i my
what which who whom whose when where why how much many any all some no not
""".split())


def _content_words(text: str) -> set[str]:
    """Stemmed content words, lowercased, punctuation stripped.

    The stemming is deliberately crude -- strip a few English suffixes -- rather
    than a real Snowball stemmer. It only has to agree with Postgres's `english`
    configuration often enough to catch the cases a human would call overlap,
    and a dependency on a stemming library for one rule in one validator is not
    worth the footprint. It errs toward *finding* overlap, which is the
    conservative direction: it can flag a query as lexical that a stemmer would
    not, and the author then rewrites it.
    """
    words = set()
    for raw in re.findall(r"[a-z0-9']+", _fold(text).lower()):
        word = raw.strip("'")
        if not word or word in _STOPWORDS or len(word) < 3:
            continue
        words.add(_stem(word))
    return words


def _fold(text: str) -> str:
    """Curly quotes and dashes to ascii, so punctuation cannot hide overlap."""
    return "".join(
        "-" if unicodedata.category(c) == "Pd" else ("'" if c in "‘’" else c)
        for c in text
    )


def _stem(word: str) -> str:
    for suffix in ("ments", "ment", "ings", "ing", "ions", "ion", "ies",
                   "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            base = word[:-len(suffix)]
            return base[:-1] if suffix == "ies" and base.endswith("i") else base
    return word


def shares_content_word(query: str, span: str) -> bool:
    """Whether a query and its gold span share any stemmed content word."""
    return bool(_content_words(query) & _content_words(span))


def check_record(query: dict) -> list[str]:
    """Everything wrong with one query. Empty means usable."""
    problems = []
    for field in REQUIRED_FIELDS:
        if field not in query:
            problems.append(f"missing field {field!r}")
    if problems:
        return problems

    stratum = query["stratum"]
    if stratum not in STRATA:
        problems.append(f"unknown stratum {stratum!r}; expected one of {STRATA}")
    if not str(query["query"]).strip():
        problems.append("query text is empty")

    gold = query["gold"]
    if stratum == "unanswerable":
        if gold:
            problems.append(
                "an unanswerable query must carry no gold: recall is undefined "
                "for it, and gold here would silently enter the recall "
                "denominator. Abstention is scored by the QA layer instead.")
    elif not gold:
        problems.append("an answerable query needs at least one gold location")

    for index, location in enumerate(gold):
        for key in ("accession", "span"):
            if not str(location.get(key, "")).strip():
                problems.append(f"gold[{index}] is missing {key!r}")

    if stratum == "conceptual":
        for index, location in enumerate(gold):
            span = str(location.get("span", ""))
            if span and shares_content_word(str(query["query"]), span):
                shared = sorted(_content_words(str(query["query"]))
                                & _content_words(span))
                problems.append(
                    f"conceptual query shares a content word with gold[{index}]: "
                    f"{shared}. The stratum is defined by having none -- either "
                    f"paraphrase the query or file it as exact_entity.")
    return problems


def check_smoke_constraint(gold: list[dict], records: list[dict]) -> list[str]:
    """The constraint disclosed 2026-08-19, checked against the store.

    Scoped to the *topic* seen, not to the issuers wholesale: the exposure was
    one query term over five chunks, and excluding MA, DOW and WYNN entirely
    would be a larger distortion of the corpus than the one being corrected.
    """
    from evaluation.retrieval_gold import contains_span

    problems = []
    by_accession = {}
    for record in records:
        by_accession.setdefault(record["accession"], []).append(record)

    for location in gold:
        accession = location.get("accession", "")
        span = str(location.get("span", ""))
        if not span:
            continue
        for record in by_accession.get(accession, ()):
            if record.get("ticker") not in SMOKE_TICKERS:
                continue
            if not contains_span(record["text"], span):
                continue
            text = record["text"].casefold()
            if all(term in text for term in SMOKE_TERMS):
                problems.append(
                    f"gold span sits in a goodwill-impairment passage in "
                    f"{record['ticker']} ({accession}), which the 2026-08-19 "
                    f"disclosure puts off limits: sparse retrieval output for "
                    f"that topic and those issuers was seen before this set was "
                    f"written. Quote a different passage.")
                break
    return problems


def stratum_counts(queries: list[dict]) -> dict[str, int]:
    counts = {}
    for query in queries:
        counts[query.get("stratum", "?")] = \
            counts.get(query.get("stratum", "?"), 0) + 1
    return counts


def check_set(queries: list[dict]) -> list[str]:
    """Set-level problems: identity, duplication, and the stratum counts."""
    problems = []

    seen_ids, seen_text = set(), {}
    for query in queries:
        qid = query.get("query_id")
        if qid in seen_ids:
            problems.append(f"duplicate query_id {qid!r}")
        seen_ids.add(qid)

        text = " ".join(str(query.get("query", "")).lower().split())
        if text and text in seen_text:
            problems.append(
                f"duplicate query text in {seen_text[text]!r} and {qid!r}: two "
                f"identical questions are one query counted twice, which "
                f"inflates the denominator without adding evidence")
        seen_text[text] = qid

    counts = stratum_counts(queries)
    for stratum, target in TARGET_COUNTS.items():
        actual = counts.get(stratum, 0)
        if actual != target:
            problems.append(
                f"{stratum}: {actual} queries, pre-registered target is {target}")
    return problems
