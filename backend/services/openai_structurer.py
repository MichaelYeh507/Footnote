import json
import os

import openai
from dotenv import load_dotenv

load_dotenv()
client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """You are a financial document parser for an investment advisory firm.
Given raw text from an investment research PDF, extract ALL data into the
exact JSON schema below. Return ONLY valid JSON, no markdown or explanation.

Convert all dollar amounts to numbers (millions). E.g. "$312.4" -> 312.4
Convert percentages to numbers. E.g. "71.0%" -> 71.0
If a field isn't present in the document, use null.

{
  "company_name": "string",
  "ticker": "string or null",
  "sector": "string",
  "headquarters": "string",
  "ceo": "string",
  "description": "string (2-3 sentence summary)",
  "founded": "string",
  "employees": "string",
  "market_cap": "string",
  "enterprise_value": "string",
  "report_type": "equity_research | due_diligence | credit_analysis",
  "rating": "BUY | HOLD | SELL | OVERWEIGHT | UNDERWEIGHT or null",
  "price_target": number or null,
  "current_price": number or null,
  "investment_thesis": "string (key bull/bear points summarized)",
  "financials": [
    {
      "fiscal_year": "FY2024A",
      "is_estimate": false,
      "revenue": number, "gross_profit": number, "gross_margin": number,
      "ebitda": number, "ebitda_margin": number, "net_income": number,
      "eps": number, "free_cash_flow": number, "fcf_margin": number
    }
  ],
  "risks": [
    {
      "risk_name": "string",
      "description": "string",
      "likelihood": "Low | Medium | High",
      "impact": "Low | Medium | High | Very High",
      "mitigation": "string"
    }
  ],
  "management": [
    { "name": "string", "title": "string", "tenure": "string", "background": "string" }
  ],
  "valuations": [
    { "metric_name": "string", "company_value": "string", "peer_avg": "string" }
  ]
}"""


def structure_text(raw_text: str) -> dict:
    """Send extracted text to OpenAI and get structured JSON back."""
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Extract structured data from this document:\n\n{raw_text}"},
        ],
        temperature=0.1,
    )
    result = response.choices[0].message.content
    return json.loads(result)
