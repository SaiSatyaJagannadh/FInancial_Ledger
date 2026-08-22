"""Editing a row. The dangerous part is editing the *wrong* row."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ledger import store
from ledger.models import BY_CHAT, BY_HAND, Direction, Entry
from ledger.money import Currency

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def make(row: int | None = 4, **kw) -> Entry:
    fields = dict(
        date=date(2026, 1, 24), person="Father", ledger="House repair",
        direction=Direction.given, amount_minor=120_050, currency=Currency.INR,
        note="UPI", row=row,
    )
    fields.update(kw)
    return Entry(**fields)


class FakeSheet:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []

    def row_values(self, n):
        return self.rows.get(n, [])

    def update(self, values=None, range_name=None, **kw):
        self.writes.append((range_name, values))


def wire(monkeypatch, rows) -> FakeSheet:
    fake = FakeSheet(rows)
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)
    return fake


def test_writes_the_edit_to_the_entrys_own_row(monkeypatch):
    entry = make()
    fake = wire(monkeypatch, {4: entry.to_row()})

    store.update(entry, replace(entry, amount_minor=999_00), secrets=CONFIGURED)

    range_name, values = fake.writes[0]
    assert range_name.startswith("A4:")
    assert values[0][4] == "999.00"


def test_the_edit_goes_to_the_original_row_not_the_edited_copys(monkeypatch):
    """An edited copy can carry a stale or absent row number. The write must
    follow where the entry actually lives."""
    entry = make(row=4)
    fake = wire(monkeypatch, {4: entry.to_row()})

    store.update(entry, replace(entry, person="Renamed", row=99), secrets=CONFIGURED)

    assert fake.writes[0][0].startswith("A4:")


def test_refuses_when_the_row_now_holds_someone_else(monkeypatch):
    entry = make()
    fake = wire(monkeypatch, {
        4: ["2026-02-02", "Someone Else", "Other", "given", "99.00", "INR", "", "", ""]
    })

    with pytest.raises(RuntimeError, match="no longer matches"):
        store.update(entry, replace(entry, note="new"), secrets=CONFIGURED)

    assert fake.writes == []


def test_refuses_when_the_row_is_gone(monkeypatch):
    fake = wire(monkeypatch, {})
    with pytest.raises(RuntimeError, match="no longer matches"):
        store.update(make(), replace(make(), note="new"), secrets=CONFIGURED)
    assert fake.writes == []


def test_an_entry_with_no_row_cannot_be_edited(monkeypatch):
    wire(monkeypatch, {})
    with pytest.raises(RuntimeError, match="no sheet row"):
        store.update(make(row=None), make(row=None, note="new"), secrets=CONFIGURED)


def test_demo_mode_refuses_rather_than_pretending():
    with pytest.raises(RuntimeError, match="Demo mode"):
        store.update(make(), make(note="new"), secrets={})


def test_the_written_range_covers_every_column(monkeypatch):
    """A short range would leave stale cells behind — clearing a note would
    silently keep the old one."""
    from ledger.models import COLUMNS

    entry = make(note="had a note")
    fake = wire(monkeypatch, {4: entry.to_row()})

    store.update(entry, replace(entry, note=""), secrets=CONFIGURED)

    range_name, values = fake.writes[0]
    assert range_name == f"A4:{store._column_letter(len(COLUMNS))}4"
    assert len(values[0]) == len(COLUMNS)
    assert values[0][6] == ""


def test_editing_a_chat_entry_can_restamp_its_source(monkeypatch):
    entry = make(source=BY_CHAT)
    fake = wire(monkeypatch, {4: entry.to_row()})

    store.update(entry, replace(entry, source=BY_HAND), secrets=CONFIGURED)

    assert fake.writes[0][1][0][8] == BY_HAND


@pytest.mark.parametrize("index,letter", [(1, "A"), (9, "I"), (26, "Z"), (27, "AA"), (52, "AZ")])
def test_column_letters(index, letter):
    assert store._column_letter(index) == letter


def test_source_round_trips_through_a_row():
    entry = make(source=BY_CHAT)
    again = Entry.from_row(dict(zip(__import__("ledger.models", fromlist=["COLUMNS"]).COLUMNS,
                                    entry.to_row())))
    assert again.source == BY_CHAT


def test_a_row_written_before_source_existed_reads_as_unknown():
    from ledger.models import COLUMNS

    old = dict(zip(COLUMNS[:-2], make().to_row()[:-2]))
    assert Entry.from_row(old).source == ""
