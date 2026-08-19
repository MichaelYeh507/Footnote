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

#### AMENDMENT 3 — QUERY STRATA RESTRATIFIED, 2026-08-19

Written before a single query was written, before either index existed, and
before any retrieval number existed.

**The defect, measured with this project's own instrument.** The strata above
are 20 exact-entity / 20 conceptual / 10 mixed. Run through
`evaluation/wilson.py`, a stratum of n=10 at a recall of 0.80 gives
**[0.490, 0.943] — ±22.7 points.** The very paragraph being amended raised the
query count from 30 to 50 on the ground that *"the interval lands near ±15
points, too wide to support any claim"*, and §3 gates any claim below n=25. The
Mixed stratum as pre-registered would therefore publish a row that fails both of
this document's own tests, and it would fail them by a wider margin than the
design that was already rejected for failing them.

**The change.** **25 exact-entity + 25 conceptual.** The Mixed stratum is
dropped. The total is unchanged at **65 queries — 50 answerable + 15
unanswerable**; only the split between strata moves. Measured at 0.80:

| stratum | n | Wilson at 0.80 | half-width |
|---|---|---|---|
| as pre-registered, Mixed | 10 | [0.490, 0.943] | ±22.7 pts |
| as pre-registered, each of the other two | 20 | [0.584, 0.919] | ±16.8 pts |
| **amended, each stratum** | **25** | **[0.609, 0.911]** | **±15.1 pts** |
| pooled answerable, unchanged | 50 | [0.670, 0.888] | ±10.9 pts |

Both reported strata now clear the n=25 gate exactly, and the pooled answerable
figure that carries the headline claim is computed on the same 50 queries either
way.

**Why Mixed goes rather than the other two shrinking.** A query is mixed by
*degree*, not by kind: every conceptual query about a named issuer carries some
lexical overlap, so the boundary would have been drawn query by query by the
person writing them. That is a dial, and it sits in the one place this project
can least afford one — the definition of the stratum, which decides which
queries a stratum's number describes. The two surviving strata are separable by
a rule that can be stated in advance and checked afterwards: **a query is
conceptual if it shares no content word with its gold span**, and exact-entity
otherwise.

*Direction of effect, stated rather than buried:* it is **not obvious** which
way this moves the hybrid arm's case, and asserting a direction would be a
guess. Mixed queries — the ones carrying both a lexical and a semantic handle —
are plausibly where fusing two partially-successful rankings has the most to add
over either arm alone, so removing them may remove hybrid's most favourable
ground. Against that, the pooled answerable figure is computed on 50 queries in
either design. What can be said without guessing is that the change was made
before any query was written and before either index existed, so it cannot have
been made toward a result.

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

---

## Appendix — retrieval parameters and the hit definition, pre-registered 2026-08-19

Added to this published record on 2026-08-19, **before either index
existed, before a single vector was computed, and before any query was
written.** Like the chunk-assembly appendix above it is drawn from §4 of
the internal plan rather than §5, which is the only reason it sits in an
appendix rather than the body.

It is published for the same reason everything else here is: each
parameter below moves recall@k, so one chosen after seeing recall@k would
be a dial rather than a decision, and a reader cannot check that ordering
unless the parameters are visible beforehand. The `BM25` correction in
part 2 refers to a sentence in the internal plan that has never appeared
in this file; it is carried across so that the public record shows the
correction rather than only the corrected version.

**1. Dense arm.**

- **Model `text-embedding-3-small`, 1,536 dimensions** — the model's native
  size, with no Matryoshka truncation, so there is no dimension parameter that
  could later be claimed to have been chosen for the result. 1,536 also sits
  under pgvector's 2,000-dimension ceiling for an HNSW index on the `vector`
  type, so the storage type is the obvious one and no `halfvec` workaround has
  to be explained away.
- **Distance: cosine.** Index opclass `vector_cosine_ops`, query operator
  `<=>`. Written once, in one place, because an HNSW index built on one opclass
  is simply not used by a query written with another operator, and that failure
  is silent — a correct answer, arrived at slowly by sequential scan.
- **HNSW `m = 16`, `ef_construction = 64`** (pgvector's defaults), query-time
  **`hnsw.ef_search = 100`**. `ef_search` below the requested depth degrades
  results quietly; 100 clears the depth of 50 fixed below by a factor of two.
- **What is embedded: the chunk's `text` field, verbatim.** No title prefix, no
  ticker or item header, no instruction prefix. `text-embedding-3-*` is
  symmetric — it has no separate query and document prefixes — so the query is
  embedded exactly the same way.
- **Measured before embedding, so the truncation question is settled rather
  than assumed:** 11,621 chunks, 4,890,354 tokens, median 460, **max 809**. The
  largest chunk in the corpus is an order of magnitude below the model's input
  limit, so **nothing is truncated** and every vector describes the whole
  passage its citation names.

**2. Sparse arm.**

- **`tsvector` configuration `english`** — Snowball stemming and the English
  stopword list. The sparse arm is the baseline the hybrid arm has to beat, and
  a baseline configured to lose is not a measurement. `simple` was considered
  and declined: it would hand the conceptual stratum to the dense arm by
  construction rather than by measurement, since "impaired goodwill" would no
  longer reach "goodwill impairment".
- **Stored as a generated column**, `to_tsvector('english', text)`, over the
  same `text` field the dense arm embeds. Both arms therefore see byte-identical
  passages, which is the entire reason `scripts/build_chunks.py` materialises a
  store instead of each index reading the filings for itself.
- **A query becomes an OR of its lexemes, not an AND.** This is the largest
  single choice on this page and it is disclosed as such. `plainto_tsquery` and
  `websearch_to_tsquery` both AND every term, and against a twelve-word
  conceptual query over 512-token passages the modal outcome of an AND is zero
  rows — which would publish a parser artifact as a property of lexical
  retrieval. The lexemes are taken from `plainto_tsquery('english', …)` and
  joined with `|`.
  *Direction of effect, stated rather than buried:* OR can only raise the sparse
  arm's recall relative to AND. It moves the number in the **baseline's**
  favour, against the hybrid arm this project is building, which is the
  direction an ablation should err in.
- **Ranking: `ts_rank_cd`, normalization flag 0** — no length normalization.
  Cover density rewards passages whose matched terms sit close together. Length
  normalization is declined because the chunker has already fixed length: these
  passages run to a 512-token target with a measured median of 460, so dividing
  by document length would correct a variation that chunk assembly removed by
  construction.
- **CORRECTION, 2026-08-19.** The fusion paragraph above says *"BM25 scores and
  cosine similarities"*. **Postgres has no BM25.** `ts_rank_cd` is a
  cover-density score and the two are not the same function. The argument for
  RRF is untouched — `ts_rank_cd` scores and cosine similarities are on
  incomparable scales for precisely the reason given there — but the name was
  wrong, and it is corrected here beside the original rather than edited away.
  The word appears only in this internal plan; it is in neither the published
  `EVALUATION-SPEC.md` nor `RESULTS.md`.

**3. Fusion.**

- **RRF constant `k = 60`**, the value published with the method (Cormack,
  Clarke and Buettcher, 2009). Fixed now and never tuned: a `k` chosen after
  seeing recall@k is a dial, and nothing in a published number would reveal that
  it had been turned.
- **The formula, written out so it cannot drift:**
  `score(d) = Σ over arms of 1 / (60 + rank_arm(d))`, ranks **1-based**, and a
  document absent from an arm's list contributes **nothing** from that arm.
- **Fusion depth: the top 50 from each arm.** A document ranked 51st by one arm
  cannot be rescued by the other. 50 is ten times the deepest reported cutoff.
- **Ties break by `chunk_id` ascending.** Arbitrary, but deterministic and
  independent of every arm's score, so recall@1 is reproducible and the
  tie-break cannot favour an arm.

**4. What counts as a hit — the definition everything else rests on.**

Gold is a **document location**, never a chunk id:

> **(accession, quoted span)**, one or more per query.

- **A retrieved chunk is a hit if and only if it comes from a gold accession
  and its text contains that gold span**, under the normalization below. `item`
  is recorded on every query for stratification and for reporting, and **is not
  part of the hit test** — see the HON paragraph below.
- **The gold chunk set is derived from the store at scoring time**, not frozen.
  It is *every* chunk whose text contains the span, and **recall@k = 1 for a
  query if at least one of them appears in that arm's top k.** With 64 tokens of
  overlap a span near a boundary legitimately sits in two chunks; that is
  correct semantics rather than a defect, because the question being asked is
  whether the answering text was put in front of the reader.
- **Normalization, fixed now and applied identically to gold span and chunk
  text:** casefold; collapse every run of whitespace, newlines included, to a
  single space; fold curly quotes to straight and en/em dashes to hyphen.
  Nothing is stripped and no punctuation is removed. The folding is not
  cosmetic — the store holds **13,603** curly apostrophes and **21,839**
  em-dashes, so a span quoted with a typewriter apostrophe would otherwise miss
  a passage that plainly contains it.
- **Gold spans are quoted from the extracted text**, which is what the store
  holds, and never from a browser rendering of the filing.
- **Validation guard, run before any arm runs:** every gold span must match at
  least one chunk in the current store. A span matching zero chunks is a broken
  query, not a retrieval failure, and the query set is refused until it is
  fixed. The guard reads **the store**, not any retriever's output, so it is the
  same class of act as reading the filing and it does not break the blind.
- **No metadata filtering.** Every query searches all 11,621 chunks in every
  arm. Restricting a search to one filing or one Item is a different and much
  easier task, and reporting it as retrieval over the corpus would be a false
  claim.

**Why a location and not a chunk id.** A chunk id is an artifact of a chunker
version. Freezing gold to chunk ids would make the whole query set worthless the
next time the chunker changes — and AMENDMENT 2 is the standing proof that this
chunker can change — and it would additionally score as a miss a chunker that
split the passage differently while still returning the text. *The honest cost,
stated:* because the gold set is derived, recall numbers are not comparable
across chunker versions either. What a location-based rule buys is that the
**query set** survives a re-chunk, not that the numbers do. The obvious attack
on a span rule — a chunker that satisfies every query by emitting one enormous
chunk — is already closed by the 512-token cap pre-registered above and
published in `EVALUATION-SPEC.md`.

**Why `item` is excluded from the hit test.** HON's Items 1 and 7 are not
separately labelled, and that text is retrievable and page-cited under
`Item 1B` — the limitation disclosed in AMENDMENT 2's outcome above. If item
equality were required for a hit, every query whose gold sits in HON's Item 1 or
Item 7 would score a miss on account of a chunker artifact this project has
already published as *not* a retrieval failure. That would import a known
labelling limitation straight into the headline number. Item stays as metadata,
where it can be reported without contaminating anything.

**AMENDMENT 4 — A CAP ON THE GOLD SET, 2026-08-19.** Written the same day as
the rule it amends, after measuring that rule against the store, and still
before any query was written, before either index existed and before any
retrieval number existed.

**The defect.** Part 4 refuses a span matching **zero** chunks. It does not
refuse a span matching **too many**, and the store says that is the larger
hole. Measured by taking a span out of a real chunk and asking how many chunks
it matches within its own filing — median filing 250 chunks:

| span length | n | median gold set | mean | max | exactly 1 |
|---|---|---|---|---|---|
| 4 words | 96 | 2 | 10.74 | **382** | 9/96 = 0.094 |
| 7 words | 96 | 2 | 4.55 | **155** | 13/96 = 0.135 |
| 12 words | 94 | 2 | 2.07 | 14 | 17/94 = 0.181 |
| 20 words | 94 | 2 | 1.88 | 14 | 28/94 = 0.298 |

The **median of 2 is correct and expected**: it is the 64-token overlap putting
a boundary-crossing span into two chunks, which part 4 already describes as
correct semantics. The **tail** is the defect. A gold set of 382 chunks is
larger than a whole median filing, and recall@5 against it is satisfied
essentially by accident. That query would enter the pooled number as a success,
and the failure is silent **in the direction that flatters the retriever** —
which is the direction that matters.

**The rule, fixed now.** A query's gold set may hold **at most 5 chunks**,
counted as the **union** across all of that query's locations. Above that the
query set is refused, in exactly the same way and for exactly the same reason
as a span matching zero: it is a defect in the query, not a result from the
retriever. Five sits above what overlap plus ordinary repetition produces — a
phrase appearing in both the MD&A and the notes reaches three or four — and far
below the boilerplate regime. It is also close to 2% of the median 250-chunk
filing.

The cap bounds the **union** rather than each location separately, because the
union is what decides how easy recall@k is. A query naming several locations
must therefore keep its total gold set inside the cap, which in practice means
two or three locations.

**A span-length floor was considered and is guidance, not a guard.** The
measurement is explicit that length does not fix the tail: the maximum is still
**14 chunks at both 12 and 20 words**. A 12-word minimum is recorded as written
guidance for whoever writes the queries, and is reported as a non-blocking note,
but what actually refuses a query is the cap.

*Direction of effect, stated rather than buried:* this can only **lower**
reported recall, never raise it, because it removes queries that were easy to
satisfy. It is a change against the retriever's interest, made before any
retriever existed.

*Why this is legitimate now.* It is the test §5 sets for amending a
pre-registered rule: the rule as written demonstrably fails its stated intent.
The stated intent is that a hit means the arm put the answering passage in front
of the reader; a 382-chunk gold set means almost any five chunks from that
filing satisfy it. No index existed, no query existed, and no retrieval number
existed when this was measured, so nothing here can have been tuned toward a
result.

**AMENDMENT 5 — DUPLICATE SPANS ARE FLAGGED, NOT REFUSED, 2026-08-19.** Decided
after both indexes were built and loaded, and still **before any query was
written**. It adds a non-blocking advisory; it changes no scoring rule.

**How it was found.** A dense-index sanity check — "a chunk is its own nearest
neighbour" — failed against a *correct* index, because two chunks sat at cosine
distance exactly 0. Their text is byte-identical.

**Measured over the loaded store:**

| | |
|---|---|
| groups of byte-identical chunk text | **217** |
| chunks involved | **448 / 11,621 = 3.9%** |
| groups that are **one issuer's two fiscal years** | **214 (98.6%)** |
| groups spanning **different issuers** | **3**, totalling **20 chunks (0.17%)** |
| groups larger than a pair | 3 (sizes 3, 7, 10) |
| filings containing the same passage **twice internally** | **0** |

The three cross-issuer groups are all trivial stubs — `Item 6. [Reserved]`
(7 tokens), `Item 9 … None.` (21 tokens), `Item 9 … Not applicable.` (22
tokens) — every one below the 12-word span guidance already in force. The
group of ten, which looked alarming as a bare number, is the second of those.

So the live case is narrow and specific: **a company's FY2024 and FY2025
boilerplate being identical, 428 chunks.**

**Why it matters.** Gold is scoped to the accession, so retrieving the other
fiscal year is scored a miss — and **no text-only retriever can distinguish
them**, because the text is the same. A query whose gold span sits in such a
pair is therefore a coin flip, for every arm equally.

**The rule.** A gold span that also appears in another filing draws a
**non-blocking advisory** naming the other accessions. The query set is not
refused. The **number of flagged queries in the final 50 is reported alongside
the results**, so the effect is disclosed rather than absorbed.

**Why advisory and not refusal — the reasoning that decided it.** Refusing such
spans would remove precisely the *stable* content: business description,
properties, risk factors — the text that legitimately repeats year to year. It
would steer the query set toward year-specific content, which is mostly
financial figures. That is a **selection effect on what gets measured**, it
would make the corpus look easier, and no reader could detect it from the
published numbers. A visible advisory plus a reported count costs nothing and
leaves the judgement with whoever writes the query, who can pin the fiscal year
in the query text instead.

*Direction of effect, stated rather than buried:* none, by construction — this
amendment changes no scoring rule and refuses nothing. Its only effect is on
which queries a human chooses to write, and the count of affected queries is
published so a reader can weigh it.

**The zero is load-bearing.** No filing contains the same passage twice, which
is why AMENDMENT 4's accession-scoped cap of 5 means what it says. All the
figures above are pinned as tests in `tests/test_indexes.py`, so a corpus change
that moves any of them fails loudly rather than quietly invalidating this
reasoning.

**DISCLOSED — RETRIEVAL OUTPUT WAS SEEN BEFORE THE QUERY SET WAS WRITTEN,
2026-08-19.** Recorded before the first query was written, and published rather
than kept internal, because it is an ordering fact and an ordering fact a reader
cannot see is indistinguishable from one that was hidden.

The rule in force is that the query set is written **blind to retrieval
output**: reading filings to write queries is fine, reading what the retriever
returns is not. While verifying that the two indexes were actually being used by
the query planner — not merely that they existed — smoke queries were run
against the live indexes. That is retrieval output, and it was seen.

**Exactly what was seen, stated so the exposure is bounded rather than
characterised:**

- **Sparse arm, one query** (`goodwill | impairment | charge`): the top five
  chunk ids with their tickers, item labels and `ts_rank_cd` scores — two MA
  `Item 8` chunks, one DOW `Item 8`, two WYNN `Item 7`. **The text of those
  chunks was not displayed.**
- **Dense arm:** self-retrieval only — a stored vector queried against the index
  returning its own chunk at distance 0. No topical query was run.
- **Incidentally, while diagnosing byte-identical chunks:** one MPC `Item 1A`
  passage and one GWW `Item 1A` passage, plus the three duplicate stubs quoted
  in AMENDMENT 5.

**Why it is a risk at all.** A query about goodwill impairment whose gold span
came from an MA, DOW or WYNN `Item 7`/`Item 8` passage would be a query written
in the knowledge that the sparse arm already ranks it first. That inflates
sparse recall, and nothing in the published numbers would reveal it.

**The constraint, fixed now and checkable afterwards:** **no query in the set may
take its gold from a goodwill-impairment passage in MA, DOW or WYNN.** The
exposure is five chunks and one query term; this constraint covers all of it
with room to spare, and it can be verified against the finished query set by
anyone.

**What is not affected.** The dense arm saw no topical query, so no query
anywhere in the set is informed by dense output. The 11,616 chunks outside those
five are untouched, as is every other issuer and every other topic.

*Direction of effect if the constraint were violated:* it would raise sparse
recall specifically, which is the arm the hybrid arm is measured against — so
the error would flatter the baseline and understate hybrid's margin. Stated for
completeness; the constraint is what makes it moot.

**5. Reporting.**

- **recall@1 and recall@5**, per arm, per stratum, each with a Wilson interval,
  on the denominators fixed in §5.
- **Stated precisely, because the name is loose:** every answerable query has at
  least one gold chunk, so the quantity reported is a **hit rate at k** — the
  share of queries with at least one gold chunk in the top k. It is called
  recall@k because that is the term the literature uses for this quantity, and
  the definition above is what is actually computed.
- **The arm comparison is paired** — the same queries pass through all three
  arms. Three overlapping Wilson intervals invite a reader to conclude "no
  difference" when the paired data may say otherwise, so any comparison between
  two arms is additionally reported as **the discordant pairs (b, c) with a
  Wilson interval on b/(b+c)** — the queries one arm gets and the other misses.
  That is the McNemar sign test, and it reuses `evaluation/wilson.py` rather
  than introducing a second statistical implementation that would need its own
  hand-verified tests.
- **Abstention is not scored here.** The 15 unanswerable queries carry no gold
  and recall is undefined for them. Abstention is a property of the QA layer and
  is measured against it in Phases 4 and 5, never against a ranked list.

**Cost, stated rather than buried.** One embedding model, one `tsvector`
configuration, one RRF constant. This project therefore reports retrieval
quality **at one configuration** and cannot say whether another embedder, a
`simple` dictionary, or a different `k` would do better — the same limitation,
for the same reason, as the one chunk size recorded above. If a second
configuration is ever run it is reported as an additional arm with this section
amended, never as a replacement for the first set of numbers.

