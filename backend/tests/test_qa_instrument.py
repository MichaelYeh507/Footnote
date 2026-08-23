"""The QA instrument: the prompt is the published one, and calls behave.

PRE-REGISTERED 2026-08-21 (`EVALUATION-SPEC.md`, appendix *PHASE 4/5*). The
failures this file is written against:

  **The running prompt drifting from the registered one.** The appendix
  publishes the system prompt verbatim. The test below extracts that block
  from the spec and asserts byte equality, so "the instrument that ran is
  the instrument that was registered" is a checked property, not a claim.

  **The fingerprint pin agreeing with itself.** `INSTRUMENT_SHA256` is a
  literal and `instrument_fingerprint()` recomputes it from the ingredients;
  asserting them equal means editing the model, the temperature or a word of
  the prompt fails here rather than silently re-fingerprinting.

  **A retry that re-rolls an answer.** Retries are transport-only and
  bounded; a delivered response is never re-requested. The mock client below
  distinguishes the two by construction.
"""

import pathlib
import re
import sys

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from services import qa  # noqa: E402

SPEC = BACKEND.parent / "EVALUATION-SPEC.md"


def _published_blocks():
    spec = SPEC.read_text(encoding="utf-8")
    marker = "### The prompt, fixed verbatim"
    section = spec[spec.index(marker):]
    return re.findall(r"```\n(.*?)```", section, re.DOTALL)


def test_system_prompt_is_the_published_block_byte_for_byte():
    blocks = _published_blocks()
    # The fenced block carries one trailing newline from the fence line.
    assert blocks[0] == qa.SYSTEM_PROMPT + "\n"


def test_user_template_matches_the_published_block():
    template = _published_blocks()[1]
    rendered = qa.render_user_message("QUERYTEXT", [{
        "ticker": "TICK", "period": "PERIODEND", "item": "ITEMNO",
        "text": "CHUNKTEXT"}])
    expected = (template
                .replace("{query}", "QUERYTEXT")
                .replace("{i}", "1")
                .replace("{ticker}", "TICK")
                .replace("{period}", "PERIODEND")
                .replace("{item}", "ITEMNO")
                .replace("{chunk text}", "CHUNKTEXT"))
    assert rendered == expected.rstrip("\n")


def test_instrument_pins():
    assert qa.MODEL == "gpt-4o-mini"
    assert qa.TEMPERATURE == 0.0
    assert qa.MAX_TRANSPORT_RETRIES == 3


def test_fingerprint_literal_matches_its_ingredients():
    assert qa.INSTRUMENT_SHA256 == qa.instrument_fingerprint()


def test_render_numbers_excerpts_from_one_in_order():
    excerpts = [
        {"ticker": "AAA", "period": "2024-12-31", "item": "1", "text": "one"},
        {"ticker": "BBB", "period": "2025-12-31", "item": "7", "text": "two"},
    ]
    rendered = qa.render_user_message("the question?", excerpts)
    assert rendered.startswith("Question: the question?\n\n")
    assert "[1] AAA 10-K, fiscal period ending 2024-12-31, Item 1:\none" \
        in rendered
    assert "[2] BBB 10-K, fiscal period ending 2025-12-31, Item 7:\ntwo" \
        in rendered
    assert rendered.index("[1]") < rendered.index("[2]")


def test_render_prints_an_empty_item_as_a_dash():
    rendered = qa.render_user_message("q?", [{
        "ticker": "AAA", "period": "2024-12-31", "item": "", "text": "t"}])
    assert "Item —:" in rendered


class _Usage:
    prompt_tokens = 123
    completion_tokens = 45


class _Response:
    def __init__(self, content):
        message = type("M", (), {"content": content})
        self.choices = [type("C", (), {"message": message})]
        self.usage = _Usage()


class _Client:
    """Fails with `errors` in order, then returns `content` forever."""

    def __init__(self, content='{"answer": null}', errors=()):
        self._errors = list(errors)
        self._content = content
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if outer._errors:
                    raise outer._errors.pop(0)
                return _Response(outer._content)

        self.chat = type("Chat", (), {"completions": _Completions()})


def _transport_error():
    import httpx
    return qa.openai.APIConnectionError(
        request=httpx.Request("POST", "https://api.test/none"))


def test_ask_returns_raw_attempts_and_usage():
    client = _Client(content='{"answer": "x"}')
    result = qa.ask("q?", [], client=client, sleep=lambda _: None)
    assert result == {"raw": '{"answer": "x"}', "attempts": 1,
                      "usage": {"prompt_tokens": 123,
                                "completion_tokens": 45}}
    sent = client.calls[0]
    assert sent["model"] == qa.MODEL
    assert sent["temperature"] == qa.TEMPERATURE
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["messages"][0] == {"role": "system",
                                   "content": qa.SYSTEM_PROMPT}


def test_ask_retries_transport_errors_and_counts_attempts():
    client = _Client(errors=[_transport_error(), _transport_error()])
    naps = []
    result = qa.ask("q?", [], client=client, sleep=naps.append)
    assert result["attempts"] == 3
    assert len(naps) == 2


def test_ask_gives_up_after_the_registered_retry_cap():
    errors = [_transport_error() for _ in range(qa.MAX_TRANSPORT_RETRIES + 1)]
    client = _Client(errors=errors)
    with pytest.raises(qa.openai.APIConnectionError):
        qa.ask("q?", [], client=client, sleep=lambda _: None)
    assert len(client.calls) == qa.MAX_TRANSPORT_RETRIES + 1


def test_ask_propagates_a_non_transport_error_immediately():
    client = _Client(errors=[ValueError("not transport")])
    with pytest.raises(ValueError):
        qa.ask("q?", [], client=client, sleep=lambda _: None)
    assert len(client.calls) == 1


def test_ask_records_a_null_content_as_empty_string():
    client = _Client(content=None)
    result = qa.ask("q?", [], client=client, sleep=lambda _: None)
    assert result["raw"] == ""
