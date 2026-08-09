"""Structured extraction from 10-K text via the OpenAI chat API.

PROMPT STATUS: draft, not frozen. Freeze before labeling begins and record the
date here. Editing the prompt after eval results exist is p-hacking; editing it
before any ground truth exists is fixing a bug. Only the second is legitimate.

The field notes below were calibrated against 8 filings from 8 sectors on
2026-08-09 (Apple, Costco, Chipotle, JPMorgan, Johnson & Johnson, Exxon Mobil,
Caterpillar, NextEra). That calibration set is deliberately DISJOINT from the
eval corpus: those documents were read closely while writing these instructions,
so including them would leak.

What the calibration changed, and why each was a defect in the instrument rather
than a measurement of difficulty:

* revenue_most_recent_fy had no guidance on which income-statement line to take.
  Eight filings used roughly seven different labels for it, and one plausible
  label folds non-operating income into the total.
* employees assumed prose phrasing. Filings also present it as a table row, and
  at least one states the count under a "(thousands)" header, where a literal
  read is wrong by a factor of 1000.
* dividends_declared_per_share used vocabulary that appeared in 1 of 8 filings.
* ceo_name anchored on "principal executive officer", absent from 1 of 8, while
  "principal executive offices" -- a street address -- appears in all 8.
* Unit declarations vary in wording and vary BETWEEN TABLES inside one filing,
  so a single per-document unit rule was never going to hold.
"""

import json
import os

import openai
from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"

client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Absence handling is load-bearing: dividends_declared_per_share and
# goodwill_impairment are absent from many filings by design, and the
# false-extraction rate measures whether the model invents values for them.
# Rule 3 is what that rate evaluates -- note it distinguishes three outcomes,
# not two, because filings routinely state in prose that an amount was zero
# while carrying no corresponding line item.
SYSTEM_PROMPT = """You are extracting structured data from SEC filings (Form 10-K).
Return ONLY valid JSON matching the exact schema below. No markdown, no explanation.

GENERAL RULES

1. Extract only what the document states. Never infer, estimate, or calculate a
   value that is not written in the filing.
2. Report figures for the MOST RECENT fiscal year. Financial statements show two
   or three comparative years side by side. The most recent is usually the first
   numeric column, but confirm against the column headers rather than assuming
   position.
3. null, 0, and a number are three different answers:
     null      the filing does not address this item
     0         the filing states the amount is zero, or that none occurred
     a number  the filing states the amount
   Never use 0 to mean "not found".
4. UNITS. Report every monetary amount in MILLIONS, as a plain number.
   Units are declared in table headers and captions, not beside each figure, and
   one filing often uses different units in different tables. Identify the
   declaration governing the table you are reading before converting.
     "$1,234,567 thousand"                          -> 1234.567
     "$4.2 billion"                                 -> 4200
     header "(in thousands)", cell "1,234,567"      -> 1234.567
     header "(millions of dollars)", cell "391,035" -> 391035
5. Counts are not money, but they carry units too. A figure of 57.9 under a
   header reading "(thousands)" is 57,900.
6. When the same figure appears in more than one place, prefer the consolidated
   financial statements over MD&A, segment notes, or narrative summaries.

FIELD NOTES

company_name
  The registrant named on the cover page. Some 10-Ks are filed jointly by more
  than one registrant, such as a parent and a subsidiary; return the first-listed
  registrant.

ticker
  The symbol in the "Trading Symbol(s)" column on the cover page. Filings may
  register several securities; return the one for the common stock, not for
  notes, bonds, or preferred series.

fiscal_year_end
  From the "For the fiscal year ended ___" line on the cover page. The adjacent
  "transition period from ___ to ___" line is a different field and is usually
  blank.

employees
  Total headcount as stated, keeping any qualifier the filing uses. Presentation
  varies: prose ("approximately N full-time equivalent employees"), a Human
  Capital table, or a breakdown by region where only the total row is the answer.
  Apply rule 5 -- a headcount table may be denominated in thousands.

total_assets
  The "Total assets" line of the consolidated balance sheet. The same words
  appear in segment notes and in intermediate subtotals; use the balance sheet.

revenue_most_recent_fy
  The top-line total of revenue from the company's own operations, from the
  consolidated statement of operations. The label varies by industry -- "Total
  net sales", "Total revenues", "Total sales and revenues", "Total net revenue",
  and "Total operating revenues" all express the same concept.
  Do not use a total that folds in non-operating items, such as one labelled
  "Total revenues and other income". Where only such a combined total is
  presented, use the operating revenue subtotal above it; if no such subtotal is
  stated, return null rather than subtracting.

ceo_name
  The person serving as chief executive officer. The reliable signal is the title
  "Chief Executive Officer" attached to a person's name; the signature page lists
  names with titles. Note that "principal executive offices" on the cover page is
  a street address, not a person. If titles are combined or there are co-CEOs,
  return the one designated principal executive officer. If a transition occurred
  during or after the fiscal year, return the one the filing presents as current.

dividends_declared_per_share
  The per-share amount declared on common stock for the most recent fiscal year.
  Wording varies -- "dividends declared per share", "cash dividends declared per
  common share", "dividends per common share" -- and it may appear in the income
  statement, the statement of equity, or the market-for-common-equity item.
  Return the per-share amount, never the total dollars paid or declared.
  If the filing states that no dividends were declared or paid, return 0.
  If the filing does not address dividends, return null.

goodwill_impairment
  The goodwill impairment charge recognized in the most recent fiscal year.
    the filing states a charge        -> that amount
    the filing states there was none  -> 0
                                         (e.g. "no impairment charges were
                                         recorded", "resulting in no impairment")
    the filing does not address it    -> null
  The goodwill carrying balance on the balance sheet is not an impairment.
  An impairment of any other asset is not a goodwill impairment.

{
  "company_name": "string",
  "ticker": "string or null",
  "fiscal_year_end": "string or null (e.g. 'December 31, 2024')",
  "employees": "string or null (as stated, e.g. 'approximately 12,000')",
  "total_assets": number or null,
  "revenue_most_recent_fy": number or null,
  "ceo_name": "string or null",
  "dividends_declared_per_share": number or null,
  "goodwill_impairment": number or null,
  "sector": "string or null",
  "headquarters": "string or null",
  "description": "string or null (2-3 sentence summary of the business)",
  "founded": "string or null (year of incorporation/founding)",
  "report_type": "10-K",
  "risks": [
    {
      "risk_name": "string",
      "description": "string or null",
      "mitigation": "string or null (only if the filing states one)"
    }
  ],
  "management": [
    {
      "name": "string",
      "title": "string or null",
      "tenure": "string or null",
      "background": "string or null"
    }
  ]
}"""


def structure_text(raw_text: str) -> dict:
    """Send extracted text to OpenAI and get structured JSON back."""
    response = client.chat.completions.create(
        model=MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract structured data from this document:\n\n{raw_text}"},
        ],
        temperature=0.1,
    )
    result = response.choices[0].message.content
    return json.loads(result)
