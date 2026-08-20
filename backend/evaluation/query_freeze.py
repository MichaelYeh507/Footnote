"""Freezing the 65-query retrieval set: one hash per query, one digest over them.

Until this module existed, "all 65 are approved" rested on reading line
positions in an append-only decision log. That is inference, not verification,
and it has two holes a reader cannot see:

  * The log records `query_id` and `verdict` and **no query text**. A verdict
    therefore goes stale silently when its query changes. It did, twice -- q009
    and q030 were re-decided after their text moved, and nothing but a person
    remembering stood between the stale `approved` and the new text.
  * Nothing detects an edit made *after* review. The set is the input to every
    number Phase 3 publishes, so a query edited afterwards would change what
    was measured without changing anything a reader could check.

The freeze closes both. Every query gets a sha256 over its own canonical bytes;
the set gets one digest over those hashes; both are written to a file that is
**committed**, dated and pushed before any retrieval number exists. Any later
edit to any query moves that query's hash, moves the set digest, and makes
`verify` fail by name.

**What the hash covers: the whole record, not a chosen subset.** Picking fields
would create a class of edit the freeze cannot see -- change a `ticker` the hash
ignores and the card the reviewer read is no longer the card on disk, with every
hash still matching. The cost is that adding any field later breaks the freeze,
and that is correct: it is a change to the set after the freeze, which is the
event this file exists to make loud.

**The canonical form, written out so it cannot drift:**

    json.dumps(query, sort_keys=True, ensure_ascii=False,
               separators=(",", ":")).encode("utf-8")

Keys are sorted, so re-serialising the same content is stable. List order is
**not** sorted, so reordering a query's gold locations is a change. Nothing else
is touched -- in particular **no normalization is applied to the span**. The
gold span is verbatim filing text; a freeze that folded whitespace or case would
accept an edit while the published normalization is applied at scoring time to a
copy, never to the stored span.

**The set digest, likewise:**

    "".join(query_id + "  " + sha256 + "\n" for entries sorted by query_id)

sha256 of those bytes: ids ascending, two spaces, newline-terminated. Sorting by
id rather than using file order means reordering the lines of `queries.jsonl` is
not a change to the set -- the set is keyed by `query_id`, and so is scoring --
while adding, removing or editing any query is.

**`file_sha256` is provenance, not the guard.** It is recorded because the label
freeze recorded one and the audit trail should read the same way, but it moves
on a line reorder that changes no query. The digest is what refuses.

This module adds no scoring rule and moves no number. It records which 65
queries the published numbers will be computed over, before they are computed.
"""

import hashlib
import json
import pathlib

MANIFEST_VERSION = 1

# The freeze is **committed**, unlike the query set it describes. It carries
# query ids, strata, accessions, items and hashes -- identifiers, never text --
# which is the same rule that lets `corpus/manifest.json` commit a sha256 for
# every filing while no filing enters the repo. Committing it is the point: the
# claim this project makes is that the set was fixed before any number existed,
# and only a pushed commit dates that.
FREEZE_PATH = (pathlib.Path(__file__).resolve().parent.parent
               / "corpus" / "query-set-freeze.json")

# A verdict is cast against text, so the text must be recoverable from the
# record. Decisions written before 2026-08-20 carry no hash; see
# `check_approvals`, which treats that as a gap rather than as a pass.
DECISION_HASH_FIELD = "query_sha256"


def canonical_bytes(query: dict) -> bytes:
    """The exact bytes a query's hash is taken over. See the module docstring."""
    return json.dumps(query, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def query_sha256(query: dict) -> str:
    """The hash of one query record.

    Refuses anything that is not a whole record. The hash is what binds a
    verdict to its text, so a caller holding only a `query_id` must not be able
    to produce one -- that would put an unbindable hash in the decision log and
    make the freeze report a binding that does not exist.
    """
    if not isinstance(query, dict):
        raise TypeError(
            f"a query record is a dict, got {type(query).__name__}. Pass the "
            f"whole record: the hash is what binds a verdict to its text, so "
            f"it cannot be computed from a query_id alone.")
    if not str(query.get("query_id", "")).strip():
        raise ValueError("query record has no query_id; the manifest is keyed "
                         "by it and an unkeyed entry would vanish on read.")
    return hashlib.sha256(canonical_bytes(query)).hexdigest()


def manifest_entries(queries: list[dict]) -> list[dict]:
    """One entry per query, sorted by `query_id`.

    `accessions` and `items` are carried for readability and for the
    composition recount. They are already inside `sha256`, which is taken over
    the whole record, so they add nothing to what the digest can detect.

    Refuses duplicate ids rather than letting one entry shadow another: the
    digest would still be well formed, and `verify` -- which keys by id --
    would compare one of the two and never mention the other.
    """
    entries = []
    seen = set()
    for query in queries:
        qid = query["query_id"]
        if qid in seen:
            raise ValueError(
                f"duplicate query_id {qid!r}. Two entries under one key make "
                f"the manifest ambiguous about which text was frozen.")
        seen.add(qid)
        gold = query.get("gold") or []
        entries.append({
            "query_id": qid,
            "stratum": query.get("stratum"),
            "accessions": [g.get("accession") for g in gold],
            "items": [g.get("item") for g in gold],
            "sha256": query_sha256(query),
        })
    return sorted(entries, key=lambda entry: entry["query_id"])


def digest_input(entries: list[dict]) -> bytes:
    """The bytes the set digest is taken over. Sorted, so file order is free."""
    ordered = sorted(entries, key=lambda entry: entry["query_id"])
    return "".join(entry["query_id"] + "  " + entry["sha256"] + "\n"
                   for entry in ordered).encode("utf-8")


def set_digest(entries: list[dict]) -> str:
    return hashlib.sha256(digest_input(entries)).hexdigest()


def composition(queries: list[dict]) -> dict:
    """What the set is made of, counted so it can be reported with denominators.

    Recorded in the freeze because section 5 and AMENDMENT 5 both require parts
    of it to be reported alongside the results, and a composition dated before
    any number exists is a stated fact rather than a description written
    afterwards to match one.
    """
    strata, items = {}, {}
    accessions, tickers = set(), set()
    answerable = 0
    for query in queries:
        stratum = query.get("stratum")
        strata[stratum] = strata.get(stratum, 0) + 1
        gold = query.get("gold") or []
        if gold:
            answerable += 1
        for location in gold:
            accessions.add(location.get("accession"))
            tickers.add(location.get("ticker"))
            item = location.get("item") or "front matter"
            items[item] = items.get(item, 0) + 1
    return {
        "queries": len(queries),
        "answerable": answerable,
        "strata": dict(sorted(strata.items())),
        "distinct_accessions": len(accessions),
        "distinct_tickers": len(tickers),
        "gold_locations": sum(items.values()),
        "items": dict(sorted(items.items())),
    }


def check_approvals(queries: list[dict], decisions: dict) -> dict:
    """Whether every query is approved, and whether each approval binds to text.

    Two different things, kept apart on purpose:

      **problems** -- a query with no decision, a rejected query, or an approval
      whose recorded hash does not match the query as it now stands. Each of
      these refuses the freeze.

      **unbound** -- an approval carrying no hash at all. Not an error and not a
      pass: the decision log predates hash recording, so for those verdicts the
      binding to text is simply not in the record, and no amount of reading the
      log will put it there. The freeze requires a dated human attestation to
      cover them, and records that it is an attestation.

    `decisions` is the collapsed map `scripts/review_queries.py` produces:
    query_id -> the latest decision for it.
    """
    problems, unbound, bound = [], [], []
    for query in queries:
        qid = query["query_id"]
        decision = decisions.get(qid)
        if decision is None:
            problems.append(f"{qid}: no decision in the log")
            continue
        verdict = decision.get("verdict")
        if verdict != "approved":
            problems.append(f"{qid}: verdict is {verdict!r}, not 'approved'")
            continue
        recorded = decision.get(DECISION_HASH_FIELD)
        if not recorded:
            unbound.append(qid)
            continue
        current = query_sha256(query)
        if recorded != current:
            problems.append(
                f"{qid}: approved against different text. The decision records "
                f"{recorded[:12]}..., the query now hashes to {current[:12]}.... "
                f"Re-review it; an approval does not survive an edit.")
            continue
        bound.append(qid)
    return {"problems": problems, "unbound": sorted(unbound),
            "bound": sorted(bound), "decided": len(decisions)}


def build_freeze(queries: list[dict], *, frozen_at: str, file_sha256: str,
                 approvals: dict, attestation: dict | None = None,
                 duplicate_span_advisories: int | None = None) -> dict:
    """The freeze record. Pure: every input is passed in, nothing is read."""
    entries = manifest_entries(queries)
    record = {
        "manifest_version": MANIFEST_VERSION,
        "frozen_at": frozen_at,
        "set_sha256": set_digest(entries),
        "file_sha256": file_sha256,
        "composition": composition(queries),
        "approvals": dict(approvals),
        "queries": entries,
    }
    if duplicate_span_advisories is not None:
        record["composition"]["duplicate_span_advisories"] = \
            duplicate_span_advisories
    if attestation is not None:
        record["approvals"]["attestation"] = attestation
    return record


def verify(queries: list[dict], freeze: dict) -> list[str]:
    """Everything that differs between the live set and the freeze. Empty is ok.

    Names the query that moved rather than reporting a digest mismatch, because
    "the set changed" sends a reader to diff 65 records by hand while "q030
    changed" sends them to one.

    Checks the freeze file against **itself** first. A hand-edited freeze is the
    one failure that would otherwise pass every comparison below: edit a query
    in `queries.jsonl` and its row here to match, and only the stored digest
    still disagrees.
    """
    problems = []

    version = freeze.get("manifest_version")
    if version != MANIFEST_VERSION:
        problems.append(
            f"freeze manifest_version is {version!r}, this module writes "
            f"{MANIFEST_VERSION}. The canonical form may have changed, which "
            f"would make every hash below incomparable.")
        return problems

    frozen_entries = freeze.get("queries") or []
    recomputed = set_digest(frozen_entries)
    if recomputed != freeze.get("set_sha256"):
        problems.append(
            f"the freeze file disagrees with itself: its rows digest to "
            f"{recomputed[:12]}..., it records "
            f"{str(freeze.get('set_sha256'))[:12]}.... The file has been "
            f"edited by hand.")

    frozen_by_id = {entry["query_id"]: entry for entry in frozen_entries}
    live_by_id = {query["query_id"]: query for query in queries}

    for qid in sorted(set(live_by_id) - set(frozen_by_id)):
        problems.append(f"{qid} is in the query set but was never frozen")
    for qid in sorted(set(frozen_by_id) - set(live_by_id)):
        problems.append(f"{qid} was frozen but is no longer in the query set")

    for qid in sorted(set(live_by_id) & set(frozen_by_id)):
        current = query_sha256(live_by_id[qid])
        if current != frozen_by_id[qid]["sha256"]:
            problems.append(
                f"{qid} has changed since the freeze: frozen "
                f"{frozen_by_id[qid]['sha256'][:12]}..., now {current[:12]}....")

    # Only when every hash matches, because otherwise this restates the finding
    # above in a form nobody can act on.
    live_composition = composition(queries)
    frozen_composition = dict(freeze.get("composition") or {})
    frozen_composition.pop("duplicate_span_advisories", None)
    if not problems and live_composition != frozen_composition:
        problems.append(
            f"composition differs from the freeze though every hash matches, "
            f"which means the freeze file's composition block was edited: "
            f"frozen {frozen_composition}, live {live_composition}")
    return problems


def load_freeze(path: pathlib.Path | None = None) -> dict:
    """The committed freeze record.

    Raises rather than returning `{}` on a missing file. "Not frozen yet" and
    "frozen and unchanged" must not have the same shape at a call site, or an
    arm run before the freeze would report a clean verification it never did.
    """
    path = FREEZE_PATH if path is None else path
    if not path.exists():
        raise FileNotFoundError(
            f"no freeze at {path}. The query set has not been frozen; run "
            f"`python scripts/freeze_queries.py --check` to see what is "
            f"outstanding. No arm may run against an unfrozen set.")
    return json.loads(path.read_text(encoding="utf-8"))


def refuse_unless_frozen(queries: list[dict],
                         path: pathlib.Path | None = None) -> dict:
    """Raise unless the live set is exactly the frozen one. Returns the freeze.

    This is what every arm calls before it retrieves anything. The freeze is
    only worth having if something enforces it, and a check each arm has to
    remember to write for itself is a check one arm will eventually skip.
    """
    freeze = load_freeze(path)
    problems = verify(queries, freeze)
    if problems:
        raise RuntimeError(
            "the query set no longer matches the freeze, so any number "
            "computed from it would not be a number over the reviewed set:\n  "
            + "\n  ".join(problems))
    return freeze
