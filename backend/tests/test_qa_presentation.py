"""The presentation layer over the frozen QA instrument.

`compose_paragraph` is a SECOND, unmeasured model call that restates an
already-computed answer as LLM-style prose. These tests pin the three rules
that keep it honest:

- it runs only on the fully verified path (answered + valid citation +
  verbatim-verified quote) and never touches the client otherwise;
- a mechanical guard rejects any paragraph carrying a digit run the input
  material did not, or missing the frozen answer verbatim -- rejection
  degrades to no paragraph, never to different words;
- it never raises: any failure (transport, malformed record, guard) yields
  None and the UI falls back to the terse verified display.

The measured instrument (`services/qa.py`) is untouched by construction --
nothing here imports it beyond the constants the demo already reuses.
"""

import pytest

from services import qa_demo


def _answered_record(**overrides):
    record = {
        "state": "answered",
        "question": ("How many stores did Domino's Pizza operate worldwide "
                     "at fiscal year end?"),
        "arm": "dense",
        "answer": "more than 22,100 locations",
        "citation": 1,
        "citation_valid": True,
        "quote": ("more than 22,100 locations in over 90 markets around the "
                  "world as of December 28, 2025"),
        "quote_verified": True,
        "excerpts": [{"n": 1, "chunk_id": "c1", "accession": "0000000000",
                      "ticker": "DPZ", "period": "2025-12-28", "item": "7",
                      "text": "…"}],
    }
    record.update(overrides)
    return record


GOOD_PARAGRAPH = (
    "Domino's Pizza operated more than 22,100 locations in over 90 markets "
    "as of December 28, 2025, per the company's 10-K for the fiscal period "
    "ending 2025-12-28 [1].")


class _Response:
    def __init__(self, content):
        message = type("M", (), {"content": content})
        self.choices = [type("C", (), {"message": message})]


class _Client:
    def __init__(self, content=GOOD_PARAGRAPH, error=None):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if error is not None:
                    raise error
                return _Response(content)

        self.chat = type("Chat", (), {"completions": _Completions()})


# --- the guard ---------------------------------------------------------


def test_guard_accepts_a_paragraph_built_only_from_the_material():
    assert qa_demo.presentation_passes_guard(
        GOOD_PARAGRAPH, _answered_record()) is True


def test_guard_rejects_a_digit_run_the_material_does_not_carry():
    novel_year = GOOD_PARAGRAPH.replace("December 28, 2025",
                                        "December 28, 2024")
    assert qa_demo.presentation_passes_guard(
        novel_year, _answered_record()) is False


def test_guard_rejects_a_novel_count_even_with_the_answer_verbatim():
    padded = ("Domino's Pizza operated more than 22,100 locations across "
              "45 countries [1].")
    assert qa_demo.presentation_passes_guard(
        padded, _answered_record()) is False


def test_guard_rejects_a_paragraph_missing_the_answer_verbatim():
    reworded = ("Domino's Pizza had a store base in over 90 markets as of "
                "December 28, 2025 [1].")
    assert qa_demo.presentation_passes_guard(
        reworded, _answered_record()) is False


def test_guard_is_comma_insensitive_on_digit_runs_only():
    # "22100" without the comma is the same digit run, but the answer text
    # itself must still appear verbatim (normalized), so this fails on
    # containment -- format drift degrades, it never rewrites.
    uncommaed = GOOD_PARAGRAPH.replace("22,100", "22100")
    assert qa_demo.presentation_passes_guard(
        uncommaed, _answered_record()) is False


def test_guard_rejects_empty_and_non_string_paragraphs():
    assert qa_demo.presentation_passes_guard("", _answered_record()) is False
    assert qa_demo.presentation_passes_guard(None,
                                             _answered_record()) is False


def test_guard_normalizes_case_and_curly_quotes_for_containment():
    fancy = GOOD_PARAGRAPH.replace("Domino's", "DOMINO’S")
    assert qa_demo.presentation_passes_guard(
        fancy, _answered_record()) is True


# --- compose_paragraph -------------------------------------------------


def test_compose_returns_the_paragraph_on_the_happy_path():
    client = _Client()
    assert qa_demo.compose_paragraph(_answered_record(),
                                     client=client) == GOOD_PARAGRAPH


def test_compose_pins_model_temperature_and_cap():
    client = _Client()
    qa_demo.compose_paragraph(_answered_record(), client=client)
    sent = client.calls[0]
    assert sent["model"] == qa_demo.PRESENTATION_MODEL
    assert sent["temperature"] == 0
    assert sent["max_tokens"] == qa_demo.PRESENTATION_MAX_TOKENS
    assert sent["messages"][0] == {"role": "system",
                                   "content": qa_demo.PRESENTATION_PROMPT}


def test_compose_sends_only_the_verified_material():
    client = _Client()
    record = _answered_record()
    qa_demo.compose_paragraph(record, client=client)
    material = client.calls[0]["messages"][1]["content"]
    assert record["question"] in material
    assert record["answer"] in material
    assert record["quote"] in material
    assert "DPZ" in material and "2025-12-28" in material
    # the full excerpt text is NOT sent -- the prose is built from the
    # verified pieces, not from unquoted context the answer never cited
    assert record["excerpts"][0]["text"] not in material.replace("…", "")


def test_compose_prompt_forbids_outside_material():
    prompt = qa_demo.PRESENTATION_PROMPT
    assert "ONLY the material" in prompt
    assert "verbatim" in prompt
    assert "Do not add" in prompt


def test_compose_skips_non_answered_states_without_a_call():
    client = _Client()
    for state in ("abstained", "malformed", "no_passages"):
        assert qa_demo.compose_paragraph(
            _answered_record(state=state), client=client) is None
    assert client.calls == []


def test_compose_skips_unverified_quotes_without_a_call():
    client = _Client()
    for verified in (False, None):
        assert qa_demo.compose_paragraph(
            _answered_record(quote_verified=verified), client=client) is None
    assert client.calls == []


def test_compose_skips_invalid_citations_without_a_call():
    client = _Client()
    assert qa_demo.compose_paragraph(
        _answered_record(citation_valid=False, citation=9),
        client=client) is None
    assert client.calls == []


def test_compose_returns_none_when_the_model_call_fails():
    client = _Client(error=RuntimeError("transport down"))
    assert qa_demo.compose_paragraph(_answered_record(),
                                     client=client) is None


def test_compose_returns_none_when_the_guard_rejects():
    client = _Client(content=GOOD_PARAGRAPH.replace("2025,", "2024,"))
    assert qa_demo.compose_paragraph(_answered_record(),
                                     client=client) is None


def test_compose_returns_none_on_empty_content():
    assert qa_demo.compose_paragraph(_answered_record(),
                                     client=_Client(content=None)) is None


def test_compose_never_raises_on_a_malformed_record():
    client = _Client()
    assert qa_demo.compose_paragraph({}, client=client) is None
    assert qa_demo.compose_paragraph(
        _answered_record(excerpts=[]), client=client) is None
    assert qa_demo.compose_paragraph(
        _answered_record(answer=None), client=client) is None
    assert client.calls == []


def test_answer_question_record_carries_presentation_none(monkeypatch):
    # The field exists on every record so the frontend type is total; the
    # ENDPOINT attaches prose, never the measured-path orchestration.
    from services import retrieval
    monkeypatch.setattr(retrieval, "or_tsquery", lambda cursor, text: "")
    monkeypatch.setattr(retrieval, "sparse_search",
                        lambda cursor, tsquery, depth=50: [])
    response = qa_demo.answer_question("nothing but stopwords", "sparse",
                                       cursor=object(), client=None)
    assert response["state"] == "no_passages"
    assert response["presentation"] is None
