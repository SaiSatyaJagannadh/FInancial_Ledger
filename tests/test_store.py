from datetime import date

import pytest

from ledger import store
from ledger.compute import totals
from ledger.demo import build_demo_entries
from ledger.models import Direction, Entry
from ledger.money import to_paise

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def test_is_configured_needs_both_halves():
    assert store.is_configured(CONFIGURED)
    assert not store.is_configured({})
    assert not store.is_configured({"gcp_service_account": {"client_email": "x"}})
    assert not store.is_configured({"sheet": {"url": "u"}})
    assert not store.is_configured({"gcp_service_account": {}, "sheet": {"url": "u"}})


def test_sheet_id_is_accepted_instead_of_url():
    assert store.is_configured(
        {"gcp_service_account": {"client_email": "x"}, "sheet": {"id": "abc"}}
    )


def test_load_without_credentials_is_demo_mode():
    result = store.load(secrets={})
    assert result.demo is True
    assert result.problems == []
    assert totals(result.entries).records == 32


def test_load_falls_back_to_demo_when_the_sheet_is_unreachable(monkeypatch):
    """An expired key must not render as 'nobody owes you anything'."""

    def boom(_secrets):
        raise ConnectionError("token expired")

    monkeypatch.setattr(store, "_open_worksheet", boom)
    result = store.load(secrets=CONFIGURED)
    assert result.demo is True
    assert "Could not reach the sheet" in result.detail
    assert result.entries  # still shows something rather than an empty page


def test_load_reads_rows_from_the_sheet(monkeypatch):
    class FakeSheet:
        def get_all_records(self):
            return [
                {"date": "2026-01-24", "person": "Father", "ledger": "House repair",
                 "direction": "given", "amount": "1,000", "note": "UPI"},
                {"date": "2026-02-01", "person": "Father", "ledger": "House repair",
                 "direction": "received", "amount": "400", "note": ""},
            ]

    monkeypatch.setattr(store, "_open_worksheet", lambda _s: FakeSheet())
    result = store.load(secrets=CONFIGURED)
    assert result.demo is False
    assert len(result.entries) == 2
    assert totals(result.entries).net_paise == to_paise(600)


def test_one_bad_row_does_not_hide_the_good_ones():
    rows = [
        {"date": "2026-01-24", "person": "Father", "ledger": "L", "direction": "given", "amount": "100"},
        {"date": "not a date", "person": "Father", "ledger": "L", "direction": "given", "amount": "100"},
        {"date": "2026-02-24", "person": "Father", "ledger": "L", "direction": "sideways", "amount": "100"},
        {"date": "2026-03-24", "person": "Father", "ledger": "L", "direction": "received", "amount": "50"},
    ]
    entries, problems = store.rows_to_entries(rows)
    assert len(entries) == 2
    assert len(problems) == 2
    assert "row 3" in problems[0] and "row 4" in problems[1]


def test_blank_spacer_rows_are_skipped_silently():
    rows = [
        {"date": "", "person": "", "ledger": "", "direction": "", "amount": ""},
        {"date": "2026-01-24", "person": "F", "ledger": "L", "direction": "given", "amount": "100"},
    ]
    entries, problems = store.rows_to_entries(rows)
    assert len(entries) == 1
    assert problems == []


def test_headers_are_matched_case_and_space_insensitively():
    entries, problems = store.rows_to_entries(
        [{" Date ": "2026-01-24", "Person": "F", "LEDGER": "L",
          "Direction": "Given", "Amount": "100", "Note": "x"}]
    )
    assert problems == []
    assert entries[0].person == "F"


def test_entries_come_back_in_date_order():
    rows = [
        {"date": "2026-03-01", "person": "A", "ledger": "L", "direction": "given", "amount": "1"},
        {"date": "2026-01-01", "person": "A", "ledger": "L", "direction": "given", "amount": "1"},
    ]
    entries, _ = store.rows_to_entries(rows)
    assert [e.date for e in entries] == [date(2026, 1, 1), date(2026, 3, 1)]


def test_append_refuses_in_demo_mode():
    """Silently discarding a save would be worse than refusing it."""
    entry = Entry(
        date=date(2026, 1, 1), person="A", ledger="L",
        direction=Direction.given, amount_paise=100,
    )
    with pytest.raises(RuntimeError, match="Demo mode"):
        store.append(entry, secrets={})


def test_append_writes_the_row_in_column_order(monkeypatch):
    written = {}

    class FakeSheet:
        def row_values(self, _n):
            return ["date", "person", "ledger", "direction", "amount", "note"]

        def append_row(self, row, **kwargs):
            written["row"] = row

    monkeypatch.setattr(store, "_open_worksheet", lambda _s: FakeSheet())
    entry = Entry(
        date=date(2026, 1, 24), person="Father", ledger="House repair",
        direction=Direction.given, amount_paise=120_050, note="UPI",
    )
    store.append(entry, secrets=CONFIGURED)
    assert written["row"] == ["2026-01-24", "Father", "House repair", "given", "1200.50", "UPI"]


def test_append_writes_a_header_to_an_empty_sheet(monkeypatch):
    calls = {}

    class FakeSheet:
        def row_values(self, _n):
            return []

        def update(self, cell, values):
            calls["header"] = (cell, values)

        def append_row(self, row, **kwargs):
            calls["row"] = row

    monkeypatch.setattr(store, "_open_worksheet", lambda _s: FakeSheet())
    store.append(
        Entry(date=date(2026, 1, 1), person="A", ledger="L",
              direction=Direction.given, amount_paise=100),
        secrets=CONFIGURED,
    )
    assert calls["header"] == ("A1", [["date", "person", "ledger", "direction", "amount", "note"]])
