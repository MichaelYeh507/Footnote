# Footnote

*Grounded question answering over SEC 10-K filings: every answer cites its
passage, every quote is checked verbatim against the filing it came from, and
every published number carries the denominator it was computed from.*

<!-- Upgrade path: dragging the demo MP4 into this spot in GitHub's web
     editor replaces the GIF with a smoother inline player. -->

<p align="center">
  <img src="docs/demo.gif" width="800" alt="A question typed into the Footnote surface, the answer streaming in with a citation, and the verbatim quote highlighted in the cited excerpt; then a question the corpus cannot answer, declined">
</p>

*Two questions, end to end: one answered with the verified quote highlighted in
the cited excerpt, and one — deliberately outside the corpus — declined. Both
run live through the measured configuration; demo answers are demonstrations,
not measurements.*

<!-- HERO SLOT 2 (optional) — a still of the answered state and one of the
     abstained state, side by side or stacked; captures of the local app at
     localhost:3000. Delete this comment if the video carries the page. -->

> **No filing text, labels, predictions, or query sets are committed**, by
> rule. The corpus rebuilds from `backend/scripts/fetch_filings.py` over the
> committed manifest's EDGAR accession numbers. The captures above are this
> project's own UI, which the same rule permits as authored assets.

---

## What it is

Footnote extracts structured fields from SEC 10-K filings, retrieves passages
from them four different ways, and answers questions with a citation and a
verbatim quote — then measures how often each of those steps actually works,
under rules published before the data they would judge existed. It runs on one
machine with a Postgres database and a browser. The models are rented
(gpt-4o-mini and text-embedding-3-small, both through APIs); the corpus
plumbing, the labeling and adjudication tools, the four retrieval arms, the
measurement harness and the answering surface are what this project is.

---

## Why I built this

<!-- MICHAEL — YOUR WORDS ONLY in this section. The Edgeball version works
     because it names your own history with the sport; this one should name
     whatever is true for you here: why SEC filings, why measurement-first,
     what you wanted to be able to say at the end that most RAG demos can't.
     2–4 short paragraphs. Delete this comment when written. -->

---

## What is built

| | |
|---|---|
| Corpus | 44 10-K filings — 22 issuers, two fiscal years, all 11 GICS sectors — drawn by a pre-registered rule from a dated S&P 500 snapshot, fetched as HTML/iXBRL by accession |
| Extraction instrument | gpt-4o-mini at temperature 0, nine fields per filing, prompt frozen by hash before any label existed |
| Gold labels | 351 fields hand-labeled in a built-in web tool that renders the real filing, every label audited against the filer's own iXBRL facts |
| Chunker | Heading-chain sections, 100.0% text coverage over 44/44 filings, 11,621 chunks, 4,890,354 tokens |
| Retrieval arms | Sparse (`tsvector` + GIN), dense (`text-embedding-3-small`, pgvector HNSW), reciprocal-rank hybrid, and a score-gated fusion |
| Query set | 65 queries (25 exact-entity, 25 conceptual, 15 unanswerable) written blind to retrieval output, frozen by SHA-256 |
| QA instrument | Context-grounded prompt byte-checked against the published spec; abstention is `"answer": null` and nothing else |
| Adjudication | Blind web tool with three tested layers of blindness; verdicts digest-frozen before scoring |
| Scoring | Wilson intervals on everything, paired comparisons for directions, reporting gate at n ≥ 25 |
| QA surface | Live retrieval through the unchanged measured instrument; verbatim-quote verification, abstention as a designed state, a guarded presentation layer that can add no fact |

**What you can do with it:**

- **Ask the filings a question** and get an answer with a citation, the model's
  quote checked verbatim against the cited excerpt, and the five passages it
  saw — or a designed decline when the corpus does not support an answer.
- **Watch it refuse**: the gated arm rejects questions its threshold was never
  measured for, naming the measured sizes, instead of improvising.
- **Reproduce every retrieval number offline** — `score_retrieval.py` needs no
  database and no API key, only the run artifacts and their hashes.
- **Rebuild the corpus from an empty directory** with the committed fetch
  script and manifest.
- **Verify the query freeze yourself**: `freeze_queries.py --verify` re-derives
  every digest; retrieval refuses to run against an edited set.
- **Read every number's workings** in [`RESULTS.md`](RESULTS.md), including the
  unflattering ones.

---

## The one thing this does differently

Every rule that moves a number was published before the data it would judge
existed, and the ordering is enforced by code and checkable in the public
history — not asserted.

```python
def refuse_unless_frozen(queries, path=None):
    """Raise unless the live set is exactly the frozen one. Returns the freeze.

    This is what every arm calls before it retrieves anything. The freeze is
    only worth having if something enforces it, and a check each arm has to
    remember to write for itself is a check one arm will eventually skip.
    """
```

The retrieval parameters and the definition of a hit were pushed publicly
before either index existed. The Phase 4/5 rules — the conditioned split, what
counts as an abstention, how a duplicate-span citation scores — were pushed
before any QA output existed. When the fourth retrieval arm was designed
*after* seeing the first three arms' results, it was pre-registered as post-hoc
in its own dated commit before a line of it was written, and its numbers carry
that label everywhere they appear, including in this file.

---

## Measured results

All rules and amendments in [`EVALUATION-SPEC.md`](EVALUATION-SPEC.md); full
tables with every cell and caveat in [`RESULTS.md`](RESULTS.md). 95% Wilson
intervals throughout. ‡ marks the post-hoc arm; † marks cells below the n ≥ 25
reporting gate, shown as counts.

| Metric | Value | 95% interval | n |
|---|---|---|---|
| Extraction accuracy, pooled over nine fields | **0.929** | 0.897 to 0.951 | 326 / 351 |
| False extraction on absent fields | **0.333** | 0.214 to 0.479 | 15 / 45 |
| Extractor abstentions | **0** | — | 0 / 351 |
| recall@5, best blind arm (dense) | **0.440** | 0.312 to 0.577 | 22 / 50 |
| recall@5, gated arm ‡ | **0.500** | 0.366 to 0.634 | 25 / 50 |
| recall@1, every arm | **0.100** | 0.043 to 0.214 | 5 / 50 |
| QA abstention on unanswerable questions, every arm | **1.000** | 0.796 to 1.000 | 15 / 15, ×4 |
| Grounded accuracy given gold in top-5, gated ‡ | **0.200** | 0.089 to 0.391 | 5 / 25 |
| End-to-end grounded-correct, all four arms | **0.080–0.120** | — | 4–6 / 50 |

- **The thesis cell is the abstention row.** The ungrounded extractor returned
  a value on all 351 instances — zero abstentions — and invented values on 15
  of 45 absent fields. The grounded pipeline, handed 60 opportunities to
  invent across four arms, declined all 60.
- **The pre-registered prediction failed and is published as-is**: hybrid was
  predicted to win overall and instead loses to dense (paired 0.333
  [0.138, 0.609] at k=5).
- **The gated arm ‡ fixed what it targeted** (beats hybrid, pooled 0.889
  [0.565, 0.980]) **but gated > dense was not established** — even though
  gated loses zero queries to dense, the paired interval misses by 0.061.
- **No direction was established between any arms on grounded accuracy** —
  all six paired intervals straddle 0.5 at n = 50.
- Grounded accuracy given gold by arm: sparse 5/10 †, dense 4/22 †, hybrid
  6/18 †, gated 5/25 ‡ — the model converts in-context chances at different
  rates per arm, and its failures are dominated by dropped thousands/millions
  scale factors.
- **Cost at volume**: one full extraction pass is 3,281,258 input tokens; the
  entire 195-call QA run cost about $0.09.
- **Tests**: 1,536 passed, 0 skipped, written red-first alongside the code
  that produces numbers, with guard tests perturbed to prove they can fail.

---

## Known limitations

The short list, worst first. The measurement behind each is in
[`RESULTS.md`](RESULTS.md).

- **Retrieval is the ceiling, and it is low.** The best arm puts a gold
  passage in the top 5 half the time (0.500 ‡, post-hoc; best blind 0.440),
  and top-1 is 0.100 for every arm. End-to-end grounded-correct is
  0.080–0.120 because of it.
- **When retrieval succeeds, the model still misses most answers.** Given gold
  in context, grounded accuracy is 0.18–0.50 across arms (three of four cells
  below the reporting gate), with scale errors the dominant failure.
- **The best-looking retrieval number is post-hoc.** The gated arm was
  designed after the first three arms' results, on the same 65 queries. It is
  a hypothesis the data suggested, not a confirmation, and gated > dense is
  not established.
- **Wrong-year citations happen with the fiscal label in view**: on flagged
  duplicate-span queries, dense cited the right passage from the wrong year 4
  times (gated 4, hybrid 1, sparse 0).
- **Answers can be right for reasons the gold set can't credit**: gold spans
  are accession-scoped, and dense answered correctly from non-gold passages 5
  times — reported on its own line, not folded in.
- **One model, one corpus, one configuration.** gpt-4o-mini at temperature 0,
  44 filings, no reranker, no metadata filtering; nothing here says how any
  other setup behaves.
- **Live demo answers are demonstrations, not measurements** — nothing asked
  in the surface is recorded or enters any published number.

---

## Documentation

| | |
|---|---|
| [`SETUP.md`](SETUP.md) | **Start here to run it.** Backend, frontend, the QA surface's requirements, the frozen query set, and reproducing the retrieval numbers offline |
| [`RESULTS.md`](RESULTS.md) | Every published number with its denominator, interval, method and caveats — extraction, retrieval, and grounded QA |
| [`EVALUATION-SPEC.md`](EVALUATION-SPEC.md) | Every pre-registered rule and dated amendment, published before the data it governs existed |

---

## Stack

FastAPI, Python 3.12, psycopg. Supabase Postgres with a GIN index over a
generated `tsvector` column and an HNSW index over `pgvector(1536)`.
gpt-4o-mini and text-embedding-3-small through the OpenAI API. Next.js App
Router, TypeScript, Tailwind. pytest — 1,536 tests, 0 skipped.

---

## Acknowledgements

The filings are public documents served by the SEC's
[EDGAR](https://www.sec.gov/edgar) system; fetches carry a real contact
address per the SEC's fair-access policy, and issuer XBRL facts are used to
audit hand labels. The extraction and answering models are OpenAI's; nothing
here was trained. The display face is
[Source Serif 4](https://fonts.google.com/specimen/Source+Serif+4).
