# Results

Two measurements, reported in the order they were made. **Phase 2 —
extraction accuracy**, measured 2026-08-18, runs from *What was measured*
below through *Reproduction*; that text is unchanged from its original
publication and no number in it has been recomputed or restated. **Phase 3 —
retrieval**, measured 2026-08-20, follows under its own heading and carries
its own limitations and reproduction notes.

Measured 2026-08-18. This document reports outcomes. Every rule that moves a
number here — issuer selection, the label schema, the matching spec, the
outcome grid, the false-extraction denominator, the n≥25 reporting gate — was
pre-registered before the data existed. The registered spec — matching rules,
outcome grid, labeling protocol, amendments, resolutions, and instrument
disclosures, each dated — is published in `EVALUATION-SPEC.md`, extracted
from the project's internal plan (see its header for provenance and the one
redaction). Nothing in this document
adjusts a registered metric after the fact; where a number benefits from
context, the context is reported beside it as composition, never as a
recomputed rate.

## What was measured

One extraction pass of `gpt-4o-mini` (temperature 0.0; prompt frozen
2026-08-09, sha256 `afb6602be0d7…` over prompt + model + temperature) across
the 39 in-window filings of the 44-filing corpus (22 issuers × 2 fiscal
years, two per GICS sector, drawn by a rule pre-registered before any filing
was fetched; seed `20260809`; the selection rule lives in the internal plan's
§2). The 5 over-window filings are excluded from the denominator rather than
truncated — chunking is a later phase, and window coverage (39 of 44) is
reported as its own number rather than hidden inside this one. Nine fields
per filing → **351 instances**.

Ground truth is 351 hand labels, frozen before unblinding at sha256
`ad155ddd…` on 2026-08-18. The scoring run (`backend/scripts/score_predictions.py`
in this repository) verified the label bytes against that hash, validated that
labels and predictions each cover the manifest-derived grid exactly once per
(accession, field) — zero gaps — and joined the two by key, never by file
position. No label was edited after unblinding. Intervals are 95% Wilson.
The reporting gate is n ≥ 25 for any per-field rate; every field here is n=39.

## The finding

**The model abstained on 0 of 351 instances.** It returned a value every
time, on every field, on every filing — including the twelve instances where
the fetched document verifiably does not state the answer. Every failure mode
in this run traces to that one behavior: where the filing states a figure,
the model is usually right; where the filing states "none," the model's
zero-default usually coincides with the truth; where the filing is silent,
the model invents — a zero, a number from the wrong concept, or a number that
appears nowhere in the document at all. The cover-page fields are solved.
The money is in what the model does with absence.

## Five outcomes, kept separate (351 instances)

| outcome | count |
|---|---|
| correct | 326 |
| wrong value | 10 |
| missed (null on a present field) | 0 |
| false extraction (value on an absent field) | 15 |
| correct abstention | 0 |

Missed and correct-abstention are zero for the same reason: the model never
returns null. A null is cheap and a confident wrong number is expensive;
collapsing these five into one error bucket — as most extraction evals do —
would hide everything this section reports.

## Per-field accuracy (n=39 each, 95% Wilson)

| field | correct/n | accuracy | 95% CI | C/WV/M/FE/CA |
|---|---|---|---|---|
| company_name | 39/39 | 1.000 | [0.910, 1.000] | 39/0/0/0/0 |
| ticker | 39/39 | 1.000 | [0.910, 1.000] | 39/0/0/0/0 |
| fiscal_year_end | 39/39 | 1.000 | [0.910, 1.000] | 39/0/0/0/0 |
| employees | 39/39 | 1.000 | [0.910, 1.000] | 39/0/0/0/0 |
| ceo_name | 39/39 | 1.000 | [0.910, 1.000] | 39/0/0/0/0 |
| revenue_most_recent_fy | 36/39 | 0.923 | [0.797, 0.973] | 36/1/0/2/0 |
| total_assets | 36/39 | 0.923 | [0.797, 0.973] | 36/1/0/2/0 |
| dividends_declared_per_share | 30/39 | 0.769 | [0.617, 0.874] | 30/7/0/2/0 |
| goodwill_impairment | 29/39 | 0.744 | [0.589, 0.854] | 29/1/0/9/0 |

A 39/39 field reports [0.910, 1.000], not certainty — thirty-nine
observations bound a rate, they do not prove one.

## Overall

- **Pooled (instance-weighted): 326/351 = 0.929 [0.897, 0.951]**
- **Population-weighted (per-field mean over 9 fields): 0.929** — identical
  to pooled because the grid gives every field the same n by construction;
  the divergence the spec told us to watch for cannot occur on a complete grid.
- **Ambiguous instances** (13 of 351 carry the labeler's flag, mostly
  computed or paid-only dividend figures under RESOLUTIONS 1–2):
  - included — the pooled figure above;
  - excluded — 317/338 = 0.938 [0.907, 0.959]. Nine of the thirteen scored
    correct, so exclusion moves the headline by less than a point.

## False-extraction rate

**15/45 = 0.333 [0.214, 0.479]**, on the denominator settled 2026-08-09:
instances labeled `stated_none` (33) plus `not_addressed` (12).

**DISCLOSED CONTAMINATION (`EVALUATION-SPEC.md`, attached to this number as required).** Before
labeling began, the labeler learned one aggregate fact about the predictions:
the model returned a non-null value on all 351 instances. The direction of
any resulting label bias is toward the model — it can only shrink this
denominator and flatter this rate. A reader who discounts this number
entirely is making a defensible choice.

**Composition, reported as counts.** The pooled rate spans two failures that
share nothing but a bucket:

- **`not_addressed` (12 instances): all 12 were false extractions.** Null is
  the only correct answer on these, and this model returns null zero times in
  351 tries — so these outcomes were entailed by the model's behavior before
  any specific document was read. This stratum is n=12, under the reporting
  gate, so no rate-with-interval is claimed for it; the count is a logical
  consequence, not an estimate.
- **`stated_none` (33 instances): 3 false extractions — 3/33 = 0.091
  [0.031, 0.236].** Here the model can be right by answering 0, and 30 times
  it was. The three failures are wrong-concept substitutions (below): a real
  impairment figure from the filing, attached to goodwill, against a filing
  that explicitly states goodwill was not impaired.

The headline remains 15/45 as registered. The composition is disclosure, not
adjustment.

## What the goodwill and dividends accuracies are made of

The two lowest fields sit three points apart and are opposite results.

| | labels | correct, by label kind |
|---|---|---|
| goodwill_impairment | 29 stated_none · 4 value · 6 not_addressed | 26 stated_none + 3 value + 0 not_addressed = 29 |
| dividends_declared_per_share | 33 value · 4 stated_none · 2 not_addressed | 26 value + 4 stated_none + 0 not_addressed = 30 |

**Goodwill's 0.744 is mostly coincidence between the model's zero-default and
filings that state none** — 26 of its 29 correct are 0-vs-`stated_none`
matches; only 3 required reading a figure. The same zero-default produced
its 9 false extractions and its one wrong value (a real charge answered with
0, below). **Dividends' 0.769 is mostly genuine reading** — 26 of 30 correct
are matched stated figures — and its failures are reading failures. The
registered accuracies stand; this table is what they are made of.

## The failure cases

Every case below was verified against the filing text during the scored
session; predictions quoted are the model's output verbatim.

### Wrong-concept impairments (3 false extractions, all against explicit "none" statements)

- **EXR FY2024, predicted 51.763** — the filing states no goodwill
  impairment for any period presented; 51,763 (thousands) is its *Life
  Storage trade name* impairment, an intangible-asset charge. Correctly
  converted to millions, attributed to the wrong concept.
- **HON FY2024, predicted 219** — the filing states its annual test found no
  impairment; $219M is its *impairment of assets held for sale*.
- **KVUE FY2024, predicted 578** — the filing states no goodwill impairment
  was necessary; $578M is its total *impairment charges* line, which the
  filing attributes primarily to a brand-intangible charge.

The pattern: the model matched "impairment" and ignored *of what*. These are
the expensive failures the five-outcome grid exists to isolate — confident,
plausible, real numbers that are wrong.

### PGR: the corpus defect, from the model's side (8 false extractions)

Both PGR primary documents incorporate their consolidated statements by
reference; the fetched text contains only parent-company Schedule II
(`EVALUATION-SPEC.md`, CORPUS DEFECT, decided 2026-08-18: the four statement-bound labels are
`not_addressed`). Facing documents that do not contain the answers, the
model produced, per year: the **verbatim Schedule II parent-only total
assets** (35,566, FY2024); a **one-digit corruption of it** (45,402 against
a printed 45,502, FY2025 — the prediction occurs nowhere in the document);
**premium totals assembled from statutory schedules** as total revenues
(74,424 from Schedule III; 81,661, net premiums earned, from the reinsurance
schedule); a **dividends-per-share figure that occurs nowhere in the filing
in any variant** (4.58, FY2024 — the document states no per-share amount of
any kind); and **asserted zeros** (dividends FY2025, goodwill both years)
where the document states nothing. This is the run's clearest exhibit of the
finding: absence in, eight specific answers out, zero nulls.

### Invented zeros on silent filings (4 false extractions)

- **QCOM, both years** — the filings discuss impairment-*testing* policy in
  critical-accounting-estimates boilerplate and never state an outcome or
  amount.
- **VICI, both years** — the word "goodwill" does not occur in the fetched
  text at all.

Under the registered grid, 0 asserts "the filing stated zero"
(`goodwill_impairment` silent → `null`, never 0 — `EVALUATION-SPEC.md`, fixed 2026-08-09).
These filings stated nothing.

### The reverse error (1 wrong value)

**CTSH FY2025, predicted 0** — the goodwill rollforward states a real
impairment charge of $12M. The zero-default that scores correct on 26
`stated_none` instances fails exactly when a real charge, or true silence,
appears. Its FY2024 counterpart (`stated_none`, predicted 0) scored correct.

### Units (2 wrong values, one filing)

**WYNN FY2025, total_assets and revenue** — predicted 13,108,117 and
7,137,924: the filing's figures, denominated in thousands, reported raw.
Prompt rule 4 pins all monetary amounts to millions and includes a worked
"(in thousands)" example; the same run converted EXR's thousands-denominated
impairment correctly. The capability is inconsistent, not absent, and at
1000× the 0.1% tolerance is irrelevant.

### Dividends (7 wrong values — every one a periodicity or vocabulary trap)

- **Quarterly rate for the fiscal-year amount:** DGX FY2025 (0.80 vs the
  summed 3.20), KVUE FY2024 (0.205 vs 0.81) — both labels are RESOLUTION 1
  sums, flagged ambiguous and reported both ways above.
- **Subsequent-event rate:** DVN, both years (0.24 — a declaration belonging
  to the next fiscal year; labels 1.45 and 0.96, ambiguous under
  RESOLUTION 2).
- **Paid where declared is stated:** LLY, both years (5.20 vs declared 5.40;
  6.00 vs declared 6.23) — the same paid/declared distinction RESOLUTION 2
  governs on the label side, made by the model against filings that state
  both.
- **MA FY2024** (2.64 vs 2.74).

These score as misses because the prompt asks for the fiscal-year declared
amount; choosing labels to match the model's likely reading would have been
selecting the ground truth to flatter the result (`EVALUATION-SPEC.md`, RESOLUTION 1, "cost").

## ceo_name mismatches

**None — the list is empty (39/39).** Reported per the registered
requirement that name-field mismatches be listed for the reader, with manual
re-adjudication not permitted. One further check, run because the rule was
amended: AMENDMENT 1 (2026-08-09, pre-data) relaxed first-initial matching to
any-shared-given-initial, a strictly more permissive rule. **The amendment
never bound** — all 39 pairs also pass the original stricter rule, so no
`ceo_name` outcome depends on it.

## What is not claimed

- **Generality.** One model, one frozen prompt, one pass, 39 filings from
  one pre-registered draw of large-cap US issuers. The intervals quantify
  sampling noise within this corpus, not transfer beyond it.
- **A clean false-extraction rate.** It carries the contamination disclosure
  above, in the model-flattering direction.
- **Coverage.** 39 of 44 selected filings fit the context window; the
  excluded five are disproportionately multi-registrant combined filings
  Results describe in-window filings only.
- **A clean-vs-real gap.** The spec's optional synthetic-corpus control has
  not been run (deferred 2026-08-18; it requires its own labeling protocol,
  and a bolt-on version would carry an unregistered instrument). If run, it
  gets its own dated section here.
- **Determinism.** Temperature 0.0 with a 12-run stability check on 2
  calibration filings (`EVALUATION-SPEC.md`); the API does not promise identical output.

## Reproduction

From `backend/`, with the corpus present (see `SETUP.md` for the two
environment variables and the fetch script):

```
python scripts/score_predictions.py
```

The runner refuses to score unless the labels file matches the frozen
sha256, and unless labels and predictions each cover the 351-pair grid
exactly. Labels and predictions are data, not source; they are gitignored
and never committed — this document publishes the aggregates and the failure
cases, not the datasets.

---

## Phase 3 — retrieval

Measured 2026-08-20. Three arms — sparse-only, dense-only, hybrid — over one
frozen query set, at one configuration. Everything that moves a number here
was pre-registered and **published before either index existed and before a
single vector was computed**: the embedder and its dimensions, the `tsvector`
configuration, the OR-of-terms decision, `ts_rank_cd` normalization 0,
`hnsw.ef_search`, RRF `k = 60`, the fusion depth, the tie-break, and — the
one everything else rests on — what counts as a hit. All of it is in
`EVALUATION-SPEC.md`'s appendix *retrieval parameters and the hit
definition*, dated 2026-08-19, with AMENDMENTS 3, 4 and 5 each dated before
any query was written.

That ordering is the claim this section is worth reading for, and it is
checkable rather than asserted: the appendix, the query-set freeze, the three
arms, the runner and the entire scoring path were public commits **before the
first query was embedded**. No parameter has been adjusted since the numbers
existed, and none will be. A `k` chosen after seeing recall@k is a dial, and
no published number would reveal that it had been turned.

### What was measured

**65 queries, 50 of them answerable**, written blind to retrieval output —
reading filings and the chunk store to write a query is permitted, reading
what a retriever returns is not. The one prior exposure to retriever output
is disclosed in `EVALUATION-SPEC.md` along with the constraint it imposed on
the set. Stratified **25 exact-entity + 25 conceptual** (AMENDMENT 3), plus
**15 unanswerable**.

**The 15 unanswerable queries carry no gold and enter no recall
denominator.** Recall is undefined for them. They were run through all three
arms and their rankings recorded, because abstention is a property of the QA
layer and Phases 4 and 5 will measure it against what the retriever actually
put in front of that layer rather than against a reconstruction. **No
abstention rate is reported here**; a ranked list cannot abstain.

The set was frozen before any arm ran, at set digest
`a35b2634f47608fdee4d1dbd612e6d6d56f64d1e261ce85c4e6bb00d5cbde16a`
(`backend/corpus/query-set-freeze.json`, committed — ids, strata,
accessions, items and hashes, never a query and never a span). Every arm
calls `query_freeze.refuse_unless_frozen` before retrieving anything.
Stated because it is the weak link: **0 of the 65 approvals are bound to
their text mechanically**, and the freeze therefore records a dated human
attestation, labelled in the artifact as an attestation and not as a
mechanical verification.

Corpus: **11,621 chunks over 44 of 44 filings, 4,890,354 tokens**, every row
carrying both a generated `tsvector` (GIN) and a 1,536-dimension embedding
(HNSW), zero NULLs of either. Both arms read the same `text` column. **No
metadata filtering** — every query searches all 11,621 chunks in every arm.

Gold is a **document location — (accession, quoted span)** — never a chunk
id, and the gold chunk set is derived from the store at scoring time. Gold
sets in this run are **min 1, median 1, max 2 chunks**, so the task is
literally "find one chunk in 11,621". Under AMENDMENT 5, a gold span that
also appears in another filing draws a non-blocking advisory, and the count
is published as required: **11 of the 50 answerable queries are flagged.**
Those are the FY2024/FY2025 boilerplate pairs no text-only retriever can
distinguish, and they affect all three arms identically.

Run `20260820-153615`. Query-embedding digest
`4ce8c0f86d15d6dc5581f256b6aba18b9bcb1aba0398a8111e6d849db57e34da`;
`hnsw.ef_search` applied and read back as **100**, `hnsw.iterative_scan`
off. Intervals are 95% Wilson, the same `evaluation/wilson.py` the Phase 2
numbers use.

### recall@5, per arm per stratum

| arm | exact-entity (n=25) | conceptual (n=25) | pooled (n=50) |
|---|---|---|---|
| sparse | 9/25 = 0.360 [0.202, 0.555] | 1/25 = 0.040 [0.007, 0.195] | 10/50 = 0.200 [0.112, 0.330] |
| dense | 14/25 = 0.560 [0.371, 0.733] | 8/25 = 0.320 [0.172, 0.516] | 22/50 = 0.440 [0.312, 0.577] |
| hybrid | 16/25 = 0.640 [0.445, 0.798] | 2/25 = 0.080 [0.022, 0.250] | 18/50 = 0.360 [0.241, 0.499] |

### recall@1, per arm per stratum

| arm | exact-entity (n=25) | conceptual (n=25) | pooled (n=50) |
|---|---|---|---|
| sparse | 5/25 = 0.200 [0.089, 0.391] | 0/25 = 0.000 [0.000, 0.133] | 5/50 = 0.100 [0.043, 0.214] |
| dense | 4/25 = 0.160 [0.064, 0.347] | 1/25 = 0.040 [0.007, 0.195] | 5/50 = 0.100 [0.043, 0.214] |
| hybrid | 5/25 = 0.200 [0.089, 0.391] | 0/25 = 0.000 [0.000, 0.133] | 5/50 = 0.100 [0.043, 0.214] |

**Precisely what these are.** Every answerable query has at least one gold
chunk, so the quantity is a **hit rate at k** — the share of queries with at
least one gold chunk in the arm's top k. It is called recall@k because that
is the term the literature uses for it.

### Which directions the data establishes, and which it does not

The three arms see the same queries, so the comparison is **paired**. Three
overlapping Wilson intervals invite a reader to conclude "no difference" when
the paired data may say otherwise — so every arm-to-arm comparison is
reported as the **discordant pairs (b, c)**, the queries one arm gets and the
other misses, with a Wilson interval on `b/(b+c)`. That is the McNemar sign
test.

**A direction is claimed only where that interval excludes 0.5.** The two
tables below are the whole comparison — all eighteen rows — split on exactly
that rule and nothing else.

*Recorded because it happened here rather than in the abstract:* the first
internal write-up of this run read two directions straight off the point
estimates, reporting *"hybrid loses to dense overall"* and *"lexical does not
win the exact-entity stratum"* as findings. Both are in the undetermined
table below. They were caught and corrected before publication, and the
tables are split this way so the same mistake cannot be made by a reader of
this page. The rule is one the spec had already written down, in its mirror
image: the paired test exists because three overlapping Wilson intervals
invite a reader to conclude *"no difference"* when the paired data may say
otherwise — and a rule written to stop a wrong conclusion in one direction
applies in both.

In every row below, **b** counts the queries the **first-named** arm hits and
the second misses, and **c** the reverse. Concordant queries — both hit, or
both miss — carry no information about the direction and are excluded from
b + c by construction.

**One caveat, stated before the tables rather than after them, because it
cuts against the rows this project would most like to lean on.** Every
discordant denominator here is small — **16 is the largest, and b + c is
below this project's n ≥ 25 reporting gate on all eighteen rows**. The
scorer prints that warning on every line and it is not decoration. What the
gate governs is **reporting b/(b+c) as a rate**: those point estimates are
below it and should not be read as rates. What the interval test governs is
whether a **direction** may be claimed, and a Wilson interval already prices
in the n it was computed on — an interval of [0.646, 1.000] on 7 pairs
excludes 0.5 honestly, and one of [0.487, 0.974] on 7 pairs does not. So
"established" below means exactly *the interval excludes 0.5*, and it is not
a claim that 7 discordant pairs are a large sample.

**Established at 95% (interval excludes 0.5) — five of the eighteen:**

| comparison | k | stratum | b, c | b/(b+c) | direction established |
|---|---|---|---|---|---|
| hybrid vs sparse | 5 | exact-entity | 7, 0 | 1.000 [0.646, 1.000] | hybrid > sparse |
| hybrid vs sparse | 5 | pooled | 9, 1 | 0.900 [0.596, 0.982] | hybrid > sparse |
| dense vs sparse | 5 | conceptual | 8, 1 | 0.889 [0.565, 0.980] | dense > sparse |
| dense vs sparse | 5 | pooled | 14, 2 | 0.875 [0.640, 0.965] | dense > sparse |
| hybrid vs dense | 5 | **conceptual** | 0, 6 | 0.000 [0.000, 0.390] | **dense > hybrid** |

**Undetermined (interval contains 0.5) — the other thirteen:**

| comparison | k | stratum | b, c | b/(b+c) | direction established |
|---|---|---|---|---|---|
| dense vs sparse | 5 | exact-entity | 6, 1 | 0.857 [**0.487**, 0.974] | none |
| hybrid vs dense | 5 | exact-entity | 4, 2 | 0.667 [0.300, 0.903] | none |
| hybrid vs sparse | 5 | conceptual | 2, 1 | 0.667 [0.208, 0.939] | none |
| hybrid vs dense | 5 | **pooled** | 4, 8 | 0.333 [0.138, **0.609**] | **none** |
| dense vs sparse | 1 | exact-entity | 3, 4 | 0.429 [0.158, 0.750] | none |
| hybrid vs dense | 1 | exact-entity | 3, 2 | 0.600 [0.231, 0.882] | none |
| hybrid vs sparse | 1 | exact-entity | 2, 2 | 0.500 [0.150, 0.850] | none |
| dense vs sparse | 1 | conceptual | 1, 0 | 1.000 [0.207, 1.000] | none |
| hybrid vs dense | 1 | conceptual | 0, 1 | 0.000 [0.000, 0.793] | none |
| hybrid vs sparse | 1 | conceptual | 0, 0 | undefined — no discordant pairs; both arms miss all 25 | none |
| dense vs sparse | 1 | pooled | 4, 4 | 0.500 [0.215, 0.785] | none |
| hybrid vs dense | 1 | pooled | 3, 3 | 0.500 [0.188, 0.812] | none |
| hybrid vs sparse | 1 | pooled | 2, 2 | 0.500 [0.150, 0.850] | none |

**Nothing at k=1 is established for any pair** — nine rows, no direction.
That is a property of paired testing at n=50 with a hit rate near 0.100, not
something this run could have fixed by trying harder.

**Two sentences that are true and are not the same sentence.** *Hybrid's
pooled point estimate is lower than dense's, 18/50 = 0.360 [0.241, 0.499]
against 22/50 = 0.440 [0.312, 0.577]* — that is arithmetic. *Hybrid loses to
dense overall* — that is a direction, it requires the paired test, and **the
paired test does not carry it**: the twelve discordant queries split 4 to 8,
giving 0.333 [0.138, 0.609]. The honest statement is that this is the
direction the data leans, and n=50 cannot establish it.

### The pre-registered expectation, adjudicated clause by clause

`EVALUATION-SPEC.md` §5 published an expectation before any of this existed:
*"lexical wins stratum one, dense wins stratum two, hybrid wins overall, and
the pooled number hides all of it. If that is not what the data says, publish
what the data says."* This is that.

| clause | what the data says | verdict |
|---|---|---|
| lexical wins exact-entity | sparse is last of three there, 9/25 = 0.360 [0.202, 0.555]; hybrid beats it on 7 discordant pairs to 0, 1.000 [0.646, 1.000] | **refuted, and the refutation is established** |
| dense wins conceptual | dense 8/25 = 0.320 [0.172, 0.516] beats sparse on 8 discordant pairs to 1, and beats hybrid on 6 to 0; both established | **confirmed** |
| hybrid wins overall | no established win over dense pooled; the point estimates lean the other way, and the discordant pairs split 4/12 → 0.333 [0.138, 0.609] | **not supported — and, at n=50, not refuted either** |
| the pooled number hides all of it | pooling does hide hybrid's 16/25 = 0.640 [0.445, 0.798] on exact-entity against its 2/25 = 0.080 [0.022, 0.250] on conceptual; it does not hide dense > sparse or hybrid > sparse, both established pooled | **holds in part** |

**So the expectation as a whole did not survive.** One clause of four is
confirmed, one is refuted outright, one the data cannot support, and one
holds only in part — and the part that holds does so for a different reason
than the one predicted. The prediction was published in advance precisely so
this paragraph could be written without anyone having to take its author's
word for what was expected.

### Why hybrid behaves this way — the mechanism, checked rather than assumed

**RRF's agreement bonus is informative where both arms have signal and
anti-informative where one of them is dead.** That is the shape of the whole
result, and it is why "hybrid failed" is the wrong summary: hybrid produces
the single best cell in the table, **16/25 = 0.640 [0.445, 0.798]** on
exact-entity, and beats sparse 7 discordant pairs to 0 there.

Where sparse has real signal — exact-entity, 9/25 = 0.360 [0.202, 0.555] —
agreement between the arms means something, and fusion helps. Where sparse is
effectively dead — conceptual, **1/25 = 0.040 [0.007, 0.195]** — fusion
inherits its noise, and this is the one established hybrid deficit in the
run: dense over hybrid on conceptual, **b = 0, c = 6**.

Those six queries were examined individually rather than argued about. **In
all six the sparse arm has no rank at all** — it never returned the gold
chunk anywhere in its top 50, which is the whole of what it was asked for —
and in all six the gold chunk sits at dense rank 2 to 5 and is pushed to
hybrid rank 12 to 22:

| query | sparse rank | dense rank | hybrid rank |
|---|---|---|---|
| q005 | none in top 50 | 4 | 22 |
| q017 | none in top 50 | 2 | 15 |
| q018 | none in top 50 | 4 | 16 |
| q019 | none in top 50 | 4 | 12 |
| q020 | none in top 50 | 5 | 18 |
| q034 | none in top 50 | 3 | 15 |

The arithmetic is not subtle. A chunk only one arm ranked earns
`1/(60 + rank) ≈ 0.016`. Any chunk **both** arms ranked, however wrongly,
earns up to `≈ 0.032`. Two mediocre agreeing candidates outrank one excellent
lone one. And the arms are looking at nearly disjoint parts of the corpus:
over the 50 answerable queries their top-50 lists **overlap by a mean of 7.4
chunks out of 50** (median 6.5, min 0, max 28).

**This is not an argument for tuning `k`.** `k = 60` is the published value,
fixed before any index existed. Tuning it rescales every vote equally and
cannot distinguish a confident arm from a dead one, which is the actual
failure. If a second configuration is ever run it is reported as an
additional arm with the appendix amended, never as a replacement for these
numbers.

### The absolute numbers are low. Here is how to tell a hard task from a broken pipeline

**10 of the 50 answerable queries are reached by no arm at any depth to 50** —
q007, q008, q028, q033, q039, q040, q043, q057, q059, q061, five from each
stratum. And **all three arms return the same pooled recall@1, 5/50 = 0.100
[0.043, 0.214]**. Those two facts are the ones a reader should be most
suspicious of, because a mis-loaded store, an index the planner never uses,
or an arm silently returning fewer rows than it was asked for would all
produce exactly this picture. They were checked as a possible defect before
being reported as a finding.

**The known-positive control settles it.** Take each of those 10 queries,
throw away the question, and hand the index the **gold span's own text** as
the query, at the arms' published parameters. **9 of the 10 are reached**,
three of them at rank 1:

| query | sparse rank | dense rank |
|---|---|---|
| q007 | 18 | none |
| q008 | 47 | 4 |
| q028 | none | 1 |
| q033 | 3 | 1 |
| q039 | none | 15 |
| q040 | 22 | 20 |
| q043 | none | 2 |
| q057 | 4 | none |
| q059 | 1 | 1 |
| q061 | none | none |

The index can return those chunks when handed their own text. What fails is
the jump from a question to the passage that answers it — which is the thing
being measured. The single exception, **q061**, is a generic
customer-concentration sentence, the near-boilerplate case AMENDMENT 5 was
written about.

The same probe carries its own **negative control**, because a probe that
reports success for everything is not evidence of anything: a nonsense query
(`zqxjvk parenthetical wombat treacle…`) run through the identical code path
against q007's gold returns **no rank in either arm**. So "reached" in the
table above is a property of the text, not of the probe.

**This control is a diagnostic and it was run after the numbers existed**
(2026-08-20, then re-run independently before publication, same result). It
uses the arms at their published parameters, it changes no parameter, and it
recomputes no reported number — every recall figure above comes from the
frozen run `20260820-153615` and from nothing else. It is reported because a
reader otherwise has no way to tell a hard task from a broken one, and
withholding it would leave the low numbers doing that work by implication.

Two further checks, run before any of this was reported: the chunk ids in
Postgres match the chunk ids in the materialised store exactly, **11,621 both
ways** — the runner refuses to retrieve otherwise — and `hnsw.ef_search` was
set and **read back** at 100 on every dense search, because Postgres accepts
a `SET` on an unrecognised prefixed name as a placeholder that never takes
effect, and at `ef_search = 10` a depth-50 request silently returns 10 rows.

**A recall@1 of 5/50 = 0.100 [0.043, 0.214] for all three arms is not three
arms agreeing.** The three hit sets are **5 queries each, 10 distinct queries
in the union, and exactly one — q001 — hit by all three.** An identical point
estimate over near-disjoint query sets is the clearest illustration in this
run of why the comparisons above are paired rather than differences of rates.

**Diagnostics, labelled as diagnostics.** §5 fixed recall@1 and recall@5 and
nothing else. The rank-of-first-gold figures, the overlap counts, the
unreached list, the gold-set sizes and the control table above exist to
answer *"is this broken?"* and are reported as such. They are not headline
metrics, and a rank distribution that drifted into a results table would be a
metric chosen after seeing the data. For completeness: the first gold chunk
lands within depth 50 for **sparse 19/50, dense 36/50, hybrid 36/50**, the
hybrid figure taken over the first 50 entries of the fused list so that it is
comparable with the other two.

### What the retrieval numbers do not claim

- **Anything about a second configuration.** One embedding model
  (`text-embedding-3-small` at 1,536 dimensions), one `tsvector`
  configuration (`english`), one RRF constant (`k = 60`), one chunk size.
  This reports retrieval quality **at one configuration** and cannot say
  whether another embedder, a `simple` dictionary, or a different `k` would
  do better.
- **That hybrid loses to dense.** See the paired tables: undetermined at
  n=50. Nor that hybrid beats dense.
- **Comparability across chunker versions.** Gold is a location and the gold
  chunk set is derived from the store, so the query set survives a re-chunk
  but these numbers do not.
- **Generality.** 44 filings from 22 large-cap US issuers drawn by a
  pre-registered rule, and 65 queries written by one author and reviewed by
  one owner. The intervals quantify sampling noise within this set, not
  transfer beyond it. (The denominator is **44** here against Phase 2's
  **39**: chunking removes the context-window limit that excluded five
  filings from the extraction grid, so retrieval covers the whole corpus.
  The two sections are not measuring the same population and their numbers
  are not comparable.)
- **Ranking quality beyond k=5.** Only recall@1 and recall@5 were
  pre-registered; no MRR, no nDCG, and adding one now would be a metric
  chosen after seeing the data.
- **That 11 duplicate-span queries are harmless.** They are disclosed and
  counted, not corrected for.

### Reproducing the retrieval numbers

From `backend/`, with the corpus present and `RAG_FILINGS_DIR` set:

```
python scripts/score_retrieval.py
```

**This needs no database and no API key.** It re-reads the ranked lists from
the run's own artifacts, so every published retrieval number above can be
recomputed offline in about three and a half minutes — most of it in
AMENDMENT 5's duplicate-span recount, which scans all 11,621 chunks per gold
span. The scorer refuses a partial run, a run made against a different
query-set digest, and a rankings file whose bytes no longer match the sha256
its provenance recorded (`202da364a4d0db…`).

`scripts/run_retrieval.py` regenerates the rankings and does need
`DATABASE_URL` and `OPENAI_API_KEY`. It refuses on five grounds before a
single query is embedded: the query set must match the freeze, the store must
match the database, no embedding may be NULL, `ef_search` must read back as
set, and the output directory must be outside the repository.

The queries, their gold spans, and the ranked lists are **data, not source**.
They are gitignored and never committed — this section publishes the
aggregates, the mechanism and the diagnostics, not the dataset. The scoring
path itself (`services/retrieval.py`, `services/fusion.py`,
`evaluation/retrieval_gold.py`, `evaluation/retrieval_scoring.py`,
`scripts/run_retrieval.py`, `scripts/score_retrieval.py`) is in this
repository, and was public and dated **before the first query was
embedded**.

---

## Phase 3b — a fourth arm, designed after seeing Phase 3's results

Measured 2026-08-21. **Read the next four sentences before any number below.**

The three arms above were pre-registered **blind**: their parameters were
public and dated before either index existed, so they could not have been
chosen to produce the result. **This arm was not.** It was designed on
2026-08-20 in response to a failure the published Phase 3 numbers revealed,
and it is measured **on the same 65 queries whose per-query outcomes were
already known**. Its result is therefore a **hypothesis consistent with the
data that suggested it — never an independent confirmation of anything.**

Only a held-out query set would make it a confirmation, and there is not one.
Building one now would not repair this arm; it would be a different, later
experiment. The full disclosure, the rule, the threshold procedure and the
clause forbidding a second attempt were **published and pushed before the arm
was built** — `EVALUATION-SPEC.md`, appendix *PHASE 3b, a fourth arm*, dated
2026-08-20 — and the Phase 3 numbers above were published and pushed before
that. The ordering is one commit at a time and it is checkable.

**The three arms above are not recomputed, adjusted or restated anywhere in
this section.** `gated` is an additional row. Re-scoring the original run
without the fourth arm reproduces every Phase 3 figure exactly; that is
enforced by test, because the reproduction command this document publishes
would otherwise stop working.

### What it targets, and the rule

**Rank-only fusion cannot distinguish a confident arm from a dead one.** RRF
sees ranks and nothing else, so an arm that has located the answer and an arm
that has returned fifty chunks it cannot order cast votes of equal weight.
That is the mechanism behind hybrid's one established deficit above.

The rule, in full. `s₁` is **the sparse arm's own top `ts_rank_cd` score** for
a query, and `L` its number of distinct lexemes:

- **`s₁ > τ(L)`** — fuse both arms under the published RRF. Bit-identical to
  the hybrid arm.
- **`s₁ ≤ τ(L)`** — the sparse arm contributes no votes; the ranking is the
  dense arm's, unchanged.

Binary, one parameter, no weights. `k = 60`, fusion depth 50 and the
`chunk_id` tie-break are the pre-registered values in both branches — 3b
changes the arm, not them. Verified by test on every query: where the gate
fired the arm is exactly `dense`, and where it did not it is exactly `hybrid`.

### Where τ comes from — the one part of 3b that is uncontaminated

`τ(L)` is the **95th percentile, nearest-rank, of the top `ts_rank_cd` scores
of 1,000 random lexeme bags** of `L` distinct lexemes, drawn from this store's
own 19,986-lexeme vocabulary with probability proportional to document
frequency, seed `20260820`. The gate fires when a query's sparse evidence is
not distinguishable, at the project's own alpha, from a query built out of
noise.

**No gold span, no gold chunk set, no hit, no miss and no recall figure enters
that computation.** It is a property of the store and the `english`
dictionary; it could have been computed on 2026-08-19, before any query
existed, to the same answer. This is AMENDMENT 4's method — the gold cap of 5
was fixed by measuring the corpus, never by watching what it did to a number.

The 95th percentile is not a fresh choice: it is the same 95% every Wilson
interval in this document uses. Measured values, published whatever they are:

| L | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 14 |
|---|---|---|---|---|---|---|---|---|---|---|
| τ(L) | 3.30 | 3.40 | 3.60 | 3.80 | 3.90 | 4.00 | 4.10 | 4.10 | 4.20 | 4.40 |

**The gate fired on 34 of the 50 answerable queries** — **21/25 conceptual**
and **13/25 exact-entity**. That count was published as required whatever it
turned out to be, and **τ was not moved after it was applied**: the
pre-registration bound this in advance, including the cases where the arm
would have come out identical to `hybrid` or identical to `dense`.

### recall@5 with the fourth row

| arm | exact-entity (n=25) | conceptual (n=25) | pooled (n=50) |
|---|---|---|---|
| sparse | 9/25 = 0.360 [0.202, 0.555] | 1/25 = 0.040 [0.007, 0.195] | 10/50 = 0.200 [0.112, 0.330] |
| dense | 14/25 = 0.560 [0.371, 0.733] | 8/25 = 0.320 [0.172, 0.516] | 22/50 = 0.440 [0.312, 0.577] |
| hybrid | 16/25 = 0.640 [0.445, 0.798] | 2/25 = 0.080 [0.022, 0.250] | 18/50 = 0.360 [0.241, 0.499] |
| **gated** *(post-hoc)* | 17/25 = 0.680 [0.484, 0.828] | 8/25 = 0.320 [0.172, 0.516] | 25/50 = 0.500 [0.366, 0.634] |

**recall@1 does not move at all.** `gated` is 5/50 = 0.100 [0.043, 0.214],
the same figure as all three arms above, with 4/25 exact-entity and 1/25
conceptual — identical to `dense`. Whatever the gate fixes, it is not the
top-1 floor.

### What the fourth arm establishes, and what it does not

Same rule as above: a direction holds only where the Wilson interval on
`b/(b+c)` excludes 0.5. **`b` counts queries the first-named arm hits and the
second misses.**

**Established, k=5:**

| comparison | stratum | b, c | b/(b+c) | direction |
|---|---|---|---|---|
| gated vs sparse | pooled | 16, 1 | 0.941 [0.730, 0.990] | gated > sparse |
| gated vs sparse | exact-entity | 8, 0 | 1.000 [0.676, 1.000] | gated > sparse |
| gated vs sparse | conceptual | 8, 1 | 0.889 [0.565, 0.980] | gated > sparse |
| **gated vs hybrid** | **pooled** | 8, 1 | 0.889 [0.565, 0.980] | **gated > hybrid** |
| **gated vs hybrid** | **conceptual** | 6, 0 | 1.000 [0.610, 1.000] | **gated > hybrid** |

**Undetermined, k=5:**

| comparison | stratum | b, c | b/(b+c) | direction |
|---|---|---|---|---|
| gated vs dense | pooled | 3, 0 | 1.000 [**0.439**, 1.000] | none |
| gated vs dense | exact-entity | 3, 0 | 1.000 [**0.439**, 1.000] | none |
| gated vs dense | conceptual | 0, 0 | undefined — the two arms agree on all 25 | none |
| gated vs hybrid | exact-entity | 2, 1 | 0.667 [0.208, 0.939] | none |

Nothing at k=1 is established for `gated` against any arm, exactly as nothing
was for the first three.

**So, precisely:**

- **The arm did what it was designed to do, and that is established.** Hybrid's
  one unambiguous deficit was conceptual, 0–6 against dense. `gated` reverses
  it: 6–0 over hybrid on conceptual, and 8–1 pooled. The gate removes the
  sparse arm's votes on the queries where it has no handle, which is the
  failure named in the pre-registration before the arm was built.
- **"gated beats dense" is NOT established, and the pooled point estimate is
  not evidence that it does.** 25/50 = 0.500 [0.366, 0.634] against 22/50 =
  0.440 [0.312, 0.577] looks like an improvement; the paired test gives
  **3, 0 → 1.000 [0.439, 1.000]**, which
  contains 0.5 and misses by 0.061. This is the same trap as hybrid-vs-dense
  above, and it is worth stating twice: **`gated` never loses a query to dense
  in this run — 3–0, no losses anywhere — and n=50 still cannot establish the
  direction.**
- **On the conceptual stratum `gated` is dense.** Not "similar to": `b = 0,
  c = 0`, the two arms agree on every one of the 25. The gate fired on 21 of
  them, where the arm *is* dense by construction, and on the other 4 fusion
  happened to land on the same hit/miss. The entire conceptual gain over
  hybrid is the gate declining to fuse.
- **Its whole advantage over dense is three exact-entity queries** — q013,
  q027, q042 — and all three are queries the gate did **not** fire on, where
  fusion with a live sparse arm found something dense alone missed. That is
  the case for fusion in this corpus, and it rests on three queries.

### What the fourth arm does not claim

- **That it is a confirmation of anything.** It is post-hoc. Every number in
  this section carries that, not just the summary line.
- **That it beats dense.** Undetermined at n=50, in both strata and pooled.
- **That the threshold is validated.** τ is uncontaminated by gold, but it was
  applied once, to the queries that motivated the design. A threshold that
  works on the set that suggested it has not been tested.
- **That the gate generalises.** It fired on 34/50 here. On another corpus,
  another `tsvector` configuration or another chunk size, the null shifts and
  so does the count.
- **Anything about recall@1.** Unmoved, 5/50, same as every other arm.
- **That a rule is better than a weight.** Weighted RRF and score-based fusion
  were both declined in the pre-registration, on grounds recorded there. They
  were not measured and lost; they were not run.

### Reproducing the fourth arm

```
python scripts/measure_gate_threshold.py     # tau, from the store. Needs DATABASE_URL.
python scripts/run_gated_arm.py              # the arm. No database, no API.
python scripts/score_retrieval.py --gated gated-rankings-<stamp>.jsonl
```

**`run_gated_arm.py` touches neither Postgres nor OpenAI**, and that is a
guarantee rather than a convenience: the fourth arm is a re-fusion of the
recorded run, so the three published arms are read from a file whose sha256 is
verified first and are **never re-run**. Nothing in Phase 3b can move a Phase 3
number, because Phase 3b never asks the retriever anything.

`score_retrieval.py` **without** `--gated` behaves exactly as it did when the
three arms were published, and reproduces them figure for figure. The fourth
arm is detected from the rankings rather than being a required arm, so the
reproduction instructions in the Phase 3 section above keep working unchanged.

## Phase 4/5 — grounded QA and abstention

Measured 2026-08-22/23. One model reads each query with the top 5 chunks one
retrieval arm put in front of it, and either answers with a citation and a
verbatim quote or declines. Every rule that moves a number — the conditioned
split and its denominators, the context depth, the outcome taxonomy, what
counts as an abstention, the correctness rubric, the prompt itself — was
pre-registered in `EVALUATION-SPEC.md`'s appendix *PHASE 4/5* and **pushed
before a single QA call existed**, control or eval. One dated amendment to
that appendix was made after a failed known-positive control and before any
eval-set call; it is described below, because what the control caught is
itself evidence.

**All four arms are measured** — sparse, dense, hybrid and `gated` — because
choosing one retriever on the same 65 queries this phase reports against
would have selected it using those labels. **Every `gated` row below inherits
Phase 3b's post-hoc disclosure**: the arm was designed after Phase 3's
results, on the same queries; its rows are a hypothesis consistent with the
data that suggested it, never a confirmation.

**No number in this section is comparable to Phase 2's extraction figures.**
Different task, different denominators, different outcome definitions. The
continuity is the model and the corpus, not the metric.

### What was measured

**k = 5.** The context for a query under an arm is that arm's recorded top 5,
in rank order — read from the pinned Phase 3 and 3b artifacts, never
re-retrieved. Top-1 would have been broken by construction (recall@1 is 0.100
for all four arms, a conditioned denominator of 5), and anything deeper than
5 is a retrieval cutoff this project never registered. Each excerpt is shown
with its ticker, fiscal period end and item label. **The run touches no
database**: rankings, gated rankings and chunk text are files verified
against the digests the appendix publishes before the first call.

**One call per distinct (query, ordered top-5) context.** `gated`'s top 5 is
bit-identical to dense's where its gate fired and to hybrid's where it did
not — verified from the files — so the 260 arm-query rows collapse to **195
API calls**, and calling twice on an identical context would only have
injected sampling noise into paired comparisons that are concordant by
construction. A structural consequence, stated in advance in the appendix:
`gated` vs `dense` can only be discordant on the un-gated queries, `gated`
vs `hybrid` only on the gated ones.

**The instrument** is `gpt-4o-mini` at temperature 0 with JSON output — the
same model and posture as the Phase 2 extractor, because the phase asks
whether grounding changes *that model's* behaviour. The full prompt is
published verbatim in the appendix; `services/qa.py` carries it byte for
byte, a test compares the two, and the fingerprint
sha256(model | temperature | prompt) = `d40f2ca571301586…` is recorded in
every artifact of the run. The model returns
`{"answer", "citation", "quote"}`. **An abstention is exactly
`"answer": null`** — a prose refusal is an answered item that asserts
nothing. **195 of 195 responses parsed as the registered schema; zero were
malformed.**

**Outcomes are mechanical except one axis.** Support is verified by machine:
the quote must appear verbatim (under the published normalization) in the
cited excerpt, and the cited chunk either passes the pre-registered hit test
for that query or does not — the same `retrieval_gold.py` functions the
retrieval numbers used, called rather than re-implemented. The one
non-mechanical axis is whether an answer's *content* agrees with the gold
span, and that was judged by a human under a rubric fixed in the appendix,
**blind to everything else**: the adjudication interface shows the question,
the gold span(s) and the answer text only — no arm, no citation, no
retrieval outcome — enforced by construction and by test, with items in a
seeded shuffled order and one verdict per distinct (query, normalized
answer). **No LLM judged, assisted or drafted the application of any
verdict.** The verdict file is append-only (misclicks are corrected by
appended retraction, never edited) and was digest-frozen before any per-arm
cell was assembled: **69 verdicts over 69 items, 2 ambiguous**, sha256
`dbae5560953259b4…`.

**Grounded-correct**, the phase's strictest outcome, is the conjunction: the
cited chunk passes the hit test, the quote verifies, and the human verdict
is correct. **The conditioned split** reports accuracy separately for
queries whose context actually contained a gold chunk and those whose
context did not — because retrieval caps this phase at 0.500, and a single
end-to-end number would be a retrieval number wearing a QA costume. The
split's denominators are the published recall@5 numerators — **10 / 22 / 18
/ 25** for sparse / dense / hybrid / gated — re-derived from the recorded
contexts under the published hit test, and both the runner and the scorer
refuse to run if they do not reproduce those numbers exactly. They did.

### The control that failed before the run, and what it caught

The harness's known-positive control plants a fact from a calibration filing
(the Phase 2 dev set — outside the corpus, outside the store) in the third
of five fixed excerpts and asks for it. On the first controls run the model
answered correctly, cited correctly — and presented as its "verbatim quote"
a sentence that **does not exist in the excerpt**: it collapsed a three-year
parallel construction ("operated 914, 890, and 861 warehouses worldwide at
August 31, 2025, September 1, 2024, and September 3, 2023") into a
fabricated single-year sentence, on all three stability repeats,
byte-identically, at temperature 0. Plausible instead of grounded, caught by
the faithfulness check before a single eval call. A second defect surfaced
on the re-run: the answer arrived as a bare JSON number on two of three
repeats — the instrument's one observed run-to-run instability, at the
serialization level.

The amendment this forced is dated in the appendix and is deliberately
narrow: **the instructions changed; the checks did not.** Loosening the
verbatim-containment test or the type check instead was considered and
refused by name — each is the one mechanical faithfulness guarantee the
phase has, and each would have moved in the direction that flatters the
pipeline. The failed control records are kept, with the superseded
fingerprints. The third controls run passed both controls on all repeats,
byte-identically, and the eval run was gated on that record.

### Grounded accuracy, given a gold chunk in the context

Can the model convert a retrieval success into a cited, quoted, correct
answer? The denominators are each arm's recall@5 numerator, so **every cell
in this table except `gated`'s sits below the n ≥ 25 reporting gate** — the
counts are the data, and the rates should be read accordingly.

| arm | n | grounded-correct | answered, not grounded-correct | abstained holding gold |
|---|---|---|---|---|
| sparse | 10 | 5/10 = 0.500 [0.237, 0.763] † | 5 | 0 |
| dense | 22 | 4/22 = 0.182 [0.073, 0.385] † | 18 | 0 |
| hybrid | 18 | 6/18 = 0.333 [0.163, 0.563] † | 12 | 0 |
| gated ‡ | 25 | 5/25 = 0.200 [0.089, 0.391] | 20 | 0 |

† below the n ≥ 25 gate. ‡ post-hoc; see the disclosure above.

**No arm abstained while holding gold, and no response was malformed: every
failure in this table is an answered failure.** The dominant outcome
everywhere is answered-but-not-grounded-correct, and the adjudication notes
say what it is made of: **scale, repeatedly** — figures reported without the
filing's "in thousands" or "in millions" (Wynn, Honeywell, Mastercard,
AppLovin, Kenvue all drew this note), incomplete multi-part answers, and
citations of a non-gold excerpt while a gold chunk sat in the same context. The QA layer,
handed the right passage, produced a grounded-correct answer between
roughly one time in five and one time in two — and that range is the
bottleneck this phase was built to expose.

### Abstention and invention, with no gold in the context

When retrieval fails, the honest behaviours are declining, or answering
correctly from a non-gold passage that genuinely contains the answer.
**Invention** — the registered measure — is the answered share that is
unsupported or adjudicated incorrect.

| arm | n | abstained | invention | supported non-gold (adjudicated correct) | unsupported |
|---|---|---|---|---|---|
| sparse | 40 | 34/40 = 0.850 [0.709, 0.929] | 6/40 = 0.150 [0.071, 0.291] | 2 (0) | 4 |
| dense | 28 | 12/28 = 0.429 [0.265, 0.609] | 11/28 = 0.393 [0.236, 0.576] | 9 (5) | 7 |
| hybrid | 32 | 19/32 = 0.594 [0.423, 0.745] | 9/32 = 0.281 [0.156, 0.454] | 7 (4) | 6 |
| gated ‡ | 25 | 10/25 = 0.400 [0.234, 0.593] | 10/25 = 0.400 [0.234, 0.593] | 8 (5) | 7 |

The shape is consistent and worth naming, without claiming a direction the
paired data does not carry: **sparse's failed retrievals are off-topic, so
the model declines; dense's failed retrievals are plausible, so the model
answers from them.** Some of those answers are real — dense answered
correctly from a non-gold passage 5 times, a limitation of accession-scoped
gold surfacing exactly where the pre-registration said it would be reported,
credited to neither grounded accuracy nor invention. One caveat inside the
invention counts, disclosed so the number reads no worse than it is: one of
dense's and gated's unsupported items is the model attempting to abstain
and breaking the schema — the literal string `"null"` rather than JSON
null. Under the registered rule only JSON null abstains, so it scores as an
answered, unsupported item; it asserts nothing, but it is counted against
the pipeline rather than for it.

### Abstention on the 15 unanswerable queries

| arm | abstained |
|---|---|
| sparse | 15/15 = 1.000 [0.796, 1.000] |
| dense | 15/15 = 1.000 [0.796, 1.000] |
| hybrid | 15/15 = 1.000 [0.796, 1.000] |
| gated ‡ | 15/15 = 1.000 [0.796, 1.000] |

**Every arm declined every unanswerable query — 60 of 60 opportunities to
invent, all declined** — and the six arm pairs agree on every query (b = 0,
c = 0 throughout), so no paired interval is even defined. n = 15 is below
the reporting gate and the interval floor is 0.796; what can be said is the
count, and the count is total. Several of these queries have answers the
model plausibly "knows" from pretraining — the set was built that way
deliberately — and it declined them anyway. This is the cell Phase 2 could
not produce: that extractor returned a value for all 351 instances and
invented on silent filings; this pipeline, grounded and licensed to decline,
declined every time the corpus was silent. The two measurements are not
numerically comparable, and the qualitative contrast is the point.

### End-to-end, with the ceiling it cannot exceed

| arm | grounded-correct (n=50) | retrieval ceiling (recall@5) |
|---|---|---|
| sparse | 5/50 = 0.100 [0.043, 0.214] | 10/50 = 0.200 |
| dense | 4/50 = 0.080 [0.032, 0.188] | 22/50 = 0.440 |
| hybrid | 6/50 = 0.120 [0.056, 0.238] | 18/50 = 0.360 |
| gated ‡ | 5/50 = 0.100 [0.043, 0.214] | 25/50 = 0.500 |

About one answerable question in ten ends in a grounded-correct cited
answer, at this configuration. The ceiling column is why the conditioned
tables above exist: without it, gated's 0.100 would read as a QA number when
half of it is retrieval.

### Which directions the data establishes, and which it does not

Same rule as every comparison in this document: paired discordants (b, c),
Wilson on b/(b+c), a direction only where the interval excludes 0.5. On
**grounded-correct over the 50 answerable queries, no direction is
established between any pair of arms** — all six intervals straddle 0.5,
with discordant counts of 1 to 10, every one below the reporting gate:

| pair | b | c | b/(b+c) | interval | |
|---|---|---|---|---|---|
| sparse vs dense | 5 | 4 | 0.556 | [0.267, 0.811] | undetermined |
| sparse vs hybrid | 3 | 4 | 0.429 | [0.158, 0.750] | undetermined |
| sparse vs gated ‡ | 5 | 5 | 0.500 | [0.237, 0.763] | undetermined |
| dense vs hybrid | 1 | 3 | 0.250 | [0.046, 0.699] | undetermined |
| dense vs gated ‡ | 0 | 1 | 0.000 | [0.000, 0.793] | undetermined |
| hybrid vs gated ‡ | 2 | 1 | 0.667 | [0.208, 0.939] | undetermined |

On abstention over the 15 unanswerable, all six pairs agree on every query.

**What n = 50 did not detect is itself the finding**: retrieval ceilings
spanning 0.200 to 0.500 did not propagate into detectably different
grounded accuracy, because the arms convert their in-context chances at
different rates — sparse converted 5 of its 10, dense 4 of its 22. Stated
as point estimates, not directions. One project decision follows and is
recorded here because the pre-registration tied it to this outcome: the
held-out query set's named trigger — *build it only if Phase 4/5 shows arm
choice actually changes grounded accuracy* — **does not fire**.

### The wrong year, cited with the year label in view

Under AMENDMENT 5, 11 of the 50 answerable queries carry gold whose text
also appears, byte-identical, in the same issuer's other fiscal year — a
coin flip no text-only retriever can call. The QA layer is past that
excuse: every excerpt is labelled with its fiscal period end. It cited the
right passage from the wrong filing anyway — **dense 4 times, gated 4,
hybrid 1, sparse 0, and every single one on a flagged query**. Gold stays
accession-scoped, so none of these are credited; the count is disclosed as
the appendix requires. The trap that began as a store measurement
(217 duplicate groups) and became a retrieval advisory is, at the QA layer,
a real model error with the disambiguating label in view.

### Ambiguous verdicts, reported both ways

Two items drew the ambiguous flag — both cases where agreement between
answer and gold span could not be determined from the span, recorded with
notes. Both are scored **incorrect** in every table above (the tie resolves
against the pipeline). Recomputed with them excluded, no cell moves by more
than its own uncertainty: dense's grounded accuracy becomes 4/21 = 0.190,
gated's 5/24 = 0.208, hybrid's abstention 19/31 = 0.613, end-to-end 4/49 /
6/49 / 5/49 for dense / hybrid / gated; sparse is untouched.

### What these numbers do not claim

- **Any comparison with Phase 2's figures.** Said at the top and repeated
  here: different task, different denominators.
- **Any direction between arms at the QA layer.** All six paired intervals
  straddle 0.5. "Sparse converts better" is a point-estimate lean, not a
  finding.
- **That the conditioned rates are stable.** Every gold-in-context cell but
  gated's is below the n ≥ 25 gate; the counts are the data.
- **That abstention generalises.** 15/15 is an interval floor of 0.796, on
  one query set, one corpus, one prompt.
- **Anything beyond one configuration.** One model, one prompt, one k, one
  run — the same limitation, for the same reason, as the one chunk size and
  one retrieval configuration above. A better prompt may exist; it was not
  searched for, because a prompt tuned on eval output is a dial.
- **That the correctness axis is more than one blinded human's rubric'd
  judgment.** The rubric, the blinding and the tie-break are published; the
  judgment is attested by the frozen verdict file, not mechanically
  verifiable.
- **That supported-non-gold answers validate the retriever.** They surface a
  known limitation of accession-scoped gold, and are reported on their own
  line for exactly that reason.

### Reproducing the QA numbers

```
python scripts/run_qa.py --dry-run     # verifies every pin; calls nothing
python scripts/score_qa.py             # recomputes every cell above. No API.
```

`score_qa.py` re-reads the recorded answers
(`answers-20260822-220946.jsonl`, sha256 `9ae919060c41798e…`, verified
against the run's provenance) and the frozen verdicts (sha256
`dbae5560953259b4…`), re-derives the conditioned split from the pinned
rankings, and **refuses to score** if any digest mismatches, if the split
fails to reproduce 10/22/18/25, or if any answered answerable item lacks a
standing verdict. The answers are never regenerated: the pre-registration's
one-run clause means the recorded responses are the phase's data, the same
way the recorded rankings are Phase 3's.
