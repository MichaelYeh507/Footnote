"""The fetch-time financial-statement detector.

Written after the PGR corpus defect (plan §5, CORPUS DEFECT, 2026-08-18):
both PGR primary documents carry only Schedule II parent-company condensed
statements -- the consolidated statements are incorporated by reference into
Item 8 from the Annual Report exhibit -- yet the manifest recorded
`has_balance_sheet: true` for both. The caption regexes were satisfied by the
incorporation-by-reference bullet list, the exact text that also fooled the
labeler.

Words cannot decide this. PG's Item 15 says its statements are "incorporated
by reference in Part II, Item 8" while physically containing them in Item 8;
PGR prints nearly the same sentence and does not contain them. And the
committed `item_8_by_reference` flag was wrong in both directions: true for
eight healthy filings (Item 3 cross-references INTO Item 8) and false for the
two defective ones (whose phrasing reverses the regex's required order).

The repaired detector asks the filer instead of the prose. These are inline
XBRL documents, so a statement that is physically present has its displayed
figures wrapped in tags: an undimensioned `us-gaap:Assets` fact is the
consolidated balance-sheet total, and an undimensioned revenue fact is the
income-statement top line. PGR's only statement facts carry
`ConsolidatedEntitiesAxis=ParentCompanyMember`. Verified over all 44 local
documents on 2026-08-18: every healthy filing tags at least two undimensioned
Assets facts and three undimensioned revenue facts; both PGR documents tag
zero of each.
"""

import json
import pathlib
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import corpus_paths  # noqa: E402
from fetch_filings import statement_flags  # noqa: E402

# ------------------------------------------------------- synthetic documents

FY_CONTEXT = (
    '<xbrli:context id="fy"><xbrli:period>'
    "<xbrli:startDate>2024-01-01</xbrli:startDate>"
    "<xbrli:endDate>2024-12-31</xbrli:endDate>"
    "</xbrli:period></xbrli:context>"
)
INSTANT_CONTEXT = (
    '<xbrli:context id="ye"><xbrli:period>'
    "<xbrli:instant>2024-12-31</xbrli:instant>"
    "</xbrli:period></xbrli:context>"
)
# PGR-style: the same periods, but every context carries the parent-company
# axis. Schedule II figures are tagged this way.
PARENT_FY_CONTEXT = (
    '<xbrli:context id="fy"><xbrli:entity><xbrli:segment>'
    '<xbrldi:explicitMember dimension="srt:ConsolidatedEntitiesAxis">'
    "srt:ParentCompanyMember</xbrldi:explicitMember>"
    "</xbrli:segment></xbrli:entity><xbrli:period>"
    "<xbrli:startDate>2024-01-01</xbrli:startDate>"
    "<xbrli:endDate>2024-12-31</xbrli:endDate>"
    "</xbrli:period></xbrli:context>"
)
PARENT_INSTANT_CONTEXT = (
    '<xbrli:context id="ye"><xbrli:entity><xbrli:segment>'
    '<xbrldi:explicitMember dimension="srt:ConsolidatedEntitiesAxis">'
    "srt:ParentCompanyMember</xbrldi:explicitMember>"
    "</xbrli:segment></xbrli:entity><xbrli:period>"
    "<xbrli:instant>2024-12-31</xbrli:instant>"
    "</xbrli:period></xbrli:context>"
)

ASSETS_FACT = ('<ix:nonFraction name="us-gaap:Assets" contextRef="ye" '
               'scale="6" unitRef="usd">35,566</ix:nonFraction>')
REVENUE_FACT = ('<ix:nonFraction name="us-gaap:Revenues" contextRef="fy" '
                'scale="6" unitRef="usd">11,539</ix:nonFraction>')

# The prose that fooled the old detector: statement titles inside the
# incorporation-by-reference bullet list, and Schedule II's caption.
REFERENCE_LIST_PROSE = (
    "The consolidated financial statements are included in our Annual Report "
    "and are incorporated by reference in Item 8: "
    "Consolidated Statements of Comprehensive Income - For the Years Ended "
    "December 31, 2024, 2023, and 2022 "
    "Consolidated Balance Sheets - December 31, 2024 and 2023 "
    "SCHEDULE II - CONDENSED FINANCIAL INFORMATION OF REGISTRANT "
    "CONDENSED BALANCE SHEETS (PARENT COMPANY) Total assets $ 35,566"
)


def document(*parts: str) -> str:
    return "<html><body>" + "".join(parts) + "</body></html>"


# ------------------------------------------------------------ the contract

def test_undimensioned_statement_facts_set_both_flags():
    raw = document(FY_CONTEXT, INSTANT_CONTEXT, ASSETS_FACT, REVENUE_FACT)
    flags = statement_flags(raw)
    assert flags["has_balance_sheet"] is True
    assert flags["has_income_statement"] is True


def test_parent_only_facts_set_neither_flag_even_with_the_captions():
    """The PGR defect in one document: parent-axis facts plus the exact prose
    that satisfied the old caption regexes."""
    raw = document(PARENT_FY_CONTEXT, PARENT_INSTANT_CONTEXT,
                   REFERENCE_LIST_PROSE, ASSETS_FACT, REVENUE_FACT)
    flags = statement_flags(raw)
    assert flags["has_balance_sheet"] is False
    assert flags["has_income_statement"] is False


def test_caption_words_alone_prove_nothing():
    """Statement titles with no tagged figures at all: a reference list, a
    table of contents, an auditor's report."""
    flags = statement_flags(document(REFERENCE_LIST_PROSE))
    assert flags["has_balance_sheet"] is False
    assert flags["has_income_statement"] is False


def test_a_dimensioned_fact_does_not_stand_in_for_the_consolidated_one():
    """Only the balance sheet is parent-tagged here; the income statement is
    genuinely present. The flags must move independently."""
    raw = document(PARENT_INSTANT_CONTEXT, FY_CONTEXT,
                   ASSETS_FACT, REVENUE_FACT)
    flags = statement_flags(raw)
    assert flags["has_balance_sheet"] is False
    assert flags["has_income_statement"] is True


def test_the_flag_dict_carries_no_retired_key():
    """`item_8_by_reference` was wrong in both directions on the committed
    manifest and text cannot repair it (PG vs PGR print the same sentence).
    Retired, not fixed -- a key that reappears here was re-added without
    reading the plan entry."""
    flags = statement_flags(document(FY_CONTEXT, ASSETS_FACT))
    assert set(flags) == {"has_balance_sheet", "has_income_statement"}


# ------------------------------------------------- the real corpus, locally

CORPUS = BACKEND / "corpus"
ROWS = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))["filings"]


def _filings_present() -> bool:
    directory = corpus_paths.filings_dir()
    return directory.is_dir() and any(directory.glob("*.htm"))


@pytest.mark.corpus
@pytest.mark.skipif(not _filings_present(),
                    reason="local filings not present (RAG_FILINGS_DIR)")
@pytest.mark.parametrize("row", ROWS, ids=lambda r: f"{r['ticker']}-{r['period']}")
def test_every_local_document_is_classified_correctly(row):
    """PGR's two documents are the corpus's known statement-free filings;
    every other document physically contains its consolidated statements.
    A detector change that misclassifies any of the 44 fails here."""
    name = f"{row['ticker']}_{row['period']}.htm"
    raw = (corpus_paths.filings_dir() / name).read_bytes().decode("utf-8", "replace")
    flags = statement_flags(raw)
    expected = row["ticker"] != "PGR"
    assert flags["has_balance_sheet"] is expected, name
    assert flags["has_income_statement"] is expected, name
