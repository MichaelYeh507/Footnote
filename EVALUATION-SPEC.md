# Evaluation specification — pre-registered

Extracted verbatim on 2026-08-18 from §5 of the project's internal plan,
after Phase 2 scoring completed. The internal plan stays private because it
carries project management beyond the spec; its section numbering is
preserved in cross-references here (§2 is the corpus and selection rule,
also internal). One redaction, disclosed: a machine-local backup path was
replaced with a generic location. Nothing else was changed.

Stated plainly rather than implied: this file's public history begins on its
publication date, which is after the dates inside it. The dates are the
record's own attestations, made in the internal plan as the decisions were
taken; publishing the spec makes its content inspectable, not its
timestamps. Results that this spec governs are in `RESULTS.md`.

---

### Extraction

- Per-field accuracy, Wilson interval on each.
- Population-weighted overall, **reported alongside the pooled estimate.** The
  gap between them is the finding, not a footnote.
- Three outcomes tracked separately: wrong value, hallucinated value, correct
  abstention. Most extraction evals collapse these; a null is cheap and a
  confident wrong number is expensive.
- False-extraction rate on the absence-prone fields.
- Optional bonus: rerun on the retained synthetic corpus and publish the
  clean-vs-real gap. Cheap, and nearly every LLM extraction demo reports the
  synthetic-equivalent number and calls it performance.

**Labeling protocol, fixed before labeling starts:**
- Label from the filing **before** looking at model output. Reviewing output and
  marking it right or wrong anchors to the model and silently inflates accuracy.
- Record absences as "correctly absent," never as blanks. Blanks are
  indistinguishable from unlabeled.
- Log ambiguous cases rather than resolving them silently. An ambiguous field
  definition produces "errors" that are really spec bugs and they contaminate
  the number.
- **Label what the filing states, never what it implies** (added 2026-08-09).
  The binding case is `goodwill_impairment` when goodwill sits on the balance
  sheet and impairment is never discussed — observed in real filings. The label
  is `null`, not `0`, even though "they evidently had none" is a sound
  inference. Rule 1 of the prompt forbids the model from inferring; holding the
  labeler to a looser standard makes the ground truth inconsistent with the
  instruction being measured.

**False-extraction denominator** (settled 2026-08-09): the rate is the share of
filings where the model returned a number *and the filing reported no charge* —
so the denominator is the `null` cases plus the `0` cases, not `null` alone.
This is both the more meaningful question and the reason no outcome-based issuer
stratification is needed.

### Label schema and matching spec — PRE-REGISTERED 2026-08-09

Fixed before any label was recorded and before any filing was extracted. The
reason for the timing is specific: **the matcher decides the accuracy number,
and matcher tuning is invisible.** Loosening the name rule or widening the float
tolerance moves the result with no visible trace, and unlike issuer selection —
which a reader can inspect — nobody can audit a matcher they never saw an
earlier version of.

**Label record.** One JSONL line per (filing, field); 351 records for the 39
extractable filings. The five over-window filings are not labeled, because there
is nothing to compare against until a chunker exists.

```json
{
  "accession": "0000320193-25-000079",
  "ticker": "AAPL", "period": "2025-09-27",
  "field": "total_assets",
  "status": "labeled",
  "answer_kind": "value",
  "value": 391035.0,
  "locator": {"section": "Item 8, consolidated balance sheet", "anchor": "Total assets"},
  "ambiguous": false, "note": "",
  "labeled_at": "...", "schema_version": 1
}
```

* `status` is explicit (`pending` / `labeled` / `skipped`). A missing record must
  be distinguishable from "labeled as absent", or the denominator is guesswork.
* `answer_kind` carries the three-way distinction, not the value:
  `value` · `stated_none` (compares as 0) · `not_addressed` (compares as null).
* `locator` is a pointer, **not a quote.** Verbatim text lives in a gitignored
  sidecar. 351 quotes at ~200 characters is 70 KB of filing text, and committing
  that starts to reconstruct the corpus.

**Matching spec.**

| Field | Rule |
|---|---|
| `company_name` | lowercase, strip punctuation and corporate suffixes (`Inc`, `Corp`, `Co`, `plc`, `Ltd`, `/DE/`), exact |
| `ticker` | lowercase, strip whitespace, exact |
| `fiscal_year_end` | parse both to ISO date, exact |
| `employees` | first integer from each side, commas stripped, exact |
| `total_assets` | relative tolerance 0.1% |
| `revenue_most_recent_fy` | relative tolerance 0.1% |
| `ceo_name` | surname equal AND **at least one given-name initial shared**, after dropping suffixes — *amended, see below* |
| `dividends_declared_per_share` | relative tolerance 0.1%, or both zero |
| `goodwill_impairment` | relative tolerance 0.1%, or both zero |

The 0.1% tolerance exists for unit-conversion rounding: a filing reporting
thousands converts to millions without landing on an exact float.

`ceo_name` is the weakest rule and is flagged rather than hidden. `Tim`/`Timothy`
passes on initial; `Robert`/`Bob` does not. The rule stays mechanical and
**name-field mismatches are reported separately**, so a reader can judge whether
they are substantive or cosmetic. Manual review of mismatches is *not* permitted
— it is a hole large enough to drive any number through.

#### AMENDMENT 1 — `ceo_name`, 2026-08-09

**Superseded rule:** surname equal AND **first**-initial equal.
**Replacement rule:** surname equal AND **at least one given-name initial
shared**.

**Reason.** Signature pages routinely render a middle-name-preferred officer as
`W. Rodney McMullen` where the body of the filing says `Rodney McMullen`. Under
the original rule the compared initials are `W` and `R`, so a substantively
correct answer scores as wrong. That is the implementation failing the stated
intent of the rule — "did the extractor identify the right person" — not the
output merely looking wrong.

**Timing.** Made before any label existed and before any filing had been
extracted, so no result could have informed it. This is the condition under
which amending is legitimate at all; after the first label exists, it is not.

**Cost, stated rather than buried.** The replacement is strictly more permissive:
it can only raise `ceo_name` accuracy relative to the original, never lower it.
Two officers sharing a surname and any given-name initial now pass. `Robert`/`Bob`
still fails under both rules. Implemented in `backend/evaluation/matching.py`
(`_match_person`), which carries the same limitation note, and exercised by
`test_matching.py`.

**Outcome grid.** The three outcomes required by this section fall out directly:

| label ↓ / prediction → | number | null |
|---|---|---|
| `value` | correct, or **wrong value** | **missed** |
| `stated_none` (0) | **false extraction** if nonzero | **missed** |
| `not_addressed` (null) | **false extraction** | **correct abstention** |

False-extraction rate = false extractions ÷ (`stated_none` + `not_addressed`).

### Labeling protocol — PRE-REGISTERED 2026-08-09, before the first label

Fixed before the labeling tool was written and before any label existed.

1. **Every `value` and `stated_none` label requires a `locator`** — a section
   and an anchor string that occurs in the filing. The tool refuses to record
   the label without one. `stated_none` is included because "the filing states
   it is zero" is a claim about text that exists and must be pointed at.

2. **Every `not_addressed` label requires the search terms that were tried**,
   recorded in `locator.searched`. `not_addressed` is the only label asserting
   a negative, it is unfalsifiable from the record alone, and it is the label
   the disclosed contamination below could bias. Requiring evidence of the
   search makes it the outcome of looking rather than of not thinking of
   anything — and it lets a reader re-run those terms against the filing.

3. **The queue is ordered by (ticker, period), fields in matching-spec order.**
   Both fiscal years of an issuer are labeled consecutively. The independence
   risk this creates is handled by rule 1: the second year's locator must
   anchor into the second year's document, so the value is re-read rather than
   carried over.

4. **The labeling tool cannot read model output.** Not "does not display it" —
   has no code path that opens `predictions.jsonl`, enforced by a test that
   fails if the file is opened during a labeling session. Convention is not
   sufficient protection for the property the whole measurement rests on.

5. **`ambiguous` is set by the labeler, not inferred**, and does not change the
   label. Ambiguous instances are scored in the headline number and reported
   again with them excluded, so the reader sees both.

### DISCLOSED CONTAMINATION — 2026-08-09, before labeling began

**The labeler learned one aggregate fact about the predictions before labeling:
across all 39 filings and all 351 instances, the model returned a non-null value
for every field. The observed abstention rate of the extractor is zero.**

How it happened, in two steps. The extraction run's per-filing progress line
reported "N/9 populated", which was justified at the time as mechanical status.
Per filing it nearly is; aggregated over 39 filings it is the abstention rate,
which is a reported result. That was a design defect. It was then compounded
in the act of describing the defect: the finding itself was stated in plain
text to the labeler rather than withheld. The console output was scrubbed and
`progress_line` no longer varies with what was extracted — but the disclosure
had already happened, and it cannot be reversed.

**What is and is not contaminated.**

- **No per-instance information leaked.** No value, and no per-field or
  per-filing breakdown. Nothing here identifies which filings or which fields
  the model answered in a particular way, because it answered all of them.
- **The affected decision is the three-way `answer_kind` call**, specifically on
  `dividends_declared_per_share` and `goodwill_impairment` — the two fields §5
  expects to be genuinely absent from many filings. A labeler who knows the
  model always produced a number could be pulled toward `value` and away from
  `not_addressed`.
- **The direction of any resulting bias is toward the model.** Labeling absent
  fields as `value` shrinks the false-extraction denominator and converts
  would-be false extractions into ordinary value comparisons. So this
  contamination can only flatter the extractor on the number it most affects.

**Handling.** Labeling proceeds; the alternative is discarding the corpus, and
an undisclosed re-do by the same labeler would be worse than a disclosed one.
The mitigations are the ones already pre-registered and they are unchanged: the
`locator` is mandatory, so every `answer_kind` must be justified by a pointer
into the filing, and `not_addressed` means the labeler looked and found nothing
rather than that nothing came to mind. **The false-extraction rate is reported
with this disclosure attached.** A reader who discounts that number entirely is
making a defensible choice, and the write-up says so rather than arguing them
out of it.

**Rule going forward: mechanical status is `ok` or `failed` and nothing else.**
Any statistic over the run, however aggregate it looks, is a result. Enforced by
`test_progress_line_is_identical_whether_or_not_extraction_happened`.

### Labels edited after being written — disclosed 2026-08-17

The first labeling sitting (2026-08-09, 17:57–24:03 local) wrote labels *before*
three tool defects were fixed, and records were rewritten afterwards. Recorded
here because the timing is otherwise unrecoverable, and a revision a reader
cannot see is indistinguishable from cherry-picking.

**What was rewritten, established by diffing two backups against the current
file rather than from memory.** Both backups are kept in
the data directory beside the repository (`label-backups/`), outside version control.

1. **Six records from the tool shakedown were replaced.** They were not labels.
   Three carried an empty `value` with a correct anchor, written before
   `f04edbe` made the tool reject empty labels. One carried the literal test
   value `999` against an invented anchor (`Total net sales $ 999`). One held an
   un-normalized date (`6/30/2024`). One anchored `total_assets` to the wrong
   balance-sheet row (`$ 15,310`), corrected to `$ 16,524`.
2. **One locator gained a section.** `AMCR 2024 goodwill_impairment` had
   `section: ""`, and `fdca112` auto-captured
   `Note 9 — Goodwill and Other Intangible Assets` from the nearest heading.

**No surviving label had its `value` or its `answer_kind` changed by a tool
fix.** The only value changes are the six shakedown records above, which
replaced empty strings and a test constant with read values.

**Verified rather than asserted:** every one of the 36 current labels is
anchored to filing text that contains its own value — `16524` to
`Total assets $ 16,524`, `41000` to `approximately 41,000`, `stated_none` on
goodwill to *"the Company concluded that goodwill was not impaired."*
`verify_labels.py` confirms all 36 anchors resolve in their filings.

**Why this is not an amendment.** No pre-registered rule changed. The matching
spec, the labeling protocol's five rules, and the outcome grid are all
untouched. This is a record of an instrument being repaired mid-use, which §5
already permits, plus the evidence that the repair moved no answer.

**Cost, stated rather than buried.** The scale-declaration fix (`19e77d5`,
`7c5ab90`) landed at 20:26 and 20:32, after labeling began at 17:57. A
thousands-denominated filing labeled before then could have been recorded
1000× wrong. Only AMCR 2024 was in progress in that window, its `total_assets`
and `revenue` were both rewritten afterwards, and both now agree with their
anchors — so the exposure is closed rather than merely argued away.

### The candidate finder was broken, and was repaired mid-labeling — 2026-08-17

**Measured, not assumed:** of the 43 anchors chosen by hand in the first two
sittings, **18 were never lit by the highlighter** — 58% coverage. The labeler
had been locating them with Ctrl+F, which is the finder's job done by hand on
42% of instances.

Worst case was the field that prompted the audit. `dividends_declared_per_share`
on CHTR FY2024 produced **zero** highlights under the old patterns, so the app
displayed *"none — use Ctrl+F"* for the one field whose absence-vs-value call
carries the false-extraction rate. Amcor's own anchor,
`Dividends declared ($0.4975 per share)`, matched none of the three original
patterns either: they required "declared **per**", and the money amount sits in
between.

**Repaired** in `FIELD_PATTERNS` (dividends, revenue, company_name, goodwill)
plus per-filing exact literals for the ticker symbol and registrant name, which
the manifest already knows and no pattern can express. Anchor coverage
**58% → 95%**; the two still dark are truncated anchors whose keyword was cut
off by the text selection, not finder misses. CHTR dividends went 0 → 2
locations, the first being *"Charter has not paid cash dividends on its common
stock…"* — the sentence that decides the label.

**Why this is an instrument repair and not an amendment.** No pre-registered
rule changed. The finder only orders the labeler's reading; §5 already states
it "never decides a label, and a field with no hit is not thereby absent."
Protocol rule 2 requires `not_addressed` to be the outcome of looking — a
`not_addressed` produced because the tool showed nothing is precisely the
defect that rule exists to prevent.

**Direction of effect, stated rather than buried.** Fewer missed values means
fewer wrong `not_addressed` labels, so the false-extraction denominator gets
smaller and more correct. **The repair is asymmetric:** the five filings labeled
before it (AMCR ×2, APP ×2, CHTR partial) were labeled under the weak finder.
AMCR's two dividend labels were independently confirmed against the filer's own
XBRL tags (below). APP's two are `stated_none`; the improved finder surfaces
non-payer sentences the old one could not, so those two anchors are worth
re-checking even though the `answer_kind` is unlikely to move.

### The second fiscal year: location reused, answer never — 2026-08-17

The labeler observed that an issuer's two filings put the evidence in the same
place, and asked whether the second year's review could be shortened. It can,
and the line is exact: **the location crosses years, the value does not.**

**The observation is correct and was already priced in.** §2 sized the corpus at
22 issuers × 2 years rather than 4 × 10 *because* consecutive filings from one
issuer are near-duplicates — same layout, same section order, the CEO on the
same signature page. Confirmed on the labels: the digit-stripped anchor is
**identical across years** for the numeric table fields — `Total assets`,
`Net sales`, `Dividends declared ( per share)`.

**What is safe to skip is the hunt. What is not is the read.** Protocol rule 3
names the carry-over risk explicitly, and **five of the nine fields change value
every year.** A carried-over answer is not weaker evidence, it is wrong data —
and it would be scored against a model that read the year correctly, so it
biases *against* the extractor while corrupting the ground truth.

**`/api/prior-hint` moves the cursor and nothing else.** Given this year's
filing and a field, it finds where the other year was anchored and returns
**three integers and a date** — `index`, `of`, `period`. Last year's anchor and
value never leave the server. That is a structural guarantee rather than a UI
convention, and it matters most for `ceo_name`, where the anchor *is* the
answer: any scheme that shipped the anchor to the browser and trusted the
template not to render it would be one edit from displaying last year's answer.

**Measured, on the filings already labeled: 26 of 28 hints land on the row the
labeler actually anchored**, 2 land in the right region one keypress away
(`AMCR goodwill_impairment`, `APP ticker`), 0 land somewhere unrelated. Coverage
rose from 2 of 6 fields to 7–9 of 9 once the matcher compared against the mark's
*surrounding text* rather than the mark alone — the anchor is a phrase the
labeler selected, a mark is only the keyword a pattern matched, and the two are
different shapes.

Below a similarity floor the endpoint returns no hint at all. A confident jump
to a plausible wrong row is the one outcome worse than no jump.

### RESOLUTION 1 — `dividends_declared_per_share` when only quarterly rates are stated, 2026-08-17

**An undecided case, not an amendment.** No existing rule changes. The prompt
(`openai_structurer.py:142`) asks for *"the per-share amount declared on common
stock for the most recent fiscal year"*, and §5 never said what to do when a
filing states only its quarterly declarations.

**Found on DGX FY2024**, which states no annual per-share figure. Its candidates
are `$0.75 per common share` (the FY2024 quarterly rate), `$0.71` (2023),
`$0.80` (announced January 2025) and `$3.20` (the 2025 annualised rate, tagged
`StatementScenarioAxis`). The label first recorded was `0.80` — wrong on period
*and* periodicity.

**Decided by the owner, 2026-08-17:**

1. **A stated annual per-share figure is always preferred.** Where the filing
   prints one, that is the label and this resolution does not apply.
2. **Where only quarterly declarations are stated, sum the ones the filing
   states** for quarters within the fiscal year.
3. **Mark such a label `ambiguous`, and record the arithmetic in the note.**
   Ambiguous instances are already reported both included and excluded, so a
   reader sees the headline number with and without every computed value.
4. **Anchor a stated quarterly declaration** — the locator still points at text
   in the filing. Prefer a span containing the word "dividend" so the ANCHOR-FIT
   check can see what the anchor is evidence for.
5. **Never sum a rate assumed to have held for quarters the filing does not
   state.** Four quarters at a rate stated once is an inference about three of
   them.
6. **Subsequent-event declarations are excluded.** A dividend announced after
   the fiscal year closed belongs to the next year, whatever its tagged concept
   says. This is the specific error the resolution was written for.

**Why summing is permitted when inferring is not.** The distinction is that
**summing stated numbers is arithmetic; concluding from silence is inference.**
Every quarterly declaration being added is a fact the filing prints, so the
total asserts nothing new. That is categorically unlike `goodwill_impairment`,
where rule 6 forbids reading silence as 0 — there the labeler would be asserting
a fact the filing never states. Rule 5 above is what keeps this from sliding
into the same error.

**Cost, stated rather than buried.** A label of `3.00` against a model that
returns `0.75` or `0.80` scores as a miss. That is a real measure of difficulty
rather than a spec bug, *because the prompt asks for the fiscal-year amount* —
and the alternative, choosing the label to match the model's likely reading,
would be selecting the ground truth to flatter the result.

**Timing, disclosed.** Decided after 80 labels existed, and it changes none of
them. All eight `dividends_declared_per_share` labels at the time were either
`stated_none` (APP ×2, CHTR ×2) or stated annual figures independently confirmed
against annual XBRL contexts (AMCR `0.4975`/`0.5075`, CTSH `1.20`/`1.24`). The
resolution therefore governs future instances only, and no result existed for it
to have been fitted to.

### CORPUS DEFECT FOUND AFTER LABELING — PGR incorporates its statements by reference, 2026-08-18

**The failure §2 pre-registered a check for happened, and the check missed it.**
Both PGR filings' primary documents contain **no consolidated financial
statements**. The only statements present are Schedule II — condensed,
parent-company-only. "Consolidated Balance Sheets" appears exactly once in each
document: as a bullet in the incorporation-by-reference list. The real
statements live in the Annual Report exhibit, which the corpus does not fetch.

**The manifest says `has_balance_sheet: true, has_income_statement: true,
item_8_by_reference: false` for both PGR rows.** The detector was fooled by
precisely the text that fooled the labeler: the caption `Total assets` exists
(in Schedule II) and the words "Consolidated Balance Sheets" exist (in the
reference list). PGR's two filings are also the corpus's smallest (38,451 and
39,108 tokens) — small *because* the statements are elsewhere, which is the
tell a size check cannot see, as §2 said.

**Four labels are affected, all recorded from parent-company-only figures:**
`total_assets` (both years, anchored to Schedule II's `Total assets` — the only
occurrence of that caption in the document) and `revenue_most_recent_fy` (both
years, anchored to Schedule II's `Total revenues`). The XBRL agrees: the only
`us-gaap:Assets` facts in the filing carry
`ConsolidatedEntitiesAxis=ParentCompanyMember`. `goodwill_impairment` was
labeled `not_addressed` on both, which is correct — the goodwill note is in the
exhibit too. Cover-page and Item 1 fields are unaffected.

**OPEN DECISION — not made unilaterally, recorded for the owner.** The
protocol says label what the filing states, and the fetched document does not
state the consolidated figures; the parent-only figures are stated but are not
the field. The recommendation is therefore **`not_addressed`** for the four
affected instances, which keeps the ground truth consistent with the text the
model actually reads — a model returning the Schedule II number would then be
scored as a false extraction against the field's definition, and a model
returning null scores as correct abstention. The alternative reading — that
these two filings are defective rows for statement-bound fields and should be
reported as a corpus limitation alongside the coverage number — is also
defensible; what is not defensible is deciding after results exist, so this is
decided now or disclosed as undecided. Either way the fetch-time detector's
false negative is disclosed here rather than repaired silently, and repairing
it does not change any committed measurement (the manifest's coverage numbers
are token counts, which are unaffected).

**Decided by the owner, 2026-08-18, after the evidence was re-derived in
session:** `not_addressed` for all four affected instances. The fetched
document does not state the consolidated figures, so the ground truth stays
consistent with the text the model reads — a model returning the Schedule II
parent-only number scores as a false extraction, and a model returning null
scores as correct abstention. The denominator stays 351. The alternative
(declare the two filings defective rows for statement-bound fields and shrink
the denominator) was considered and declined. Decided before any scoring ran;
the four labels were relabeled the same day, with the removed records backed
up outside the repo by `relabel.py`.

**Extended to `dividends_declared_per_share`, same day (2026-08-18), during
the dividends pass.** The defect reaches this field too: both primary
documents discuss dividend policy and state aggregates (2024: $674 million
paid, $2,695 million payable) but state **no per-share amount of any kind**
— verified exhaustively, the only "$ per share" figures are the preferred
liquidation preference and common par value — and tag no per-share dividend
concept. The per-share figures live in the Annual Report exhibit. Neither
resolution can produce a label: RESOLUTION 1 has no stated quarters to sum,
RESOLUTION 2 no stated per-share paid figure to take, and deriving one from
the aggregate over the share count would assert a number the filing never
states. **Owner decision: `not_addressed` for both PGR dividend instances,
labeled through the app with the searched terms recorded.** Clarification,
so the rules do not appear to conflict: RESOLUTION 2 clause 6 ("not_addressed
remains wrong when dividends are discussed") governs vocabulary substitution
where a per-share figure **is** stated; it does not reach a fetched document
that states no per-share amount at all.

### RESOLUTION 2 — `dividends_declared_per_share` when a filing states only the amount *paid*, 2026-08-18

**A second undecided case, not an amendment.** The prompt asks for the per-share
amount **declared**. Some filers never state one.

**Found on EXR.** The word "declared" occurs three times in the whole filing —
REIT boilerplate ("distributions when declared by our board"), a registration
statement "declared effective", and a derivatives default clause. None is an
amount. What EXR states is `Dividends paid on common stock at $X per share` in
the statement of stockholders' equity, tagged
`us-gaap:CommonStockDividendsPerShareCashPaid`. DVN is the same.

**Decided by the owner, 2026-08-18:**

1. **A stated *declared* per-share figure is always preferred.** Where the
   filing prints one, this resolution does not apply.
2. **Where the filing states only the amount paid, label that figure.**
3. **Mark it `ambiguous`, and say in the note that the filing states paid
   only.** The distinction then reaches the reader instead of dying here.
4. **Anchor the stated paid figure**, as always.
5. **This does not license reading paid for declared when both are stated.**
   If a filing gives both and they differ, the declared figure is the answer.
6. **`not_addressed` remains wrong when dividends are discussed.** That label
   means the filing does not address dividends, not that it uses different
   vocabulary.

**Why paid is accepted rather than refused.** The alternative puts a company
that demonstrably pays a dividend into the false-extraction denominator as
though its filing said nothing — which misrepresents the filing and corrupts
the very rate the field exists to measure. For a regular quarterly payer the
two coincide except at timing edges.

**Cost, stated rather than buried.** They are not the same quantity. A dividend
declared in December and paid in January falls in different fiscal years, so on
those filings the label may differ from what a model reporting *declared* would
return, and that instance scores as a miss. The `ambiguous` flag is what keeps
this visible: §5 already reports ambiguous instances both included and excluded.

**Timing, disclosed — and this one does touch existing labels.** Decided after
156 labels existed. Of the sixteen `dividends_declared_per_share` labels at the
time, twelve came from filings tagging a *declared* figure (AMCR, CTSH, DGX,
DOW, DPZ) and four were `stated_none` (APP, CHTR) — none affected. **The two DVN
labels came from a paid-only filing and are therefore governed by this
resolution**, which is why they were re-labeled under it rather than left
standing. EXR's two were unlabeled at the time.

### DETECTOR REPAIR — statement presence is now decided by the filer's tags, 2026-08-18

The repair the CORPUS DEFECT entry promised. It changes no committed
measurement: token counts are untouched, and the four corrected flags plus the
retired field are corpus bookkeeping, not results.

**The old detector was wrong in both directions, not just on PGR.** The
caption regexes marked PGR healthy because "Consolidated Balance Sheets"
appears in its incorporation-by-reference bullet list, and the
`item_8_by_reference` regex required "Item 8" *before* "incorporated by
reference" while PGR writes the reverse order. Separately — found while
repairing — the committed manifest flagged **eight healthy filings**
(GWW, LLY, WYNN, PCG ×2 years) as `item_8_by_reference: true` from Item 3
legal-proceedings cross-references *into* Item 8. And prose cannot decide the
question at all: PG's Item 15 prints "are incorporated by reference in
Part II, Item 8 of this Form 10-K" about statements physically present in its
Item 8; PGR prints nearly the same sentence about statements in an exhibit.

**New rule.** These are inline-XBRL documents, so a statement that is
physically present has its displayed figures wrapped in tags.
`has_balance_sheet` := at least one **undimensioned** `us-gaap:Assets` fact;
`has_income_statement` := at least one undimensioned fact from the audit's
revenue concept list (`FIELD_CONCEPTS` reused — fetch and audit share one
definition of the top line). "Undimensioned" is the audit's own helper for
the consolidated figure of the primary registrant. `us-gaap:NetIncomeLoss`
was measured and rejected as a signal: DOW, MA, PCG, and SO 2024 tag only
attributable-to-parent variants (0 undimensioned) while healthy.
`item_8_by_reference` is **retired**, not repaired.

**Validated 2026-08-18 over all 44 local documents:** every healthy filing
tags ≥2 undimensioned Assets facts and ≥3 undimensioned revenue facts; both
PGR documents tag 0 of each. The manifest was re-described offline from
sha256-verified local files: 4 flag corrections (PGR ×2 filings ×2 fields),
the retired field dropped from all 44 rows, defects now
`missing_financial_statements: [PGR 2025-12-31, PGR 2024-12-31]` (previously
empty, while the old block listed the eight healthy filings under
`item_8_by_reference`).

**Tests, red before green, perturbed.** `tests/test_statement_detector.py`:
five contract tests plus all 44 documents parametrized (marker `corpus`,
skips cleanly without local filings). Red first against the caption logic —
six failures including both real PGR documents — then green under the new
rule. Perturbed three ways (undimensioned filter dropped, caption-regex
regression, emptied concept list): all caught. The manifest guard evolved
from "no defects" to "defects equal the disclosed set exactly"
(`test_no_undisclosed_filing_is_missing_its_financial_statements`,
`test_the_retired_by_reference_flag_stays_retired`); it went red against the
repaired manifest before being evolved, and its perturbations (retired flag
re-added, new undisclosed defect, PGR papered over) were all caught.

### Label defects found by the labeler, and the guards added — 2026-08-17

The labeler asked whether `goodwill_impairment` should be *total goodwill*. It
should not, and the question found two real defects that
`verify_labels.py` had passed as clean.

**Defect 1 — wrong quantity.** `goodwill_impairment` was labeled `6085` on both
CTSH filings from the anchor `Total goodwill $ 6,085`. That is the **carrying
balance**, which the field guidance names by name: *"The goodwill carrying
balance is not an impairment."* Anchor-existence checking passed it, because the
text is real and in the right filing — it is simply evidence for a different
quantity. Both years were labeled 49 minutes apart, so this was read twice and
got the same wrong answer twice.

**Defect 2 — wrong period.** `DGX 2024 dividends_declared_per_share` was labeled
`0.80` from the anchor `0.80 per common share`. In the filing's own XBRL, `0.80`
is tagged to **2025-01-01 → 2025-01-31 under `SubsequentEventTypeAxis`** — a
dividend declared after the fiscal year closed. The FY2024 quarterly
declarations tag at `0.75`. Found by running `scripts/xbrl_facts.py` against
the anchor that the new guard flagged.

Both are corrected by re-labeling, not by editing the record. `scripts/relabel.py`
removes specific instances so the queue serves them again — the app's undo pops
only the most recent label, which is useless once a defect surfaces six filings
later, and hand-editing a JSONL changes the denominator with nothing to reveal it.

**Two guards added, both reporting as WARN rather than failing the run.**
Neither is proof of an error, and a check that cries wolf sends the labeler back
over work that was already right.

1. **ANCHOR-FIT** — the anchor must mention the field's own subject: `impair`
   for goodwill, `dividend`/`distribution` for dividends, and so on. Only fields
   where a keyword is genuinely obligatory get a rule; `ceo_name` and
   `company_name` are names with no obligatory neighbouring word and are exempt.
2. **CARRY-OVER** — both fiscal years holding the same value **and** the same
   anchor text. This is the deliberate counterweight to `prior_hint` above:
   that feature starts the second year on the first year's row, which raises the
   risk protocol rule 3 names, so the risk is made visible afterwards. Both
   conditions are required — values legitimately repeat, identical sentences
   with identical spacing across two different documents do not. `ticker` and
   `company_name` are exempt because they *should* match.

Run over the 80 labels existing at the time, the guards produced 8 warnings:
2 true defects (CTSH goodwill, both years), 1 that led to defect 2 above, and
5 anchors that are merely truncated or terse — `As of June 30, 2024, we had
approximately 41,000` lost the word "employees" to the text selection. Those
last are locator-quality findings, not value errors.

### LLM-assisted labeling — considered and rejected 2026-08-17

The labeler asked whether a hard-to-find field could just be run through an LLM.
**Rejected, and the reason is recorded because the request was reasonable.**

A label produced by a model makes extraction accuracy on that field a comparison
between a model and a model. `dividends_declared_per_share` is the worst
available field for it: it is one of the two absence-prone fields carrying the
false-extraction rate, and that rate already carries the disclosed contamination
above — biased in the same direction, toward the extractor.

**What replaces it: the filer's own inline XBRL.** These are iXBRL documents —
AMCR FY2024 carries 2,281 tagged facts — and the dividend is among them as
`us-gaap:CommonStockDividendsPerShareDeclared`, `unitRef="usdPerShare"`. That is
the *registrant's* structured assertion, inside the document being labeled, so
reading it is reading the filing rather than consulting a second extractor.
`scripts/xbrl_facts.py` prints every tagged fact for a field with its resolved
reporting period.

**It is a finder, not an oracle, and the distinction is load-bearing.** AMCR
FY2024 tags the dividend four times: FY2022, FY2023 and FY2024 comparatives plus
a subsequent-event quarterly declaration. The script prints all four with their
periods and never a single answer; choosing the fiscal year under label, and
anchoring it in selected text, stays with the labeler. It is deliberately a
standalone script and is **not** wired into the labeling app, whose isolation
from model output is enforced by tests.

Used this way it confirmed both AMCR dividend labels exactly — `0.4975` against
the 2023-07-01→2024-06-30 context, `0.5075` against 2024-07-01→2025-06-30.

Absence of a tag is **not** evidence of absence in the filing: filers tag
inconsistently, and a value stated only in prose is untagged. The script says so
in its own output rather than leaving that inference to the reader.

### Instrument stability, measured before accuracy

`structure_text` runs at `temperature=0.1`, so the extractor is not obviously
deterministic. Before any accuracy number exists, run one filing through
extraction three times and diff the nine fields.

- Identical across runs → set `temperature=0`, record it, proceed.
- Not identical → the instrument has run-to-run noise, and every accuracy figure
  needs that noise characterized, because otherwise a model error cannot be
  distinguished from a sampling artifact.

Either outcome is worth having. Doing this after labeling would be discovering
the thermometer is unreliable after taking the patient's temperature.

### Retrieval

**50 answerable queries with known gold passages**, plus **15 unanswerable** for
abstention. (An earlier estimate of 30 was too small — the interval lands near
±15 points, too wide to support any claim.)

Stratified:
- Exact-entity (20): tickers, subsidiary names, defined terms, section titles
- Conceptual (20): paraphrase, no lexical overlap with the source passage
- Mixed (10)

**Three arms: sparse-only, dense-only, hybrid.** Shipping hybrid without the
ablation is an unmeasured assertion. Expected result — lexical wins stratum one,
dense wins stratum two, hybrid wins overall, and the pooled number hides all of
it. If that is not what the data says, publish what the data says.

Report recall@1 and recall@5 per arm per stratum. Abstention rate on the
unanswerable set, per arm.

---

## Appendix — chunk assembly, pre-registered 2026-08-18

Added to this published record on 2026-08-18, after the section splitter was
built and **before any chunk, any index, or any retrieval number existed.** It
is drawn from §4 of the internal plan rather than §5, which is the only reason
it sits in an appendix instead of the body above; the header's "verbatim §5
extract" describes everything before this line.

It is published for the same reason the retrieval design above is: chunk size
moves recall@k, so a size chosen after seeing recall@k would be a dial rather
than a decision, and a reader cannot check that ordering unless the parameters
are visible beforehand.

**Measured first.** Over all 44 corpus documents, the section splitter yields
**1,000 sections: median 117 tokens, p75 1,032, p95 22,154, max 238,240.**
`Item 8` alone has a median of 25,510 tokens. A section is therefore sometimes
one sentence and sometimes tens of thousands of tokens, and neither is a
passage — which is why chunk assembly needs parameters at all.

**CORRECTION — the figures in the paragraph above are wrong, 2026-08-19.** They
are kept rather than edited, because a revision a reader cannot see is
indistinguishable from a quiet fix.

Building the store over all 44 filings showed that section detection was
chaining contents-table entries to the handful of headings printed near the end
of a document. A contents table lists every Item in canonical order by
construction; a body does not, because some headings are printed in a form the
pattern did not match. The mixed chain was therefore *longer* than any chain
drawn from the body alone and won on length. What that produced:

- **SO** put 91% of its filing under one section labelled `Item 13 Certain
  Relationships and Related Transactions`. The **max 238,240** quoted above is
  that section, and the p95 of 22,154 is inflated by the same defect.
- **DVN** put 82% under `Item 9C`.
- **HON** delivered **83 of 11,907 non-blank lines — 0.7%** — because every
  `Item N` line in the document sits in a cross-reference index after its
  signature block, and no body heading was matched at all.
- The claim that every filing yields Items 1/1A/7/8 was true only because a
  contents-table stub counts as a section.
- Independently: the last section ran to end of document, so for the 12 filings
  that incorporate Item 8 by reference the financial statements landed under
  `Item 15` or `Item 16`. CHTR and DGX carried ~50% of their chunks under
  `Item 16 Form 10-K Summary` — a citation naming a section the text is not in.

**Corrected measurement, after the repair:** **100.0% of the corpus text
(377,270 of 377,270 non-blank lines) now falls inside a detected section**, up
from 90.9%, and **11,621 chunks** are built from 44 of 44 filings. Chunks
labelled `Item 13` fell from 1,299 to 42 and `Item 16` from 1,226 to 166, while
`Item 8` rose from 2,378 to 3,668. The largest section in every filing is now
either `Item 8` (32 filings) or the item-less front matter and post-signature
tail (10).

The repair changed **where sections begin and end**, and nothing about chunk
size or overlap: 512/64 stand as fixed below. It was made before any index
existed, before any query was written, and before any retrieval number existed,
so no result could have been tuned toward — and none is comparable across it,
because none was computed before it. The rules, the four acceptance criteria
fixed before the repair was written, the two of those criteria that turned out
to be wrong, and the one filing that still does not parse cleanly (HON, whose
Items 1 and 7 remain unlabelled because its document order is not canonical)
are recorded in the project plan.

**Fixed, 2026-08-18:**

1. **Target 512 tokens per chunk, 64 tokens of overlap** (~7,600 chunks over
   3.88M section tokens).
2. **Split on paragraph boundaries**, never mid-paragraph. A single paragraph
   longer than the target becomes its own oversized chunk rather than being cut.
3. **A chunk never spans two Items** — the citation names one section, and a
   chunk straddling a boundary would make that citation false.
4. **Small sections are kept whole**, never merged with a neighbour.
5. **Every chunk carries** accession, ticker, period, item, section title, and
   the page range its text falls on. Pages come from the `<hr>` page rules,
   which are present in all 44 documents and whose count matches the
   `page-break` CSS count exactly in every one.
6. **Tokens are counted with the model's own encoding**, not characters or
   words.

**Clarification, 2026-08-18 — overlap is quantised to whole blocks.** Made
while implementing, before any chunk was indexed and before any retrieval number
existed. Rule 2 forbids cutting a block, so overlap can only be carried in whole
blocks, and a strict 64-token budget then produces **no overlap at all** on
ordinary filings — a prose block runs 80-plus tokens against a 64-token budget.
Zero overlap is the failure this parameter exists to prevent. The rule is
therefore: **always carry back at least one whole block, and let the 64-token
budget decide how many additional blocks follow.** *Direction of effect:* the
effective overlap is larger than 64 tokens, never smaller, so marginally more
text is duplicated across chunks — a change in the retriever's favour, disclosed
as such.

**A second measurement, recorded because it changed the implementation.** The
atomic block is a **line**, not a blank-line-separated paragraph. In extracted
filing text one line is one block element — a heading, a paragraph, or a table
cell — and filings run heading, paragraph, heading down consecutive lines with
no blank between them. A blank-line rule was written first and returned whole
sections as single blocks, so chunking silently did not happen at all. The
corpus is unambiguous: the median non-blank line is 3 characters, because most
lines are table cells, while prose lines reach 2,198.

**Cost, stated rather than buried.** 512 is a convention, not a measurement.
The alternative — running two chunk sizes as declared arms and publishing both —
was considered and declined for scope. So retrieval quality here is reported
**at one chunk size**, and this project cannot say whether another size would do
better. If a second size is ever run it is reported as an additional arm, with
this appendix amended, and never as a replacement for the first set of numbers.
