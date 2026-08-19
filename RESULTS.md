# Results — Phase 2 extraction accuracy

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
