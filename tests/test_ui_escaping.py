"""Nothing user-supplied reaches the page as markup.

Every entry field is written into HTML with unsafe_allow_html. A note, a
person's name or a category is free text that arrives from a form, a
spreadsheet cell, or a model reading an uploaded document — none of which is
trustworthy enough to render raw.
"""

from __future__ import annotations

import pytest

from ledger.ui import esc, safe_href


@pytest.mark.parametrize("payload", [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><script>alert(1)</script>',
    "<svg/onload=alert(1)>",
])
def test_markup_is_neutralised(payload):
    out = esc(payload)
    assert "<" not in out and ">" not in out
    assert "&lt;" in out


def test_quotes_are_escaped_so_attributes_cannot_be_broken_out_of():
    """The attachment value lands inside href="…"; an unescaped quote would
    let an onmouseover handler be appended."""
    out = esc('" onmouseover="alert(1)')
    assert '"' not in out
    assert "&quot;" in out


def test_ampersands_survive_readably():
    assert esc("Ravi & Amma") == "Ravi &amp; Amma"


@pytest.mark.parametrize("value", [None, "", 0])
def test_empty_values_are_safe(value):
    assert esc(value) in ("", "0")


def test_ordinary_text_is_left_readable():
    assert esc("UPI to Amma, 5000") == "UPI to Amma, 5000"


# ------------------------------------------------------------------ href rules

@pytest.mark.parametrize("url", [
    "https://drive.google.com/file/d/abc/view",
    "http://example.com/statement.pdf",
    "  https://example.com/x.pdf  ",
])
def test_http_links_are_kept(url):
    assert safe_href(url) == url.strip()


@pytest.mark.parametrize("url", [
    "javascript:alert(document.cookie)",
    "JavaScript:alert(1)",
    "  javascript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "sheet:abc123",
])
def test_everything_else_is_dropped(url):
    """Dropped, not escaped — a statement link has no reason to be anything
    but http(s), so there is nothing to preserve."""
    assert safe_href(url) == ""


@pytest.mark.parametrize("url", [None, "", "   "])
def test_absent_links_are_empty(url):
    assert safe_href(url) == ""


def test_a_scheme_check_is_not_fooled_by_a_leading_newline():
    assert safe_href("\n javascript:alert(1)") == ""
