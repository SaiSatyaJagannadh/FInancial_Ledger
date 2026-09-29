"""Old debt clearances: the model, the grouping, and the guards on a write.

The point of this tab is what it does *not* do — it is summed into nothing — so
one test below asserts that no other module imports it.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ledger import clearance, store
from ledger.clearance import Clearance
from ledger.models import EntryError
from ledger.money import Currency

CONFIGURED = {
    "gcp_service_account": {"client_email": "x@y.iam.gserviceaccount.com"},
    "sheet": {"url": "https://docs.google.com/spreadsheets/d/abc/edit"},
}


def make(row: int | None = 4, **kw) -> Clearance:
    fields = dict(date=date(2026, 3, 1), payer="RAVI", under="VIHAR",
                  amount_minor=250_000, row=row)
    fields.update(kw)
    return Clearance(**fields)


class FakeSheet:
    """Copies gspread's signatures, including the arguments we do not use —
    a double that is merely convenient is how four bugs shipped green."""

    def __init__(self, rows):
        self.rows = rows
        self.writes: list = []
        self.deleted: list[int] = []
        self.appended: list = []
        self.options: list = []

    def row_values(self, n):
        return self.rows.get(n, [])

    def update(self, values=None, range_name=None, **kw):
        self.writes.append((range_name, values))

    def delete_rows(self, n):
        self.deleted.append(n)

    def append_rows(self, rows, **kw):
        self.options.append(kw.get("insert_data_option"))
        for row in rows:
            self.appended.append(list(row))
            # INSERT_ROWS lands after the last row that holds anything, which
            # is what makes the read-back below exercise the real thing.
            self.rows[max(self.rows, default=0) + 1] = list(row)

    #: gspread's own signature. It refuses a header row with duplicates — and
    #: blank headings are duplicates of each other — unless expected_headers
    #: names the columns, which is the whole reason `store.records` exists.
    def get_all_records(self, expected_headers=None, **kw):
        header = self.rows.get(1, [])
        if expected_headers is None:
            blanks = [h for h in header if not str(h).strip()]
            if len(blanks) > 1:
                raise ValueError("the header row in the worksheet is not unique")
            header = list(header)
        else:
            header = list(expected_headers)
        return [
            dict(zip(header, list(self.rows[n]) + [""] * len(header)))
            for n in sorted(self.rows) if n != 1
        ]

    def get_all_values(self):
        return [list(self.rows[n]) for n in sorted(self.rows)]


def wire(monkeypatch, rows) -> FakeSheet:
    """A whole fake workbook, one sheet per tab.

    One fake standing in for every tab would let an archive write land in the
    clearances tab and still look right — and the archive is the thing standing
    between a mis-click and a lost record, so it gets its own sheet to land in.
    The clearances sheet comes back; the rest hang off it as `.book`.
    """
    book: dict[str, FakeSheet] = {
        clearance.WORKSHEET: FakeSheet({1: list(clearance.COLUMNS), **rows})
    }

    def open_worksheet(_secrets, tab=None):
        return book.setdefault(tab or clearance.WORKSHEET, FakeSheet({}))

    monkeypatch.setattr(store, "_open_worksheet", open_worksheet)
    monkeypatch.setattr(store, "_secrets", lambda: CONFIGURED)
    mine = book[clearance.WORKSHEET]
    mine.book = book
    return mine


def test_the_modules_self_check_passes():
    clearance.demo()


# ------------------------------------------------------------------- the model

@pytest.mark.parametrize("field", ["date", "payer", "under", "amount"])
def test_a_missing_required_field_is_named(field):
    row = dict(zip(clearance.COLUMNS, make().to_row()))
    row[field] = ""
    with pytest.raises(EntryError, match=field):
        Clearance.from_row(row)


def test_a_zero_amount_is_refused():
    with pytest.raises(EntryError):
        make(amount_minor=0)


def test_the_row_round_trips_through_the_same_door_a_sheet_row_does():
    original = make(row=None, note="cash, old college loan", currency=Currency.USD)
    assert Clearance.from_row(dict(zip(clearance.COLUMNS, original.to_row()))) == original


def test_the_amount_is_written_as_minor_units_not_float_division():
    assert make(amount_minor=1).to_row()[3] == "0.01"
    assert make(amount_minor=250_000).to_row()[3] == "2500.00"


# ---------------------------------------------------------------- the grouping

def test_records_are_filed_under_the_name_they_were_given_under():
    rows = [
        make(under="VIHAR", payer="RAVI", amount_minor=250_000),
        make(under="vihar", payer="AMMA", amount_minor=150_000),
        make(under="CHAITU", payer="RAVI", amount_minor=50_000),
    ]
    grouped = clearance.by_under(rows, Currency.INR)
    # Sorted by name, case-insensitively — and a differently-cased name is its
    # own row, because the sheet is hand-editable and we do not guess.
    assert [b["under"] for b in grouped] == ["CHAITU", "VIHAR", "vihar"]


def test_a_groups_payers_are_listed_once_each():
    rows = [make(payer="RAVI"), make(payer="AMMA"), make(payer="RAVI")]
    assert clearance.by_under(rows, Currency.INR)[0]["payers"] == ["AMMA", "RAVI"]


def test_currencies_are_never_mixed():
    rows = [make(amount_minor=250_000), make(amount_minor=90_000, currency=Currency.USD)]
    rupees = clearance.by_under(rows, Currency.INR)
    assert len(rupees) == 1 and rupees[0]["total_minor"] == 250_000
    assert clearance.recorded_total(rows, Currency.USD) == 90_000


#: The only modules allowed to know this tab exists. `archive` and the Deleted
#: page *move* these rows — they hold a deleted one and put it back — which is
#: not the same as adding them up. `compute`, `store`, `facts`, `export` and
#: the assistant are the ones that must never appear here.
MAY_TOUCH = ["ledger/archive.py", "views/clearance.py", "views/deleted.py"]


def test_nothing_else_in_the_app_sums_these_rows():
    """The whole reason this tab exists is that it feeds no total. A future
    import into compute/store/facts is exactly the mistake to catch here."""
    root = Path(__file__).resolve().parent.parent
    importers = sorted(
        name
        for name in (
            path.relative_to(root).as_posix()
            for path in (*root.glob("ledger/*.py"), *root.glob("views/*.py"))
            if "clearance" in path.read_text()
        )
        if name != "ledger/clearance.py"
    )
    assert importers == MAY_TOUCH, importers


# --------------------------------------------------------------- writing to it

def test_add_appends_through_the_guarded_helper(monkeypatch):
    fake = wire(monkeypatch, {})
    clearance.add(make(row=None))
    assert fake.appended == [make(row=None).to_row()]
    # INSERT_ROWS, or Google's append overwrites whatever sits below a gap.
    assert fake.options == ["INSERT_ROWS"]


def test_a_brand_new_tab_gets_its_header_written_to_a1(monkeypatch):
    fake = FakeSheet({})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    monkeypatch.setattr(store, "_secrets", lambda: CONFIGURED)
    clearance.add(make(row=None))
    assert fake.writes == [("A1", [clearance.COLUMNS])]


def test_delete_removes_a_matching_row(monkeypatch):
    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})
    clearance.remove(make())
    assert fake.deleted == [4]


def test_delete_refuses_when_the_row_now_holds_somebody_else(monkeypatch):
    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "CHAITU", "2500.00", "INR"]})
    with pytest.raises(RuntimeError, match="no longer matches"):
        clearance.remove(make())
    assert fake.deleted == []


def test_the_amount_is_compared_as_a_number(monkeypatch):
    """Sheets hands back "2500" for what was written as "2500.00"; a string
    comparison passes against a fake and fails against the real sheet."""
    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500", "INR"]})
    clearance.remove(make())
    assert fake.deleted == [4]


def test_edit_writes_over_the_original_row(monkeypatch):
    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})
    clearance.replace_row(make(), make(amount_minor=999_00, under="CHAITU", row=99))
    range_name, values = fake.writes[-1]
    # The row it came from, not the row the edited copy claims to be on.
    assert range_name.startswith("A4:"), range_name
    assert values[0][2] == "CHAITU" and values[0][3] == "999.00"


def test_edit_refuses_when_the_row_now_holds_somebody_else(monkeypatch):
    fake = wire(monkeypatch, {4: ["2026-03-01", "AMMA", "CHAITU", "100.00", "INR"]})
    with pytest.raises(RuntimeError, match="no longer matches"):
        clearance.replace_row(make(), make(note="corrected"))
    assert not [w for w in fake.writes if w[0] and w[0].startswith("A4:")]


def test_an_edit_covers_every_column_it_writes(monkeypatch):
    """A short range would leave the tail of the old row in place — an edit
    that blanks a note has to actually blank it."""
    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})
    clearance.replace_row(make(note="old", attachment="sheet:ab12"),
                          make(note="", attachment=""))
    range_name, values = fake.writes[-1]
    assert range_name == f"A4:{store._column_letter(len(clearance.COLUMNS))}4"
    assert len(values[0]) == len(clearance.COLUMNS)
    assert values[0][-1] == "" and values[0][5] == ""


def test_an_edit_is_not_archived(monkeypatch):
    """Deliberate: an edit keeps the row and changes what it says, so there is
    no moment where the record is absent — which is what the archive is for."""
    from ledger import archive

    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})
    clearance.replace_row(make(), make(note="corrected"))
    assert archive.WORKSHEET not in fake.book or not fake.book[archive.WORKSHEET].appended


def test_a_row_with_no_number_cannot_be_edited(monkeypatch):
    wire(monkeypatch, {})
    with pytest.raises(RuntimeError, match="no sheet row"):
        clearance.replace_row(make(row=None), make(row=None, note="x"))


def test_a_row_with_no_number_cannot_be_deleted(monkeypatch):
    wire(monkeypatch, {})
    with pytest.raises(RuntimeError, match="no sheet row"):
        clearance.remove(make(row=None))


def test_demo_mode_reads_nothing_and_refuses_to_write():
    assert clearance.load({}) == ([], [])
    with pytest.raises(RuntimeError, match="Demo mode"):
        clearance.add(make(row=None), secrets={})

def test_a_saved_record_reads_back_off_the_sheet(monkeypatch):
    """The write and the read, in one test. Asserting only the append — or
    monkeypatching `load` — is how a write that never round-tripped shipped."""
    wire(monkeypatch, {})
    original = make(row=None, note="old college loan, paid in cash",
                    amount_minor=317_50, currency=Currency.USD)
    clearance.add(original)

    rows, problems = clearance.load()
    assert problems == []
    assert len(rows) == 1
    back = rows[0]
    assert back.row == 2                      # header is row 1
    assert back == original                   # `row` is excluded from equality
    assert back.amount_minor == 317_50 and back.currency is Currency.USD
    assert back.note == original.note


def test_a_twenty_column_tab_with_blank_spare_headings_still_reads(monkeypatch):
    """`_open_worksheet` makes a new tab 20 columns wide and we write seven
    headings in. A bare `get_all_records()` refuses that — every sign-in once
    failed on exactly this — so the read must go through `store.records`."""
    wide = list(clearance.COLUMNS) + [""] * 13
    fake = FakeSheet({1: wide, 2: make(row=None).to_row()})
    monkeypatch.setattr(store, "_open_worksheet", lambda _s, tab=None: fake)
    monkeypatch.setattr(store, "_secrets", lambda: CONFIGURED)

    with pytest.raises(ValueError):             # the fake refuses it, like gspread
        fake.get_all_records()
    rows, problems = clearance.load()
    assert problems == [] and len(rows) == 1
    assert rows[0].under == "VIHAR"


def test_a_malformed_row_is_named_and_does_not_hide_the_good_ones(monkeypatch):
    wire(monkeypatch, {
        2: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR", "", ""],
        3: ["not a date", "AMMA", "CHAITU", "100.00", "INR", "", ""],
        4: ["2026-04-01", "", "CHAITU", "100.00", "INR", "", ""],
    })
    rows, problems = clearance.load()
    assert len(rows) == 1 and rows[0].payer == "RAVI"
    assert len(problems) == 2
    assert "row 3" in problems[0] and "row 4" in problems[1]
    assert "payer" in problems[1]


def test_a_blank_spacer_row_is_skipped_rather_than_reported(monkeypatch):
    wire(monkeypatch, {
        2: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR", "", ""],
        3: ["", "", "", "", "", "", ""],
    })
    rows, problems = clearance.load()
    assert len(rows) == 1 and problems == []


def test_rows_come_back_filed_under_their_name(monkeypatch):
    """`load` sorts by the name it was given under, so the page groups without
    having to sort first."""
    wire(monkeypatch, {
        2: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR", "", ""],
        3: ["2026-03-02", "AMMA", "CHAITU", "100.00", "INR", "", ""],
        4: ["2026-01-05", "RAVI", "VIHAR", "700.00", "INR", "", ""],
    })
    rows, _ = clearance.load()
    assert [(c.under, c.date.month) for c in rows] == [
        ("CHAITU", 3), ("VIHAR", 1), ("VIHAR", 3)
    ]


# ------------------------------------------------------- what is on the screen
# `tests/test_pages.py` renders this page in demo mode, where there are no rows
# at all — so the grouped list, the per-name subtotals and the delete control
# are never reached there. These drive the page with rows on it. `load` is
# stubbed, which would be the wrong thing to do to a *write* test; the read path
# has its own fake-sheet tests above, and what is under test here is the screen.

PAGE = str(Path(__file__).resolve().parent.parent / "views" / "clearance.py")

SCREEN = [
    Clearance(date=date(2026, 3, 1), payer="RAVI", under="VIHAR",
              amount_minor=250_000, note="old cash loan",
              attachment="sheet:ab12", row=2),
    Clearance(date=date(2026, 4, 9), payer="AMMA", under="VIHAR",
              amount_minor=150_000, row=3),
    Clearance(date=date(2026, 2, 2), payer="RAVI", under="CHAITU",
              amount_minor=50_000, row=4),
    Clearance(date=date(2025, 8, 8), payer="RAVI", under="ammu",
              amount_minor=9_900, row=5),
    Clearance(date=date(2026, 5, 5), payer="RAVI", under="VIHAR",
              amount_minor=90_000, currency=Currency.USD, row=6),
]


def on_screen(monkeypatch, rows=SCREEN):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(clearance, "load", lambda *a, **k: (list(rows), []))
    app = AppTest.from_file(PAGE, default_timeout=60)
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    return app


def test_the_page_files_the_records_under_each_name_alphabetically(monkeypatch):
    app = on_screen(monkeypatch)
    assert [e.label for e in app.expander] == [
        "ammu — ₹99.00 · 1 record",
        "CHAITU — ₹500.00 · 1 record",
        "VIHAR — ₹4,000.00 · 2 records",
    ]
    assert "From: AMMA, RAVI" in [c.value for c in app.caption]


def test_the_page_says_its_total_is_counted_nowhere_else(monkeypatch):
    """The figure is the sum of these rows only. Saying so on screen is the
    whole point — a number under a heading that does not explain itself is how
    somebody comes to believe it moved the ledger."""
    captions = [c.value for c in on_screen(monkeypatch).caption]
    assert any("₹4,599.00 across 4 records" in c for c in captions), captions
    assert any("counted nowhere else" in c for c in captions), captions


def test_the_page_never_mixes_the_currencies(monkeypatch):
    app = on_screen(monkeypatch)
    app.radio(key="cl_currency").set_value("USD").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert [e.label for e in app.expander] == ["VIHAR — $900.00 · 1 record"]


def test_the_year_filter_narrows_the_filing(monkeypatch):
    app = on_screen(monkeypatch)
    app.selectbox(key="cl_year").select(2025).run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert [e.label for e in app.expander] == ["ammu — ₹99.00 · 1 record"]


def test_deleting_asks_twice_because_the_sheet_has_no_undo(monkeypatch):
    app = on_screen(monkeypatch)
    assert "Delete it" not in [b.label for b in app.button]
    app.button(key="cl_del_2").click().run()
    assert not app.exception, [str(e.message) for e in app.exception]
    labels = [b.label for b in app.button]
    assert "Delete it" in labels and "Cancel" in labels


def test_an_empty_form_cannot_be_saved_and_says_what_is_missing(monkeypatch):
    app = on_screen(monkeypatch, rows=[])
    assert [b.label for b in app.button] == ["Save record"]
    assert app.button[0].disabled
    assert any("Amount" in w.value for w in app.warning), [w.value for w in app.warning]


def test_a_filled_form_previews_what_it_will_record(monkeypatch):
    app = on_screen(monkeypatch, rows=[])
    names = app.selectbox(key="cl_payer").options
    app.selectbox(key="cl_payer").select(names[0])
    app.selectbox(key="cl_under").select(names[1])
    app.text_input(key="cl_amount").set_value("2500")
    app.run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert not app.button[0].disabled
    preview = " ".join(i.value for i in app.info)
    assert "₹2,500.00" in preview and "filed under" in preview
    assert "no total elsewhere changes" in preview

# ------------------------------------------------- the archive, and the photo

def test_a_deleted_record_is_archived_before_the_row_goes(monkeypatch):
    from ledger import archive

    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})
    record = make(note="old college loan", attachment="sheet:ab12")
    clearance.remove(record)

    kept = fake.book[archive.WORKSHEET].appended
    assert len(kept) == 1, kept
    stored = archive.Deletion.from_row(dict(zip(archive.COLUMNS, kept[0])))
    assert stored.kind == archive.CLEARANCE
    assert stored.source_row == 4
    assert stored.data == record.to_row(), "the row must be kept exactly as it was"
    assert "under VIHAR" in stored.summary
    assert fake.deleted == [4]


def test_a_failed_archive_stops_the_deletion(monkeypatch):
    """The whole bargain of the archive: when both cannot happen, the row
    stays. A notice that fails costs a message; this would cost the record."""
    from ledger import archive

    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})

    def refuse(*a, **kw):
        raise RuntimeError("the deleted tab is unreachable")

    monkeypatch.setattr(archive, "record", refuse)
    with pytest.raises(RuntimeError, match="unreachable"):
        clearance.remove(make())
    assert fake.deleted == [], "the row must still be on the sheet"


def test_an_archived_clearance_rebuilds_into_the_same_row(monkeypatch):
    """A restore is not an approximation — it goes back through `from_row`."""
    from ledger import archive

    fake = wire(monkeypatch, {4: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR"]})
    original = make(note="cash, at home", attachment="sheet:ab12")
    clearance.remove(original)

    kept = archive.Deletion.from_row(
        dict(zip(archive.COLUMNS, fake.book[archive.WORKSHEET].appended[0])))
    back = archive.rebuild(kept)
    assert back.to_row() == original.to_row()
    assert back.attachment == "sheet:ab12" and back.note == "cash, at home"
    assert back.amount_minor == original.amount_minor


def test_the_attachment_survives_the_sheet(monkeypatch):
    wire(monkeypatch, {})
    clearance.add(make(row=None, attachment="sheet:ab12"))
    rows, problems = clearance.load()
    assert problems == [] and rows[0].attachment == "sheet:ab12"


def test_a_row_written_before_the_attachment_column_existed_still_reads(monkeypatch):
    """`attachment` was appended last for exactly this: an older seven-cell row
    reads as having none, rather than shifting `note` and `source` along."""
    wire(monkeypatch, {2: ["2026-03-01", "RAVI", "VIHAR", "2500.00", "INR",
                           "old loan", "manual"]})
    rows, problems = clearance.load()
    assert problems == []
    assert rows[0].attachment == "" and rows[0].note == "old loan"
    assert rows[0].source == "manual"

def test_the_filed_under_filter_narrows_to_one_name(monkeypatch):
    app = on_screen(monkeypatch)
    assert app.selectbox(key="cl_f_under").options == [
        "Everyone", "ammu", "CHAITU", "VIHAR"
    ]
    app.selectbox(key="cl_f_under").select("CHAITU").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert [e.label for e in app.expander] == ["CHAITU — ₹500.00 · 1 record"]


def test_the_who_gave_it_filter_narrows_to_one_payer(monkeypatch):
    app = on_screen(monkeypatch)
    assert app.selectbox(key="cl_f_payer").options == ["Anyone", "AMMA", "RAVI"]
    app.selectbox(key="cl_f_payer").select("AMMA").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    # AMMA gave only the one, so VIHAR's group drops to it alone.
    assert [e.label for e in app.expander] == ["VIHAR — ₹1,500.00 · 1 record"]
    assert "From: AMMA" in [c.value for c in app.caption]


def test_the_filters_only_offer_names_in_the_currency_on_screen(monkeypatch):
    """A name that would empty the page the moment it is picked is not a
    filter, it is a trap."""
    app = on_screen(monkeypatch)
    app.radio(key="cl_currency").set_value("USD").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert app.selectbox(key="cl_f_under").options == ["Everyone", "VIHAR"]


def test_the_search_box_looks_in_the_note_and_the_names(monkeypatch):
    app = on_screen(monkeypatch)
    app.text_input(key="cl_search").set_value("old cash").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert [e.label for e in app.expander] == ["VIHAR — ₹2,500.00 · 1 record"]


def test_filters_that_match_nothing_say_so_rather_than_showing_an_empty_page(monkeypatch):
    app = on_screen(monkeypatch)
    app.text_input(key="cl_search").set_value("nothing like this").run()
    assert not app.exception, [str(e.message) for e in app.exception]
    assert any("Nothing matches" in i.value for i in app.info)
    assert not app.expander


def test_a_stored_photo_is_offered_on_its_row_and_fetched_only_when_asked(monkeypatch):
    """The file is reassembled from the sheet on click, never on page load —
    a list of photos fetched eagerly is a page that crawls."""
    app = on_screen(monkeypatch)
    assert "📎 View / download" in [b.label for b in app.button]
    assert app.button(key="cl_att_2") is not None


def test_the_form_offers_a_photo_and_a_link(monkeypatch):
    app = on_screen(monkeypatch, rows=[])
    assert app.text_input(key="cl_link") is not None
    labels = " ".join(str(w.label) for w in app.get("file_uploader"))
    assert "Photo or receipt" in labels, labels


def test_every_row_offers_an_edit(monkeypatch):
    """The dialog itself cannot be opened from a test — `st.dialog` needs a
    real click — so what is checked here is that the control is on every row."""
    app = on_screen(monkeypatch)
    assert app.button(key="cl_edit_2") is not None
    assert sum(1 for b in app.button if b.label == "Edit") == 4  # the INR rows
