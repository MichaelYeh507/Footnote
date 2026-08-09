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
# "Return null rather than guessing" is the instruction that rate evaluates.
SYSTEM_PROMPT = """You are extracting structured data from SEC filings (Form 10-K).
Return ONLY valid JSON matching the exact schema below. No markdown, no explanation.

RULES
1. Extract only what the document states. Never infer, estimate, or calculate a
   value that is not written in the filing.
2. If a field is not present in the document, return null. Do not guess.
3. null and 0 are different. Return 0 only when the filing states zero. Return
   null when the line item does not appear at all.
4. Report all monetary amounts in MILLIONS as plain numbers.
   "$1,234,567 thousand" -> 1234.567   "$4.2 billion" -> 4200
5. revenue_most_recent_fy is the MOST RECENT fiscal year only. Income statements
   show several comparative years side by side; do not take an earlier column.
6. ceo_name is the current principal executive officer. Signature pages list many
   officers; if titles are combined or there are co-CEOs, return the one
   designated principal executive officer.

{
  "company_name": "string (registrant name, cover page)",
  "ticker": "string or null (trading symbol, cover page)",
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
