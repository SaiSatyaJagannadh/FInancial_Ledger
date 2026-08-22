"""The shareable summary, and the links that carry it."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, unquote, urlparse

from ledger.export import email_link, summary_text, whatsapp_link
from ledger.models import Direction, Entry
from ledger.money import Currency

ROWS = [
    Entry(date=date(2026, 1, 1), person="Ravi", ledger="UK",
          direction=Direction.given, amount_minor=500_000),
    Entry(date=date(2026, 2, 1), person="Ravi", ledger="UK",
          direction=Direction.received, amount_minor=200_000),
    Entry(date=date(2026, 3, 1), person="Sam", ledger="Books",
          direction=Direction.given, amount_minor=4_000, currency=Currency.USD),
]


def test_the_summary_names_who_owes_what():
    text = summary_text(ROWS, today=date(2026, 8, 22))
    assert "Ravi" in text and "owes me" in text
    assert "3,000.00" in text          # 5,000 given less 2,000 back


def test_both_currencies_appear_and_stay_apart():
    text = summary_text(ROWS, today=date(2026, 8, 22))
    assert "Indian Rupees" in text and "US Dollars" in text
    assert "INR" in text and "USD" in text


def test_a_settled_person_is_left_out():
    """A zero balance is not news; listing it makes the message longer for nothing."""
    rows = ROWS + [Entry(date=date(2026, 4, 1), person="Ravi", ledger="UK",
                         direction=Direction.received, amount_minor=300_000)]
    assert "Ravi" not in summary_text(rows, today=date(2026, 8, 22))


def test_an_empty_ledger_still_produces_a_message():
    assert "Nothing recorded" in summary_text([], today=date(2026, 8, 22))


def test_the_date_is_stated():
    assert "22 Aug 2026" in summary_text(ROWS, today=date(2026, 8, 22))


def test_whatsapp_link_carries_the_whole_message():
    text = summary_text(ROWS, today=date(2026, 8, 22))
    link = whatsapp_link(text)
    assert link.startswith("https://wa.me/?text=")
    assert unquote(link.split("text=", 1)[1]) == text


def test_email_link_has_a_subject_and_the_body():
    text = summary_text(ROWS, today=date(2026, 8, 22))
    link = email_link(text)
    assert link.startswith("mailto:?")
    fields = parse_qs(urlparse(link).query)
    assert fields["subject"] == ["Personal Ledger"]
    assert fields["body"][0] == text


def test_newlines_and_symbols_survive_encoding():
    """A summary is multi-line and has an em dash; both must not break the URL."""
    text = "line one\nline two — dash\n₹ rupee & ampersand"
    assert unquote(whatsapp_link(text).split("text=", 1)[1]) == text
    assert parse_qs(urlparse(email_link(text)).query)["body"][0] == text


def test_the_links_contain_no_raw_spaces():
    link = whatsapp_link(summary_text(ROWS, today=date(2026, 8, 22)))
    assert " " not in link
