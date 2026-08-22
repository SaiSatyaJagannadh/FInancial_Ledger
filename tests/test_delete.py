"""Deleting a row. The dangerous part is deleting the *wrong* row."""

from __future__ import annotations

from datetime import date

import pytest

from ledger import store
from ledger.models import Direction, Entry
from ledger.money import Currency

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def make(row: int | None = 2, **kw) -> Entry:
    fields = dict(
        date=date(2026, 1, 24), person="Father", ledger="House repair",
        direction=Direction.given, amount_minor=120_050, currency=Currency.INR,
        note="UPI", row=row,
    )
    fields.update(kw)
    return Entry(**fields)


class FakeSheet:
    def __init__(self, rows: dict[int, list[str]]):
        self.rows = rows
        self.deleted: list[int] = []

    def row_values(self, n):
        return self.rows.get(n, [])

    def delete_rows(self, n):
        self.deleted.append(n)


def sheet_for(entry: Entry) -> FakeSheet:
    return FakeSheet({entry.row: entry.to_row()})


def test_deletes_the_row_when_it_still_matches(monkeypatch):
    entry = make()
    fake = sheet_for(entry)
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)

    store.delete(entry, secrets=CONFIGURED)

    assert fake.deleted == [2]


def test_refuses_when_the_row_now_holds_something_else(monkeypatch):
    """Rows shift when anything above them is removed. A stale row number must
    not be allowed to delete an unrelated record."""
    entry = make()
    fake = FakeSheet({2: ["2026-02-02", "Someone Else", "Other", "given", "99.00", "INR", ""]})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)

    with pytest.raises(RuntimeError, match="no longer matches"):
        store.delete(entry, secrets=CONFIGURED)

    assert fake.deleted == []


def test_refuses_when_the_row_is_gone(monkeypatch):
    entry = make()
    fake = FakeSheet({})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)

    with pytest.raises(RuntimeError, match="no longer matches"):
        store.delete(entry, secrets=CONFIGURED)

    assert fake.deleted == []


def test_tolerates_a_narrower_sheet(monkeypatch):
    """A sheet written before `attachment` existed has fewer cells, but the
    identifying fields still match, so the delete must go ahead."""
    entry = make()
    fake = FakeSheet({2: entry.to_row()[:6]})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)

    store.delete(entry, secrets=CONFIGURED)

    assert fake.deleted == [2]


@pytest.mark.parametrize("stored", ["1200.5", "1200", "1,200.50", " 1200.50 "])
def test_amount_is_compared_as_a_number_not_as_text(monkeypatch, stored):
    """Sheets hands back what it stored, not what we wrote.

    We write "1200.50" and Google returns "1200.5". Comparing those as strings
    made every row look changed, which made deletion impossible in the real app
    while passing against a fake that echoed the row back verbatim.
    """
    entry = make(amount_minor=120_050 if stored != "1200" else 120_000)
    row = entry.to_row()
    row[4] = stored
    fake = FakeSheet({2: row})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: fake)

    store.delete(entry, secrets=CONFIGURED)

    assert fake.deleted == [2]


def test_a_different_amount_on_the_row_still_blocks_the_delete(monkeypatch):
    entry = make(amount_minor=120_050)
    row = entry.to_row()
    row[4] = "999.00"
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: FakeSheet({2: row}))

    with pytest.raises(RuntimeError, match="no longer matches"):
        store.delete(entry, secrets=CONFIGURED)


def test_an_unparseable_row_blocks_the_delete(monkeypatch):
    """Garbage in the cells must fail closed, not raise out of the guard."""
    monkeypatch.setattr(
        store, "_open_worksheet",
        lambda _s: FakeSheet({2: ["not-a-date", "Father", "House repair", "given", "x"]}),
    )
    with pytest.raises(RuntimeError, match="no longer matches"):
        store.delete(make(), secrets=CONFIGURED)


def test_an_entry_with_no_row_cannot_be_deleted(monkeypatch):
    monkeypatch.setattr(store, "_open_worksheet", lambda _s: FakeSheet({}))
    with pytest.raises(RuntimeError, match="no sheet row"):
        store.delete(make(row=None), secrets=CONFIGURED)


def test_demo_mode_refuses_rather_than_pretending():
    with pytest.raises(RuntimeError, match="Demo mode"):
        store.delete(make(), secrets={})
