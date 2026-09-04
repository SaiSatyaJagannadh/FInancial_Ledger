"""The record of what was deleted.

Two properties carry the weight. **A deletion that cannot be archived does not
happen** — a sheet has no undo, so losing the row entirely is worse than
refusing to remove it. And **a restore is exact**, not an approximation: it goes
back through the same `from_row` a sheet row goes through, so what returns is
byte-for-byte what left.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from ledger import archive, interest, store
from ledger.models import COLUMNS, Direction, Entry, EntryError
from ledger.money import Currency

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def entry(row=46, **kw) -> Entry:
    fields = dict(date=date(2026, 8, 27), person="Narayana Rao D", ledger="Nanna",
                  direction=Direction.given, amount_minor=2_00_000_00,
                  currency=Currency.INR, note="uncle", row=row)
    fields.update(kw)
    return Entry(**fields)


class FakeSheet:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.deleted, self.archived = [], []

    def row_values(self, n):
        return self.rows.get(n, [])

    def delete_rows(self, n):
        self.deleted.append(n)

    def append_rows(self, rows, **kw):
        self.archived.extend(rows)

    def update(self, values=None, range_name=None, **kw):
        pass


@pytest.fixture()
def sheet(monkeypatch):
    fake = FakeSheet({46: entry().to_row()})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    return fake


# ------------------------------------------- a failed archive stops the delete

def test_the_row_survives_when_it_cannot_be_archived(monkeypatch, sheet):
    """The whole reason the archive is written first."""
    monkeypatch.setattr(archive, "record",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no tab")))
    with pytest.raises(OSError):
        store.delete(entry(), secrets=CONFIGURED)
    assert sheet.deleted == [], "the row must still be on the sheet"


def test_an_interest_charge_survives_the_same_way(monkeypatch):
    fake = FakeSheet({7: ["2026-07-01", "Narayana", "15000.00"]})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    monkeypatch.setattr(interest, "_sheet", lambda _s: fake)
    monkeypatch.setattr(archive, "record",
                        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("no tab")))
    charge = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                             amount_minor=15_000_00, row=7)
    with pytest.raises(OSError):
        interest.remove(charge, secrets=CONFIGURED)
    assert fake.deleted == []


def test_the_archive_is_written_before_the_row_is_removed(sheet):
    store.delete(entry(), secrets=CONFIGURED)
    assert sheet.archived, "nothing was archived"
    assert sheet.deleted == [46]


def test_what_is_archived_carries_the_whole_row(sheet):
    store.delete(entry(), secrets=CONFIGURED)
    kept = dict(zip(archive.COLUMNS, sheet.archived[0]))
    assert json.loads(kept["data"]) == entry().to_row()
    assert kept["kind"] == archive.ENTRY
    assert kept["source_row"] == "46"
    assert "Narayana Rao D" in kept["summary"], "it must be readable without JSON"


# ---------------------------------------------------------- a restore is exact

def test_a_restored_entry_is_identical_to_what_was_removed():
    original = entry()
    kept = archive.Deletion(deleted_at=datetime.now(), kind=archive.ENTRY, by="",
                            summary="", source_row=46, data=original.to_row())
    assert archive.rebuild(kept).to_row() == original.to_row()


def test_a_restored_charge_is_identical_too():
    charge = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                             amount_minor=15_000_00, note="monthly")
    kept = archive.Deletion(deleted_at=datetime.now(), kind=archive.INTEREST,
                            by="", summary="", source_row=7, data=charge.to_row())
    assert archive.rebuild(kept).to_row() == charge.to_row()


def test_a_restore_appends_rather_than_reusing_the_old_row_number(monkeypatch):
    """Everything below moved up when the row went; the number means nothing."""
    written = []
    monkeypatch.setattr(store, "append", lambda e, s=None: written.append(e))
    monkeypatch.setattr(archive, "_forget", lambda *_a, **_kw: None)
    kept = archive.Deletion(deleted_at=datetime.now(), kind=archive.ENTRY, by="",
                            summary="", source_row=46, data=entry().to_row())
    back = archive.restore(kept, secrets=CONFIGURED)
    assert written and written[0].to_row() == entry().to_row()
    assert back.row is None, "a restored row must not claim a stale position"


def test_a_deletion_with_no_data_refuses_rather_than_inventing_one():
    kept = archive.Deletion(deleted_at=datetime.now(), kind=archive.ENTRY, by="",
                            summary="", source_row=None, data=[])
    with pytest.raises(EntryError):
        archive.rebuild(kept)


def test_an_unknown_kind_refuses():
    kept = archive.Deletion(deleted_at=datetime.now(), kind="nonsense", by="",
                            summary="", source_row=None, data=["x"])
    with pytest.raises(EntryError):
        archive.rebuild(kept)


# --------------------------------------------------------------- reading it back

def test_a_corrupted_row_costs_that_row_not_the_page():
    got = archive.Deletion.from_row(
        {"deleted_at": "2026-09-03T14:22:00", "kind": archive.ENTRY,
         "data": "{not json"})
    assert got.data == []


def test_rows_come_back_newest_first(monkeypatch):
    def rows():
        return [
            {"deleted_at": "2026-09-01T10:00:00", "kind": archive.ENTRY,
             "by": "", "summary": "older", "source_row": "2", "data": "[]"},
            {"deleted_at": "2026-09-03T10:00:00", "kind": archive.ENTRY,
             "by": "", "summary": "newer", "source_row": "3", "data": "[]"},
        ]

    fake = FakeSheet()
    fake.get_all_records = rows
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    got, problems = archive.load(CONFIGURED)
    assert [d.summary for d in got] == ["newer", "older"]
    assert problems == []


def test_demo_mode_archives_nothing():
    with pytest.raises(RuntimeError, match="Demo mode"):
        archive.record(archive.ENTRY, entry(), secrets={})
    assert archive.load({}) == ([], [])
