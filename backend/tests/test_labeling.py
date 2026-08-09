"""Contracts for the labeling tool.

Two things are being protected. First, the label record shape and the protocol
rules pre-registered in HYBRID-RETRIEVAL-SEC-PLAN.md §5, because a label that
does not carry its evidence is not checkable by anyone later. Second, and more
important, the labeler's isolation from model output.

The isolation tests are not about display. A tool that merely refrains from
printing predictions is one careless edit away from leaking them, and the leak
would be undetectable in the resulting numbers. So the guarantee under test is
that no labeling code path OPENS the predictions file at all -- verified by
instrumenting open() for the duration of a simulated session, not by reading
the source and concluding it looks fine.
"""

import builtins
import json
import pathlib

import pytest

from evaluation.extraction_run import EVAL_FIELDS
from evaluation.labeling import (
    ANSWER_KINDS,
    SCHEMA_VERSION,
    build_queue,
    candidate_passages,
    completed_keys,
    label_record,
    validate_label,
)

CORPUS = pathlib.Path(__file__).resolve().parent.parent / "corpus"


@pytest.fixture(scope="module")
def manifest_fixture():
    return json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))


def locator(**kwargs):
    base = {"section": "Item 8, consolidated balance sheet", "anchor": "Total assets"}
    base.update(kwargs)
    return base


# --- queue -----------------------------------------------------------------

def test_queue_is_351_instances(manifest_fixture):
    assert len(build_queue(manifest_fixture)) == 39 * 9 == 351


def test_queue_covers_every_field_of_every_filing(manifest_fixture):
    queue = build_queue(manifest_fixture)
    by_filing = {}
    for item in queue:
        by_filing.setdefault(item["accession"], []).append(item["field"])
    assert len(by_filing) == 39
    assert all(fields == list(EVAL_FIELDS) for fields in by_filing.values())


def test_queue_keeps_both_years_of_an_issuer_together(manifest_fixture):
    tickers = [item["ticker"] for item in build_queue(manifest_fixture)]
    runs = [tickers[0]]
    for ticker in tickers[1:]:
        if ticker != runs[-1]:
            runs.append(ticker)
    assert len(runs) == len(set(runs)), "an issuer's instances are not contiguous"


def test_queue_excludes_over_window_filings(manifest_fixture):
    over = {f["accession"] for f in manifest_fixture["filings"]
            if not f["fits_context_window"]}
    assert not {i["accession"] for i in build_queue(manifest_fixture)} & over


# --- record shape ----------------------------------------------------------

def test_label_record_matches_the_pre_registered_shape():
    record = label_record(
        {"accession": "0000320193-25-000079", "ticker": "AAPL",
         "period": "2025-09-27", "field": "total_assets"},
        answer_kind="value", value=391035.0, locator=locator(),
        ambiguous=False, note="")
    assert set(record) == {
        "accession", "ticker", "period", "field", "status", "answer_kind",
        "value", "locator", "ambiguous", "note", "labeled_at", "schema_version"}
    assert record["status"] == "labeled"
    assert record["schema_version"] == SCHEMA_VERSION


def test_the_three_answer_kinds_are_exactly_as_pre_registered():
    assert set(ANSWER_KINDS) == {"value", "stated_none", "not_addressed"}


def test_stated_none_and_not_addressed_carry_no_value():
    """stated_none compares as 0 and not_addressed as null; the label must not
    also carry a number, or there are two sources of truth."""
    for kind in ("stated_none", "not_addressed"):
        record = label_record(
            {"accession": "A", "ticker": "T", "period": "P", "field": "goodwill_impairment"},
            answer_kind=kind, value=None,
            locator=locator(searched=["goodwill", "impairment"]))
        assert record["value"] is None


# --- protocol rules 1 and 2 ------------------------------------------------

@pytest.mark.parametrize("kind", ["value", "stated_none"])
def test_value_and_stated_none_require_an_anchor(kind):
    """Rule 1. A label that cannot be pointed at is not checkable."""
    with pytest.raises(ValueError, match="anchor"):
        validate_label(label_record(
            {"accession": "A", "ticker": "T", "period": "P", "field": "total_assets"},
            answer_kind=kind, value=1.0 if kind == "value" else None,
            locator={"section": "Item 8", "anchor": ""}))


def test_not_addressed_requires_the_search_terms_that_were_tried():
    """Rule 2. The only label asserting a negative, and the one the disclosed
    contamination could bias. It must be the result of looking."""
    with pytest.raises(ValueError, match="searched"):
        validate_label(label_record(
            {"accession": "A", "ticker": "T", "period": "P",
             "field": "dividends_declared_per_share"},
            answer_kind="not_addressed", value=None,
            locator={"section": "", "anchor": "", "searched": []}))


def test_not_addressed_is_accepted_with_search_terms():
    validate_label(label_record(
        {"accession": "A", "ticker": "T", "period": "P",
         "field": "dividends_declared_per_share"},
        answer_kind="not_addressed", value=None,
        locator={"section": "", "anchor": "", "searched": ["dividend", "per share"]}))


def test_value_kind_requires_a_value():
    with pytest.raises(ValueError, match="value"):
        validate_label(label_record(
            {"accession": "A", "ticker": "T", "period": "P", "field": "total_assets"},
            answer_kind="value", value=None, locator=locator()))


def test_unknown_answer_kind_is_rejected():
    with pytest.raises(ValueError, match="answer_kind"):
        validate_label(label_record(
            {"accession": "A", "ticker": "T", "period": "P", "field": "total_assets"},
            answer_kind="probably_none", value=None, locator=locator()))


# --- resume ----------------------------------------------------------------

def test_completed_keys_are_per_field_not_per_filing():
    """Labeling is interrupted mid-filing far more often than between filings."""
    lines = [
        json.dumps({"accession": "A", "field": "ticker"}),
        json.dumps({"accession": "A", "field": "total_assets"}),
    ]
    assert completed_keys(lines) == {("A", "ticker"), ("A", "total_assets")}


def test_malformed_label_line_refuses_to_resume():
    with pytest.raises(ValueError):
        completed_keys(["{ broken"])


# --- candidate passages ----------------------------------------------------

def test_candidate_passages_find_the_field_in_real_text():
    text = ("Item 7. MD&A ... blah blah. Item 8. Financial Statements. "
            "CONSOLIDATED BALANCE SHEETS (in millions) ... Total assets 391,035 ... "
            "Total liabilities 308,030")
    hits = candidate_passages(text, "total_assets", limit=5)
    assert hits, "no candidate found for a field that is plainly present"
    assert any("Total assets" in h["snippet"] for h in hits)


def test_candidate_passages_return_offsets_for_jumping():
    text = "x" * 500 + "Total assets 1,000"
    hits = candidate_passages(text, "total_assets", limit=1)
    assert hits[0]["offset"] >= 500


def test_candidate_passages_are_capped():
    text = "Total assets 1 " * 200
    assert len(candidate_passages(text, "total_assets", limit=3)) == 3


def test_absent_field_yields_no_candidates_rather_than_raising():
    """Which is itself evidence for not_addressed, and must not crash the tool."""
    assert candidate_passages("nothing relevant here", "goodwill_impairment") == []


# --- isolation from model output -------------------------------------------

FORBIDDEN_IMPORTS = ("openai", "evaluation.extraction_run", "services")


def test_labeling_module_imports_nothing_that_can_reach_model_output():
    """Checked against the import graph, not against a grep of the text.

    An earlier version searched the source for the word "predictions" and
    failed on the docstring that explains the isolation. Prose is not a code
    path; imports are. This also catches the case a grep would miss -- an
    innocuous-looking module that itself imports the extraction run.
    """
    import ast

    import evaluation.labeling as labeling

    tree = ast.parse(pathlib.Path(labeling.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in FORBIDDEN_IMPORTS:
        offenders = [name for name in imported
                     if name == forbidden or name.startswith(forbidden + ".")]
        assert not offenders, f"labeling imports {offenders}"


def test_no_string_literal_in_labeling_points_at_the_predictions_file():
    """Docstrings may discuss it; no code may name the path."""
    import ast

    import evaluation.labeling as labeling

    tree = ast.parse(pathlib.Path(labeling.__file__).read_text(encoding="utf-8"))

    # Identify docstrings by node identity. Comparing values does not work:
    # ast.get_docstring() returns the cleaned string while Constant.value is raw.
    docstring_nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstring_nodes.add(id(first.value))

    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstring_nodes]
    offenders = [s for s in literals if "prediction" in s.lower()]
    assert not offenders, f"code names the predictions path: {offenders}"


LABELING_CLI = (pathlib.Path(__file__).resolve().parent.parent
                / "scripts" / "label_filings.py")

# The CLI legitimately needs services.html_parser, so it cannot inherit the
# module-level ban. What it must never reach is the extractor or its output.
CLI_FORBIDDEN = ("openai", "evaluation.extraction_run", "services.openai_structurer")


def test_labeling_cli_imports_nothing_that_can_reach_model_output():
    """The pure module being clean is not enough -- the CLI is what runs."""
    import ast

    tree = ast.parse(LABELING_CLI.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    for forbidden in CLI_FORBIDDEN:
        offenders = [name for name in imported
                     if name == forbidden or name.startswith(forbidden + ".")]
        assert not offenders, f"labeling CLI imports {offenders}"


def test_labeling_cli_names_no_predictions_path():
    import ast

    tree = ast.parse(LABELING_CLI.read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstring_nodes.add(id(first.value))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstring_nodes]
    assert not [s for s in literals if "prediction" in s.lower()]


def test_no_labeling_code_path_opens_the_predictions_file(tmp_path, monkeypatch,
                                                          manifest_fixture):
    """The load-bearing test.

    Instruments open() for a full simulated labeling session -- queue, resume,
    candidate search, record, validate, persist -- and fails if anything named
    predictions is opened. Source inspection cannot prove this; an indirect
    import three levels down would pass a grep and still read the file.
    """
    opened = []
    real_open = builtins.open

    def watched_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watched_open)
    monkeypatch.setattr(pathlib.Path, "read_text",
                        lambda self, *a, **k: opened.append(str(self)) or "")

    labels = tmp_path / "labels.jsonl"
    queue = build_queue(manifest_fixture)
    completed_keys([])
    candidate_passages("Total assets 1,000", "total_assets")
    record = label_record(queue[0], answer_kind="value", value=1.0,
                          locator=locator())
    validate_label(record)
    with watched_open(labels, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")

    leaked = [p for p in opened if "prediction" in p.lower()]
    assert not leaked, f"labeling session opened model output: {leaked}"
