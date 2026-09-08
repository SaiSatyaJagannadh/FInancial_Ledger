"""Deleting several rows at once. The dangerous part is the second one.

`delete_rows(5)` moves row 6 up into 5. So the moment the first row goes, every
other row number held by the caller points one row too low — and the second
delete of a three-row selection is aimed at a stranger.

**The fake below shifts, because the real sheet shifts.** A double that just
records the numbers it was handed would pass on any ordering at all, which is
the failure this repo has shipped four times: the fake did what was convenient,
CI stayed green, and the sheet lost a row. This one holds a list and deletes out
of it, so a wrong order fails here the same way it would fail in front of a
person.
"""

from __future__ import annotations

from datetime import date

from ledger import store
from ledger.models import Direction, Entry
from ledger.money import Currency

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def make(row: int, person: str, amount: int = 100_00) -> Entry:
    return Entry(
        date=date(2026, 1, row), person=person, ledger="House repair",
        direction=Direction.given, amount_minor=amount, currency=Currency.INR,
        note="", row=row,
    )


class ShiftingSheet:
    """A worksheet that moves rows up when one is deleted, as gspread's does.

    Row numbers are 1-based and row 1 is the header, which is why `records`
    starts at 2 everywhere in this app — so the entry at row *n* is at index
    *n - 1* of the list below.
    """

    def __init__(self, entries: list[Entry]):
        self.cells: list[list[str]] = [["header"]]
        for entry in sorted(entries, key=lambda e: e.row):
            assert entry.row == len(self.cells) + 1, "rows must be contiguous"
            self.cells.append(entry.to_row())
        self.archived: list[list] = []

    def row_values(self, n: int) -> list[str]:
        return self.cells[n - 1] if 1 <= n <= len(self.cells) else []

    def delete_rows(self, n: int) -> None:
        if 1 <= n <= len(self.cells):
            self.cells.pop(n - 1)

    def append_rows(self, rows, **_kw) -> None:
        self.archived.extend(rows)

    def update(self, values=None, range_name=None, **_kw) -> None:
        pass

    def people(self) -> list[str]:
        """Who is still on the sheet, in order. The header is not a person."""
        return [row[1] for row in self.cells[1:]]


def sheet_of(names: list[str], monkeypatch) -> tuple[ShiftingSheet, list[Entry]]:
    entries = [make(n + 2, name) for n, name in enumerate(names)]
    fake = ShiftingSheet(entries)
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    return fake, entries


def test_the_fake_shifts_rows_up_like_the_real_sheet(monkeypatch):
    """If this is wrong every test below it is worthless."""
    fake, entries = sheet_of(["Amma", "Vihar", "Ravi"], monkeypatch)
    fake.delete_rows(2)
    assert fake.people() == ["Vihar", "Ravi"]
    assert fake.row_values(2)[1] == "Vihar", "Vihar moved up into row 2"


def test_deletes_every_selected_row_and_nothing_else(monkeypatch):
    fake, entries = sheet_of(["Amma", "Vihar", "Ravi", "Chaitu", "Nanna"], monkeypatch)
    amma, vihar, ravi, chaitu, nanna = entries

    problems = store.delete_many([vihar, chaitu, nanna], secrets=CONFIGURED)

    assert problems == []
    assert fake.people() == ["Amma", "Ravi"]


def test_the_selection_order_on_screen_does_not_matter(monkeypatch):
    """Ticks arrive in whatever order somebody clicked them."""
    fake, entries = sheet_of(["Amma", "Vihar", "Ravi", "Chaitu"], monkeypatch)
    amma, vihar, ravi, chaitu = entries

    assert store.delete_many([chaitu, amma, ravi], secrets=CONFIGURED) == []
    assert fake.people() == ["Vihar"]


def test_deleting_in_list_order_would_have_taken_the_wrong_rows(monkeypatch):
    """The bug this ordering exists to prevent, spelled out.

    Ascending, the second delete is aimed one row too low — at a record that is
    now a different person's. The guard in `store.delete` catches it *here*,
    because the rows happen to differ; it is not a defence to rely on, since two
    rows that look alike would let it through and the wrong money would go.
    """
    fake, entries = sheet_of(["Amma", "Vihar", "Ravi", "Chaitu"], monkeypatch)
    amma, vihar, ravi, chaitu = entries

    refused = 0
    for entry in [amma, vihar, ravi]:          # ascending: what not to do
        try:
            store.delete(entry, secrets=CONFIGURED)
        except RuntimeError:
            refused += 1

    assert refused == 2, "only the first delete was aimed at the right row"
    assert fake.people() == ["Vihar", "Ravi", "Chaitu"], "two of the three survived"


def test_every_deleted_row_is_archived_before_it_goes(monkeypatch):
    fake, entries = sheet_of(["Amma", "Vihar", "Ravi"], monkeypatch)

    store.delete_many(entries, secrets=CONFIGURED)

    assert fake.people() == []
    assert len(fake.archived) == 3, "a bulk delete must archive every row, not the last"


def test_one_row_that_cannot_go_does_not_strand_the_others(monkeypatch):
    fake, entries = sheet_of(["Amma", "Vihar", "Ravi"], monkeypatch)
    amma, vihar, ravi = entries
    stale = make(9, "Nobody")               # a row number the sheet does not have

    problems = store.delete_many([amma, stale, ravi], secrets=CONFIGURED)

    assert [entry.person for entry, _why in problems] == ["Nobody"]
    assert "no longer matches" in problems[0][1]
    assert fake.people() == ["Vihar"], "the two good ones still went"


def test_an_entry_with_no_row_is_reported_rather_than_skipped(monkeypatch):
    fake, entries = sheet_of(["Amma", "Vihar"], monkeypatch)
    unsaved = Entry(
        date=date(2026, 1, 1), person="Ghost", ledger="x",
        direction=Direction.given, amount_minor=100, currency=Currency.INR,
        note="", row=None,
    )

    problems = store.delete_many([entries[0], unsaved], secrets=CONFIGURED)

    assert [entry.person for entry, _why in problems] == ["Ghost"]
    assert fake.people() == ["Vihar"]


def test_demo_mode_refuses_all_of_them_rather_than_pretending():
    problems = store.delete_many([make(2, "Amma"), make(3, "Vihar")], secrets={})
    assert len(problems) == 2
    assert all("Demo mode" in why for _entry, why in problems)


def test_nothing_selected_is_not_an_error():
    assert store.delete_many([], secrets=CONFIGURED) == []
