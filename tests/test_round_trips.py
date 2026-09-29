"""How many times each operation talks to Google.

Every one of these is a whole HTTPS round trip from the app to Google and back,
and they happen one after another — so the count *is* the wait a person sees.
A delete used to cost eight, most of which were the library re-fetching the
workbook's layout to answer "which tab is `entries`?" and re-reading row 1 to
ask "does this tab have a header yet?". Both answers are the same all day.

These tests are budgets. They fail when an operation starts costing more trips
than it did, which is the regression that is otherwise invisible — everything
still works, it is just slower every time, for ever.
"""

from __future__ import annotations

from datetime import date

import pytest

from ledger import store
from ledger.models import Direction, Entry

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit",
              "worksheet": "entries"},
}


def entry(row: int | None = 5) -> Entry:
    return Entry(date=date(2026, 3, 1), person="RAVI", ledger="Personal",
                 direction=Direction.given, amount_minor=250_000, row=row)


class FakeWorksheet:
    """Counts like gspread does: one trip per values/batch call."""

    def __init__(self, book, title, rows=None):
        self.book, self.title = book, title
        self.rows = dict(rows or {})

    def _trip(self, what):
        self.book.calls.append(f"{self.title}.{what}")

    def row_values(self, n):
        self._trip("row_values")
        return list(self.rows.get(n, []))

    def get_all_records(self, expected_headers=None, **kw):
        self._trip("get_all_records")
        header = list(expected_headers or self.rows.get(1, []))
        return [dict(zip(header, list(self.rows[n]) + [""] * len(header)))
                for n in sorted(self.rows) if n != 1]

    def get_all_values(self):
        self._trip("get_all_values")
        return [list(self.rows[n]) for n in sorted(self.rows)]

    def append_rows(self, rows, **kw):
        self._trip("append_rows")
        for row in rows:
            self.rows[max(self.rows, default=0) + 1] = list(row)

    def delete_rows(self, n):
        self._trip("delete_rows")
        self.rows.pop(n, None)

    def update(self, values=None, range_name=None, **kw):
        self._trip("update")


class FakeBook:
    """`open_by_key` is free in gspread 6 — it builds an object from the id.

    `worksheet(title)` is not: it calls `fetch_sheet_metadata`, which is a
    network round trip, *every time it is called*. That is the cost this
    whole file exists to count.
    """

    def __init__(self, tabs):
        self.calls: list[str] = []
        self.sheets = {name: FakeWorksheet(self, name, rows)
                       for name, rows in tabs.items()}

    def worksheet(self, title):
        self.calls.append("fetch_sheet_metadata")
        if title not in self.sheets:
            from gspread.exceptions import WorksheetNotFound

            raise WorksheetNotFound(title)
        return self.sheets[title]

    def add_worksheet(self, title, rows=200, cols=20):
        self.calls.append("add_worksheet")
        self.sheets[title] = FakeWorksheet(self, title, {})
        return self.sheets[title]

    @property
    def sheet1(self):
        self.calls.append("fetch_sheet_metadata")
        return next(iter(self.sheets.values()))


class FakeClient:
    def __init__(self, book):
        self.book = book

    def open_by_url(self, url):
        return self.book          # free, exactly as gspread 6 is

    def open_by_key(self, key):
        return self.book


@pytest.fixture()
def book(monkeypatch):
    from ledger.models import COLUMNS

    made = FakeBook({
        "entries": {1: list(COLUMNS), 5: entry().to_row()},
        "deleted": {1: ["deleted_at", "kind", "by", "summary", "source_row", "data"]},
    })
    monkeypatch.setattr(store, "_client", lambda account: FakeClient(made))
    monkeypatch.setattr(store, "_secrets", lambda: CONFIGURED)
    store.forget_sheets()
    yield made
    store.forget_sheets()


def trips(book) -> int:
    return len(book.calls)


def test_the_fake_counts_what_gspread_would():
    """A counter that misses the expensive call would make every budget pass."""
    made = FakeBook({"entries": {1: ["a"]}})
    made.worksheet("entries").row_values(1)
    assert made.calls == ["fetch_sheet_metadata", "entries.row_values"]


def test_opening_the_same_tab_twice_costs_one_trip(book):
    store._open_worksheet(CONFIGURED)
    store._open_worksheet(CONFIGURED)
    store._open_worksheet(CONFIGURED)
    assert trips(book) == 1, book.calls


def test_a_delete_costs_a_guard_read_an_archive_and_the_delete(book):
    """Cold — the first write after a deploy — also pays to find the two tabs
    and to check the archive has a header. Warm, only the three that carry
    meaning: confirm the row is still the right one, archive it, remove it."""
    store.delete(entry(), CONFIGURED)
    assert trips(book) == 6, book.calls
    assert "deleted.append_rows" in book.calls and "entries.delete_rows" in book.calls

    book.sheets["entries"].rows[9] = entry(row=9).to_row()
    book.calls.clear()
    store.delete(entry(row=9), CONFIGURED)
    assert trips(book) == 3, book.calls
    assert "fetch_sheet_metadata" not in book.calls


def test_an_append_costs_one_trip_once_the_tab_is_known(book):
    store.append(entry(row=None), CONFIGURED)
    assert trips(book) == 3, book.calls          # find the tab, check the header, write
    book.calls.clear()
    store.append(entry(row=None), CONFIGURED)
    assert book.calls == ["entries.append_rows"], book.calls


def test_a_read_costs_one_trip_once_the_tab_is_known(book):
    store.load(CONFIGURED)
    book.calls.clear()
    store.load(CONFIGURED)
    assert book.calls == ["entries.get_all_records"], book.calls


def test_reading_a_tab_twice_does_not_ask_google_twice(book):
    """Streamlit re-runs the whole script on every keystroke, so a filter box
    was re-reading the tab from Google letter by letter. `records` holds it."""
    from ledger.models import COLUMNS

    sheet = store._open_worksheet(CONFIGURED, "entries")
    store.records(sheet, COLUMNS)
    book.calls.clear()
    for _ in range(5):
        store.records(sheet, COLUMNS)
    assert book.calls == [], book.calls


def test_a_write_drops_the_held_rows(book):
    """Held for a minute, but never across a change — the row somebody just
    saved has to be on the screen they land on."""
    from ledger.models import COLUMNS

    sheet = store._open_worksheet(CONFIGURED, "entries")
    assert len(store.records(sheet, COLUMNS)) == 1
    store.append(entry(row=None), CONFIGURED)
    assert len(store.records(sheet, COLUMNS)) == 2, "the new row must show up"


def test_every_write_door_drops_the_held_rows(book):
    """The three doors every write in this app goes through. A fourth way to
    write would be a way to leave somebody looking at rows that are gone."""
    from ledger.models import COLUMNS

    sheet = store._open_worksheet(CONFIGURED, "entries")
    for write in (
        lambda: store.append_rows(sheet, [entry(row=None).to_row()]),
        lambda: store.write_cells(sheet, [entry(row=None).to_row()], "A5:I5"),
        lambda: store.delete_row(sheet, 5),
    ):
        store.records(sheet, COLUMNS)
        assert store._ROWS, "something should be held before the write"
        write()
        assert not store._ROWS, "and nothing after it"


def test_held_rows_are_copied_out_not_handed_out(book):
    """A caller that edits a row it was given would otherwise poison the cache
    for every page after it."""
    from ledger.models import COLUMNS

    sheet = store._open_worksheet(CONFIGURED, "entries")
    first = store.records(sheet, COLUMNS)
    first[0]["person"] = "SOMEBODY ELSE"
    assert store.records(sheet, COLUMNS)[0]["person"] == "RAVI"


def test_a_stale_tab_handle_is_dropped_when_google_says_it_is_gone(book):
    """A tab renamed in the sheet must not break every call until a redeploy."""
    store._open_worksheet(CONFIGURED)
    assert store._SHEETS
    store.forget_sheets()
    assert not store._SHEETS and not store._HEADERS and not store._ROWS


def test_deleting_several_rows_reopens_nothing(book):
    from ledger.models import COLUMNS

    for row in (6, 7, 8):
        book.sheets["entries"].rows[row] = entry(row=row).to_row()
    book.sheets["entries"].rows[1] = list(COLUMNS)

    store.delete_many([entry(row=r) for r in (5, 6, 7, 8)], CONFIGURED)
    # Three trips a row — guard, archive, delete — and the tabs found once.
    assert trips(book) <= 15, f"{trips(book)} trips: {book.calls}"
    assert book.calls.count("fetch_sheet_metadata") == 2, book.calls


def test_a_write_also_clears_the_pages_own_cache(book):
    """The ledger page holds its rows in a Streamlit cache for a minute. A row
    somebody just saved has to be on the screen they land on, not a minute
    later, so a write clears that too — through a hook, because `store` must
    not import the views layer."""
    cleared = []
    store.ON_WRITE.append(lambda: cleared.append(True))
    try:
        store.append(entry(row=None), CONFIGURED)
        assert cleared, "a write should have cleared it"
    finally:
        store.ON_WRITE.pop()


def test_a_failing_hook_never_fails_a_save(book):
    """Clearing a cache is housekeeping. The row is already in the sheet."""
    def boom():
        raise RuntimeError("no streamlit runtime here")

    store.ON_WRITE.append(boom)
    try:
        store.append(entry(row=None), CONFIGURED)   # must not raise
    finally:
        store.ON_WRITE.remove(boom)
