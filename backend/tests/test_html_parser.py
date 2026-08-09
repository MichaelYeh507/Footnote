"""Tests for HTML/iXBRL text extraction.

EDGAR does not serve 10-Ks as PDF. The primary document is HTML, and since
roughly 2019 it is inline XBRL: machine-readable facts wrapped around the same
words a human reads. The parser has to keep the human text and drop everything
else, because whatever it returns is what the extraction model sees.

The hidden-header case is the load-bearing one. iXBRL filings carry a
display:none block of contexts and unit definitions. Left in, it contributes
thousands of tokens of noise with no prose in it.
"""

import pytest

from services.html_parser import extract_text_from_html


def html(body: str) -> bytes:
    return f"<html><body>{body}</body></html>".encode()


def test_extracts_visible_text():
    text = extract_text_from_html(html("<p>Total assets were $391,035 million.</p>"))
    assert "Total assets were $391,035 million." in text


def test_drops_script_and_style():
    text = extract_text_from_html(
        html("<style>.x{color:red}</style><script>var a=1;</script><p>Item 1. Business</p>")
    )
    assert "Item 1. Business" in text
    assert "color:red" not in text
    assert "var a=1" not in text


def test_drops_hidden_ixbrl_header():
    """The display:none block is context/unit definitions, not filing prose."""
    text = extract_text_from_html(
        html(
            '<div style="display:none">'
            "<ix:header><ix:references>us-gaap-2024</ix:references></ix:header>"
            "</div>"
            "<p>Item 1A. Risk Factors</p>"
        )
    )
    assert "Item 1A. Risk Factors" in text
    assert "us-gaap-2024" not in text


def test_drops_hidden_block_regardless_of_spacing():
    """Filers write the style attribute inconsistently; matching must not be brittle."""
    for style in ("display:none", "display: none", "DISPLAY:NONE", "display:none;"):
        text = extract_text_from_html(
            html(f'<div style="{style}">SECRET_CONTEXT</div><p>Visible</p>')
        )
        assert "Visible" in text, style
        assert "SECRET_CONTEXT" not in text, style


def test_keeps_text_inside_inline_xbrl_tags():
    """ix:nonFraction wraps the number a human reads. Dropping the tag must not
    drop its contents."""
    text = extract_text_from_html(
        html('<p>Revenue <ix:nonFraction name="us-gaap:Revenues">391,035</ix:nonFraction></p>')
    )
    assert "391,035" in text


def test_unescapes_entities():
    text = extract_text_from_html(html("<p>Research&nbsp;&amp;&nbsp;Development</p>"))
    assert "Research & Development" in text
    assert "&amp;" not in text
    assert "&nbsp;" not in text


def test_table_cells_do_not_run_together():
    """Balance sheet rows are tables. Adjacent cells must stay separable, or
    'Total assets' and its value merge into one unparseable token."""
    text = extract_text_from_html(
        html("<table><tr><td>Total assets</td><td>391,035</td></tr></table>")
    )
    assert "Total assets" in text
    assert "391,035" in text
    assert "Total assets391,035" not in text


def test_collapses_runaway_whitespace():
    """Filings are full of layout whitespace. It is pure token cost."""
    text = extract_text_from_html(html("<p>A</p>" + "<p>  </p>" * 50 + "<p>B</p>"))
    assert "\n\n\n" not in text


def test_markup_only_document_yields_no_text():
    """Must be falsy so the pipeline can raise rather than send an empty prompt."""
    text = extract_text_from_html(html('<div style="display:none">hidden</div>'))
    assert not text.strip()


def test_accepts_bytes_not_str():
    """Callers pass raw upload/fetch bytes; decoding is the parser's job."""
    assert "café" in extract_text_from_html("<p>café</p>".encode("utf-8"))


def test_survives_malformed_markup():
    """Filer HTML is frequently invalid. The parser must not raise."""
    text = extract_text_from_html(b"<html><body><p>Unclosed<div>Nested</body>")
    assert "Unclosed" in text
    assert "Nested" in text


@pytest.mark.parametrize("payload", [b"", b"   "])
def test_empty_input_returns_empty(payload):
    assert extract_text_from_html(payload).strip() == ""
