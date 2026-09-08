"""The tick boxes, run the way a browser runs them.

`tests/test_delete_many.py` covers what reaches the sheet. This covers what a
person actually does: tick three rows, read what is about to go, confirm once.
Demo mode's entries carry no row number, so nothing here can use the pages —
these run `entry_table` directly against entries that have rows.
"""

from __future__ import annotations

from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from ledger import store, ui
from ledger.models import Direction, Entry
from ledger.money import Currency

SCOPE = "INR"

#: What the confirm step handed to `store.delete_many`, recorded by the stand-in
#: below. A module-level list because the script runs in this same process.
SENT: list[list[Entry]] = []


def make(row: int, person: str) -> Entry:
    return Entry(
        date=date(2026, 1, row), person=person, ledger="House repair",
        direction=Direction.given, amount_minor=row * 1000_00,
        currency=Currency.INR, note="", row=row,
    )


ENTRIES = [make(2, "Amma"), make(3, "Vihar"), make(4, "Ravi")]


def table() -> None:
    """The script under test: one ledger table, three entries with rows."""
    from datetime import date as _date

    from ledger import ui as _ui
    from ledger.models import Direction as _Direction, Entry as _Entry
    from ledger.money import Currency as _Currency

    rows = [
        _Entry(date=_date(2026, 1, n), person=name, ledger="House repair",
               direction=_Direction.given, amount_minor=n * 1000_00,
               currency=_Currency.INR, note="", row=n)
        for n, name in ((2, "Amma"), (3, "Vihar"), (4, "Ravi"))
    ]
    _ui.entry_table(rows, scope="INR")


@pytest.fixture()
def app(monkeypatch):
    SENT.clear()

    def fake_delete_many(entries, secrets=None):
        SENT.append(list(entries))
        return []

    monkeypatch.setattr(store, "delete_many", fake_delete_many)
    monkeypatch.setattr(ui, "clear_cache", lambda: None)
    return AppTest.from_function(table, default_timeout=30)


def ticks(app: AppTest):
    return app.checkbox


def test_every_row_offers_a_tick_box(app):
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert len(ticks(app)) == 3, "one per entry, in the first column"


def test_nothing_ticked_shows_no_bulk_control(app):
    app.run()
    assert not any("selected" in (b.label or "") for b in app.button), \
        "the bar belongs on screen only once something is selected"


def test_ticking_rows_offers_to_delete_exactly_those(app):
    app.run()
    ticks(app)[0].check().run()
    ticks(app)[2].check().run()

    assert not app.exception, [str(e.message) for e in app.exception]
    assert any("2 selected" in (c.value or "") for c in app.caption), \
        [c.value for c in app.caption]
    assert any(b.label == "Delete 2 selected" for b in app.button), \
        [b.label for b in app.button]


def test_the_confirmation_names_every_entry_rather_than_counting_them(app):
    app.run()
    ticks(app)[0].check().run()
    ticks(app)[1].check().run()
    next(b for b in app.button if b.label == "Delete 2 selected").click().run()

    said = " ".join(w.value for w in app.warning)
    assert "Amma" in said and "Vihar" in said, said
    assert "Ravi" not in said, "an unticked row must not be listed for deletion"
    assert "₹2,000.00" in said and "₹3,000.00" in said, said


def test_confirming_sends_just_the_ticked_ones_and_clears_the_ticks(app):
    app.run()
    ticks(app)[1].check().run()
    ticks(app)[2].check().run()
    next(b for b in app.button if b.label == "Delete 2 selected").click().run()
    next(b for b in app.button if b.label == "Delete 2 entries").click().run()

    assert not app.exception, [str(e.message) for e in app.exception]
    assert [e.person for e in SENT[0]] == ["Vihar", "Ravi"]
    # Rows move up when one goes, so a tick left behind would mark whichever
    # entry has slid into that row.
    assert not [c for c in ticks(app) if c.value], "the boxes must come back empty"


def test_cancelling_the_confirmation_deletes_nothing(app):
    app.run()
    ticks(app)[0].check().run()
    next(b for b in app.button if b.label == "Delete 1 selected").click().run()
    next(b for b in app.button if b.label == "Cancel").click().run()

    assert SENT == []
    assert any(b.label == "Delete 1 selected" for b in app.button), \
        "cancelling the confirmation keeps the selection, it does not drop it"


def test_clear_selection_unticks_without_deleting(app):
    app.run()
    ticks(app)[0].check().run()
    next(b for b in app.button if b.label == "Clear selection").click().run()

    assert SENT == []
    assert not [c for c in ticks(app) if c.value]


def test_what_could_not_be_deleted_is_named_after_the_reload(app, monkeypatch):
    """Some rows go and the page reloads, so the failure has to survive it."""
    def refuse_one(entries, secrets=None):
        return [(entries[-1], "Row 4 no longer matches this entry")]

    monkeypatch.setattr(store, "delete_many", refuse_one)

    app.run()
    ticks(app)[0].check().run()
    ticks(app)[2].check().run()
    next(b for b in app.button if b.label == "Delete 2 selected").click().run()
    next(b for b in app.button if b.label == "Delete 2 entries").click().run()

    shown = " ".join(e.value for e in app.error)
    assert "Deleted 1 of 2" in shown, shown
    assert "no longer matches" in shown, shown
