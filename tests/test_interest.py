"""Interest charges.

The rule that matters most is a negative one: nothing here may reach the
lending ledger's totals. The rest is the suggestion maths and the same
money-never-through-a-float discipline as everywhere else.
"""

from __future__ import annotations

import inspect
import pathlib
from datetime import date

import pytest

from ledger import interest
from ledger.compute import totals
from ledger.models import Direction, Entry, EntryError
from ledger.money import Currency


def entry(minor, *, person="Chaitu", direction=Direction.given,
          currency=Currency.INR, when=date(2026, 1, 1)) -> Entry:
    return Entry(date=when, person=person, ledger="Loan", direction=direction,
                 amount_minor=minor, currency=currency, note="")


@pytest.fixture
def lent() -> list[Entry]:
    """₹50,000 out, ₹10,000 back — ₹40,000 still owed."""
    return [
        entry(50_000_00, when=date(2026, 1, 1)),
        entry(10_000_00, direction=Direction.received, when=date(2026, 2, 1)),
    ]


# ------------------------------------------------- interest is never a debt

def test_charges_are_not_ledger_entries(lent):
    """If a Charge could be summed into the ledger, someone eventually would."""
    charge = interest.Charge(date=date(2026, 3, 1), person="Chaitu",
                             amount_minor=800_00)
    assert not isinstance(charge, Entry)
    assert totals(lent, Currency.INR).net_minor == 40_000_00


def test_the_interest_tab_is_not_the_entries_tab():
    from ledger import store

    assert interest.WORKSHEET != store._secrets.__module__  # sanity
    assert interest.WORKSHEET == "interest"
    assert "amount" in interest.COLUMNS and "person" in interest.COLUMNS


# ------------------------------------------------------------- the suggestion

def test_interest_is_charged_on_what_is_still_owed_not_what_was_lent(lent):
    """2% of the ₹40,000 outstanding, not of the ₹50,000 first handed over."""
    assert interest.suggest(lent, "Chaitu", rate_percent=2.0,
                            on=date(2026, 3, 1)) == 800_00


def test_a_settled_person_is_charged_nothing(lent):
    settled = lent + [entry(40_000_00, direction=Direction.received,
                            when=date(2026, 3, 1))]
    assert interest.suggest(settled, "Chaitu", rate_percent=2.0,
                            on=date(2026, 4, 1)) == 0


def test_someone_who_owes_nothing_is_never_charged_a_negative(lent):
    overpaid = lent + [entry(90_000_00, direction=Direction.received,
                             when=date(2026, 3, 1))]
    assert interest.suggest(overpaid, "Chaitu", rate_percent=2.0,
                            on=date(2026, 4, 1)) == 0


def test_an_unknown_person_is_charged_nothing(lent):
    assert interest.suggest(lent, "Nobody", rate_percent=2.0) == 0


def test_a_zero_rate_suggests_zero(lent):
    assert interest.suggest(lent, "Chaitu", rate_percent=0.0,
                            on=date(2026, 3, 1)) == 0


def test_entries_after_the_charge_date_do_not_count(lent):
    """Charging for February must not know about a March repayment."""
    later = lent + [entry(40_000_00, direction=Direction.received,
                          when=date(2026, 6, 1))]
    assert interest.suggest(later, "Chaitu", rate_percent=2.0,
                            on=date(2026, 3, 1)) == 800_00


def test_the_other_currency_is_not_charged(lent):
    mixed = lent + [entry(1_000_00, currency=Currency.USD)]
    assert interest.suggest(mixed, "Chaitu", rate_percent=2.0,
                            currency=Currency.USD, on=date(2026, 3, 1)) == 20_00


# ----------------------------------------------------------------- the record

def test_a_charge_survives_the_sheet_round_trip():
    charge = interest.Charge(date=date(2026, 3, 1), person="Chaitu",
                             amount_minor=800_00, rate_percent=2.0, note="March")
    rebuilt = interest.Charge.from_row(dict(zip(interest.COLUMNS, charge.to_row())))
    assert rebuilt.amount_minor == 800_00
    assert rebuilt.person == "Chaitu"
    assert rebuilt.date == date(2026, 3, 1)
    assert rebuilt.rate_percent == 2.0


def test_the_amount_never_goes_through_a_float():
    row = interest.Charge(date=date(2026, 3, 1), person="X",
                          amount_minor=1_234_56).to_row()
    assert row[2] == "1234.56"


def test_a_zero_charge_is_refused():
    with pytest.raises(EntryError):
        interest.Charge(date=date(2026, 1, 1), person="X", amount_minor=0)


def test_a_charge_needs_a_person():
    with pytest.raises(EntryError):
        interest.Charge(date=date(2026, 1, 1), person="  ", amount_minor=100)


def test_one_charge_per_person_per_month():
    """A second row for the same month is nearly always a double click."""
    charge = interest.Charge(date=date(2026, 3, 1), person="Chaitu",
                             amount_minor=800_00)
    assert interest.already_charged([charge], "Chaitu", date(2026, 3, 28)) is charge
    assert interest.already_charged([charge], "Chaitu", date(2026, 4, 1)) is None
    assert interest.already_charged([charge], "Sirisha", date(2026, 3, 1)) is None


def test_currencies_are_totalled_apart():
    charges = [
        interest.Charge(date=date(2026, 3, 1), person="Chaitu", amount_minor=800_00),
        interest.Charge(date=date(2026, 3, 1), person="Sam", amount_minor=50_00,
                        currency=Currency.USD),
    ]
    assert interest.totals(charges, Currency.INR) == 800_00
    assert interest.totals(charges, Currency.USD) == 50_00


def test_per_person_puts_the_largest_first():
    charges = [
        interest.Charge(date=date(2026, 3, 1), person="Chaitu", amount_minor=200_00),
        interest.Charge(date=date(2026, 3, 1), person="Sirisha", amount_minor=900_00),
        interest.Charge(date=date(2026, 4, 1), person="Chaitu", amount_minor=200_00),
    ]
    rows = interest.by_person(charges, Currency.INR)
    assert rows[0]["person"] == "Sirisha"
    assert rows[1]["person"] == "Chaitu" and rows[1]["months"] == 2
    assert rows[1]["total_minor"] == 400_00


def test_by_month_is_ordered_oldest_first():
    charges = [
        interest.Charge(date=date(2026, 4, 1), person="A", amount_minor=100),
        interest.Charge(date=date(2026, 2, 1), person="A", amount_minor=100),
    ]
    assert [r["month"] for r in interest.by_month(charges, Currency.INR)] == [
        "2026-02", "2026-04"
    ]


def test_a_charge_is_filed_under_the_month_it_is_for():
    charge = interest.Charge(date=interest.month_start(date(2026, 3, 27)),
                             person="X", amount_minor=100)
    assert charge.month == "2026-03"
    assert charge.month_label == "Mar 2026"


# ------------------------------------------------- one figure per month

class TestSettingAMonth:
    """The page is a grid you type over, so saving twice must *change* a month
    rather than append a second row that silently doubles it.

    A fake sheet stands in for the workbook: what matters is which of
    add/replace/remove gets called, not that gspread can be reached.
    """

    def store(self, monkeypatch, existing: list[interest.Charge]):
        calls = {"add": [], "replace": [], "remove": []}
        monkeypatch.setattr(interest, "load", lambda *_a, **_kw: (list(existing), []))
        monkeypatch.setattr(interest, "add",
                            lambda c, *_a, **_kw: calls["add"].append(c))
        monkeypatch.setattr(interest, "replace_row",
                            lambda o, n, *_a, **_kw: calls["replace"].append((o, n)))
        monkeypatch.setattr(interest, "remove",
                            lambda c, *_a, **_kw: calls["remove"].append(c))
        return calls

    def charge(self, minor: int, person="Chaitu", month=date(2026, 8, 1)):
        return interest.Charge(date=month, person=person, amount_minor=minor, row=7)

    def test_a_new_figure_is_added(self, monkeypatch):
        calls = self.store(monkeypatch, [])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 35_000_00,
                                      secrets={}) == "added"
        assert len(calls["add"]) == 1 and not calls["replace"]
        assert calls["add"][0].amount_minor == 35_000_00

    def test_typing_over_a_month_replaces_it_never_appends(self, monkeypatch):
        """The bug this exists to prevent: August charged twice."""
        calls = self.store(monkeypatch, [self.charge(30_000_00)])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 35_000_00,
                                      secrets={}) == "updated"
        assert calls["add"] == [], "a second row for August would double the month"
        assert len(calls["replace"]) == 1
        assert calls["replace"][0][1].amount_minor == 35_000_00

    def test_setting_it_to_zero_removes_the_charge(self, monkeypatch):
        """A row saying "no interest" and no row at all mean the same thing."""
        calls = self.store(monkeypatch, [self.charge(30_000_00)])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 0,
                                      secrets={}) == "removed"
        assert len(calls["remove"]) == 1 and not calls["add"]

    def test_zero_on_a_month_with_nothing_writes_nothing(self, monkeypatch):
        calls = self.store(monkeypatch, [])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 0,
                                      secrets={}) == "unchanged"
        assert not any(calls.values())

    def test_saving_the_same_figure_again_touches_nothing(self, monkeypatch):
        calls = self.store(monkeypatch, [self.charge(30_000_00)])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 30_000_00,
                                      secrets={}) == "unchanged"
        assert not any(calls.values())

    def test_a_different_month_does_not_disturb_this_one(self, monkeypatch):
        calls = self.store(monkeypatch, [self.charge(30_000_00, month=date(2026, 7, 1))])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 35_000_00,
                                      secrets={}) == "added"
        assert len(calls["add"]) == 1 and not calls["replace"]

    def test_a_different_person_does_not_disturb_this_one(self, monkeypatch):
        calls = self.store(monkeypatch, [self.charge(30_000_00, person="Sirisha")])
        assert interest.set_for_month("Chaitu", date(2026, 8, 15), 35_000_00,
                                      secrets={}) == "added"
        assert len(calls["add"]) == 1

    def test_any_day_of_the_month_files_under_the_first(self, monkeypatch):
        calls = self.store(monkeypatch, [])
        interest.set_for_month("Chaitu", date(2026, 8, 27), 100_00, secrets={})
        assert calls["add"][0].date == date(2026, 8, 1)
        assert calls["add"][0].month == "2026-08"


class TestTheMonthPicker:
    def test_it_offers_the_recent_months_newest_first(self):
        months = interest.months_back(4, today=date(2026, 8, 23))
        assert [f"{m:%b %Y}" for m in months] == [
            "Aug 2026", "Jul 2026", "Jun 2026", "May 2026"
        ]

    def test_it_steps_back_over_a_year_boundary(self):
        months = interest.months_back(3, today=date(2026, 1, 5))
        assert [f"{m:%b %Y}" for m in months] == ["Jan 2026", "Dec 2025", "Nov 2025"]

    def test_every_option_is_a_first_of_the_month(self):
        assert all(m.day == 1 for m in interest.months_back(24))


class TestReadingOneMonth:
    CHARGES = [
        interest.Charge(date=date(2026, 8, 1), person="Chaitu", amount_minor=35_000_00),
        interest.Charge(date=date(2026, 7, 1), person="Chaitu", amount_minor=30_000_00),
        interest.Charge(date=date(2026, 8, 1), person="Sirisha", amount_minor=5_000_00),
        interest.Charge(date=date(2026, 8, 1), person="Sam", amount_minor=50_00,
                        currency=Currency.USD),
    ]

    def test_it_returns_that_month_only(self):
        found = interest.for_month(self.CHARGES, date(2026, 8, 20))
        assert set(found) == {"Chaitu", "Sirisha"}
        assert found["Chaitu"].amount_minor == 35_000_00

    def test_any_day_in_the_month_finds_it(self):
        assert interest.for_month(self.CHARGES, date(2026, 8, 1)).keys() == \
               interest.for_month(self.CHARGES, date(2026, 8, 31)).keys()

    def test_a_month_with_nothing_is_empty_not_an_error(self):
        assert interest.for_month(self.CHARGES, date(2026, 6, 1)) == {}

    def test_the_other_currency_is_kept_apart(self):
        assert "Sam" not in interest.for_month(self.CHARGES, date(2026, 8, 1))
        assert "Sam" in interest.for_month(self.CHARGES, date(2026, 8, 1), Currency.USD)


class TestTheLedgerIsOnlyEverTouchedOnPurpose:
    """Interest stays out of the ledger unless somebody chooses otherwise.

    That rule changed shape: the page now has one deliberate, opt-in path for
    when the interest money is handed on to somebody else — at that point it
    has stopped being interest and become a loan to them. What must stay true
    is that the path is *narrow*: a single named helper, never a bare Entry
    built somewhere in a view, and never reachable without a person choosing.
    """

    SOURCE = (
        pathlib.Path(__file__).resolve().parent.parent / "views" / "interest.py"
    ).read_text()

    @pytest.mark.parametrize("forbidden", [
        "store.delete",       # nothing here removes a ledger row
        "Entry(",             # building one by hand, outside the helper
        "transfer_entries",   # the group-move path does not belong here
    ])
    def test_the_page_has_no_other_way_into_the_ledger(self, forbidden):
        assert forbidden not in self.SOURCE, (
            f"views/interest.py contains {forbidden!r} — the only route to the "
            "ledger is interest.ledger_entry()"
        )

    def test_the_view_never_writes_to_the_ledger_itself(self):
        """Both routes — the add form and the edit dialog — go through
        `interest.sync_ledger_entry`, which decides append against correct and
        which row is actually ours. A bare `store.append` in the view is how
        the edit dialog used to duplicate a loan on a second tick."""
        for forbidden in ("store.append(", "store.update("):
            assert forbidden not in self.SOURCE, (
                f"views/interest.py writes to the ledger directly with "
                f"{forbidden!r} — it must call interest.sync_ledger_entry()"
            )

    def test_it_looks_for_a_standing_entry_before_writing_one(self):
        """The guard against the duplicate that reached the real sheet, and
        against overwriting a loan this page did not write."""
        assert "interest.sync_ledger_entry(" in self.SOURCE
        assert "find_ledger_entry" in inspect.getsource(interest.sync_ledger_entry)

    def test_every_route_to_the_ledger_also_marks_the_charge_as_moved(self):
        """`moved_to` is what stops the money being counted twice.

        `recorded_total` sums only charges with an empty `moved_to`, because a
        charge handed on is a loan now and the ledger is counting it. The edit
        dialog wrote the ledger row without setting it, so ₹15,000 sat in the
        interest total *and* in somebody's balance at the same time.
        """
        writes = self.SOURCE.count("interest.sync_ledger_entry(")
        marks = self.SOURCE.count("moved_to=taker")
        assert writes >= 2, "both the add form and the edit dialog should be here"
        assert marks >= writes, (
            f"{writes} ledger writes but only {marks} of them record moved_to — "
            "the difference is interest counted twice"
        )

    def test_the_edit_dialog_reuses_the_person_the_charge_went_to(self):
        """The standing row is found by a trail carrying the taker's name.

        Re-saving against whoever sorts first in the list builds a different
        trail, matches nothing, and appends a second loan under someone who
        never took the money.
        """
        assert "charge.moved_to" in self.SOURCE

    def test_it_says_on_screen_that_interest_stays_out_of_the_ledger(self):
        """A rule the reader cannot see is a rule they will not trust."""
        assert "ledger" in self.SOURCE and "Kept out of the ledger" in self.SOURCE

    def test_the_guard_would_actually_catch_something(self):
        assert len(self.SOURCE) > 500
        assert "interest.set_for_month" in self.SOURCE


class TestTheOptInLedgerEntry:
    """interest.ledger_entry() — the one bridge, in isolation."""

    def charge(self, minor=1_500_00, person="Chaitu"):
        return interest.Charge(date=date(2026, 8, 1), person=person,
                               amount_minor=minor, note="monthly")

    def test_it_becomes_a_loan_to_whoever_took_it(self):
        entry = interest.ledger_entry(self.charge(), "Sirisha", ledger="Side")
        assert entry.person == "Sirisha"
        assert entry.direction is Direction.given
        assert entry.amount_minor == 1_500_00
        assert entry.ledger == "Side"

    def test_it_is_a_real_entry_the_ledger_would_accept(self):
        entry = interest.ledger_entry(self.charge(), "Sirisha", ledger="Side")
        assert isinstance(entry, Entry)
        assert entry.currency is Currency.INR

    def test_it_says_where_the_money_came_from(self):
        """Six months on, "1,500 to Sirisha" needs to explain itself."""
        entry = interest.ledger_entry(self.charge(), "Sirisha", ledger="Side")
        assert "Chaitu interest for Aug 2026" in entry.note
        assert "taken by Sirisha" in entry.note

    def test_a_typed_reason_is_added_to_the_trail_never_instead_of_it(self):
        """The rows that prompted this said only "given to vihar but used to
        pay proxy service" — no Narayana, no interest, no month."""
        entry = interest.ledger_entry(self.charge(), "Sirisha", ledger="Side",
                                      note="he needed it for fees")
        assert "he needed it for fees" in entry.note
        assert "Chaitu interest for Aug 2026" in entry.note
        assert "taken by Sirisha" in entry.note

    def test_the_trail_names_all_four_things_that_matter(self):
        """Whose interest, which month, who took it, and why."""
        note = interest.trail_note(self.charge(), "Sirisha", "for fees")
        for part in ("Chaitu", "Aug 2026", "Sirisha", "for fees"):
            assert part in note, part

    def test_the_trail_comes_first_so_it_survives_a_truncated_column(self):
        note = interest.trail_note(self.charge(), "Sirisha", "for fees")
        assert note.startswith("Chaitu interest for Aug 2026")

    def test_no_dangling_separator_when_no_reason_was_typed(self):
        for blank in ("", "   ", None):
            assert not interest.trail_note(self.charge(), "Sirisha", blank).endswith("—")

    def test_it_refuses_without_somebody_to_charge(self):
        with pytest.raises(EntryError):
            interest.ledger_entry(self.charge(), "  ", ledger="Side")

    def test_it_only_builds_the_entry_and_never_writes_it(self):
        """Writing stays the caller's decision, so it cannot happen by accident."""
        import inspect

        body = inspect.getsource(interest.ledger_entry)
        assert "append" not in body and "_sheet" not in body


class TestDueAndGiven:
    def test_a_row_written_before_the_column_existed_reads_as_due(self):
        """The honest reading of an interest row with no status is "owed"."""
        assert interest.parse_kind("") is interest.Kind.due
        assert interest.parse_kind(None) is interest.Kind.due

    @pytest.mark.parametrize("word,expected", [
        ("due", interest.Kind.due), ("owed", interest.Kind.due),
        ("pending", interest.Kind.due), ("GIVEN", interest.Kind.given),
        ("paid", interest.Kind.given), ("received", interest.Kind.given),
    ])
    def test_it_reads_the_words_people_type(self, word, expected):
        assert interest.parse_kind(word) is expected

    def test_a_word_it_cannot_read_is_refused(self):
        with pytest.raises(EntryError):
            interest.parse_kind("maybe")

    def test_the_kind_survives_the_sheet_round_trip(self):
        charge = interest.Charge(date=date(2026, 8, 1), person="X",
                                 amount_minor=100, kind=interest.Kind.given,
                                 attachment="sheet:abc")
        back = interest.Charge.from_row(dict(zip(interest.COLUMNS, charge.to_row())))
        assert back.kind is interest.Kind.given
        assert back.attachment == "sheet:abc"

    def test_due_and_given_are_totalled_apart(self):
        charges = [
            interest.Charge(date=date(2026, 8, 1), person="A", amount_minor=1_000_00),
            interest.Charge(date=date(2026, 8, 1), person="B", amount_minor=400_00,
                            kind=interest.Kind.given),
        ]
        split = interest.split_by_kind(charges, Currency.INR)
        assert split[interest.Kind.due] == 1_000_00
        assert split[interest.Kind.given] == 400_00
        assert sum(split.values()) == interest.totals(charges, Currency.INR)


class TestAddingAColumnDoesNotShiftExistingRows:
    """A tab written before a column existed keeps its old header, and every
    row in it is *positional*.

    This is a scar. `kind` and `attachment` were first added in the middle of
    COLUMNS, and against the real sheet that put "due" under the heading
    `rate_percent`, widened the tab with blank headings, and then made gspread
    refuse to read it at all — two blank headings count as duplicates. Two rows
    of real data went unreadable.
    """

    #: The header the tab was first created with. Every row written under it is
    #: positional, so these seven must stay first, in this order, for ever.
    ORIGINAL = [
        "date", "person", "amount", "currency", "rate_percent", "note", "source",
    ]

    def test_the_original_columns_are_still_first_and_in_order(self):
        """The rule that makes an old row still parse correctly. Anything added
        since goes after them, where an absent value reads as a default."""
        assert interest.COLUMNS[:len(self.ORIGINAL)] == self.ORIGINAL

    def test_everything_added_since_came_after_them(self):
        assert set(interest.COLUMNS[len(self.ORIGINAL):]) == {
            "kind", "attachment", "moved_to"
        }

    def test_a_row_written_before_the_columns_existed_still_reads(self):
        """Exactly the shape sitting in the sheet today."""
        old = ["2026-08-01", "Narayana", "15000", "INR", "0",
               "for 3 lakh interest", "manual"]
        charge = interest.Charge.from_row(dict(zip(interest.COLUMNS, old)))
        assert charge.person == "Narayana"
        assert charge.amount_minor == 15_000_00
        assert charge.note == "for 3 lakh interest"
        assert charge.kind is interest.Kind.due       # the honest default
        assert charge.attachment == ""

    def test_a_short_row_does_not_read_a_value_out_of_the_wrong_column(self):
        """The failure that made "due" arrive as a rate: values shifting left."""
        old = ["2026-08-01", "Sriram", "1000", "INR", "0", "", "manual"]
        charge = interest.Charge.from_row(dict(zip(interest.COLUMNS, old)))
        assert charge.rate_percent == 0.0
        assert charge.source == "manual"
        assert charge.note == ""

    def test_to_row_and_columns_stay_the_same_length(self):
        """If they drift, every row after the drift is written misaligned."""
        charge = interest.Charge(date=date(2026, 8, 1), person="X",
                                 amount_minor=100)
        assert len(charge.to_row()) == len(interest.COLUMNS)

    def test_a_full_row_round_trips_in_order(self):
        charge = interest.Charge(
            date=date(2026, 8, 1), person="X", amount_minor=1_500_00,
            currency=Currency.INR, kind=interest.Kind.given, rate_percent=2.0,
            note="n", attachment="sheet:abc", source="manual",
        )
        back = interest.Charge.from_row(dict(zip(interest.COLUMNS, charge.to_row())))
        for attribute in ("person", "amount_minor", "currency", "kind",
                          "rate_percent", "note", "attachment", "source"):
            assert getattr(back, attribute) == getattr(charge, attribute), attribute

    def test_reading_by_position_recovers_a_tab_with_a_broken_header(self):
        """The fallback: the header is only a label, the values are the data."""
        class Sheet:
            def get_all_values(self):
                return [
                    ["date", "person", "amount", "currency", "", "", "", "", ""],
                    ["2026-08-01", "Narayana", "15000", "INR", "0", "note", "manual"],
                ]

        rows = interest._records_by_position(Sheet())
        assert len(rows) == 1
        charge = interest.Charge.from_row(rows[0])
        assert charge.person == "Narayana" and charge.amount_minor == 15_000_00


class TestSavingTwiceDoesNotLendTwice:
    """The bug that reached the real sheet.

    An interest charge is upserted — save it twice and there is still one row.
    But the ledger write was a bare append, so the second save put a second
    identical loan in the ledger. Vihar was shown owing ₹15,000 more than he
    did, from two rows that were byte-for-byte the same.
    """

    def charge(self, minor=15_000_00):
        return interest.Charge(date=date(2026, 7, 1), person="Narayana",
                               amount_minor=minor, kind=interest.Kind.given,
                               note="given to vihar but used to pay proxy service")

    def written(self, charge, person="Vihar", ledger="VIHAR"):
        return interest.ledger_entry(charge, person, ledger=ledger)

    def test_the_second_save_finds_the_first_entry(self):
        first = self.written(self.charge())
        found = interest.find_ledger_entry([first], self.charge(), "Vihar", "VIHAR")
        assert found is first, "a second save would have appended a duplicate"

    def test_it_matches_even_when_the_note_was_reworded(self):
        """A person is free to change the wording between saves."""
        first = interest.ledger_entry(self.charge(), "Vihar", ledger="VIHAR",
                                      note="one wording")
        found = interest.find_ledger_entry([first], self.charge(), "Vihar", "VIHAR")
        assert found is first

    def test_a_different_person_is_not_a_match(self):
        first = self.written(self.charge(), person="Sriram")
        assert interest.find_ledger_entry([first], self.charge(), "Vihar",
                                          "VIHAR") is None

    def test_a_different_month_is_not_a_match(self):
        first = self.written(self.charge())
        august = interest.Charge(date=date(2026, 8, 1), person="Narayana",
                                 amount_minor=15_000_00)
        assert interest.find_ledger_entry([first], august, "Vihar", "VIHAR") is None

    def test_a_different_ledger_is_not_a_match(self):
        first = self.written(self.charge(), ledger="VIHAR")
        assert interest.find_ledger_entry([first], self.charge(), "Vihar",
                                          "Other") is None

    def test_an_empty_ledger_finds_nothing(self):
        assert interest.find_ledger_entry([], self.charge(), "Vihar", "VIHAR") is None

    def test_a_changed_amount_is_still_found_so_it_can_be_corrected(self):
        """Found, not ignored: the standing row should be updated, not doubled."""
        first = self.written(self.charge(15_000_00))
        found = interest.find_ledger_entry(
            [first], self.charge(20_000_00), "Vihar", "VIHAR"
        )
        assert found is first
        assert found.amount_minor != 20_000_00


class TestMovedInterestIsCountedOnce:
    """Once a charge has been handed on, the ledger is counting it. Leaving it
    in the interest total as well counts the same money twice."""

    CHARGES = [
        interest.Charge(date=date(2026, 7, 1), person="Narayana",
                        amount_minor=15_000_00, kind=interest.Kind.given,
                        moved_to="Vihar"),
        interest.Charge(date=date(2026, 8, 1), person="Narayana",
                        amount_minor=15_000_00, kind=interest.Kind.due),
    ]

    def test_what_moved_is_left_out_of_the_interest_total(self):
        assert interest.recorded_total(self.CHARGES, Currency.INR) == 15_000_00

    def test_what_moved_is_reported_on_its_own(self):
        assert interest.moved_total(self.CHARGES, Currency.INR) == 15_000_00

    def test_the_two_add_back_up_to_the_gross(self):
        assert (
            interest.recorded_total(self.CHARGES, Currency.INR)
            + interest.moved_total(self.CHARGES, Currency.INR)
            == interest.totals(self.CHARGES, Currency.INR)
        )

    def test_a_moved_charge_is_left_out_of_due_and_given_too(self):
        split = interest.split_by_kind(self.CHARGES, Currency.INR)
        assert split[interest.Kind.given] == 0, "the moved one is counted in the ledger"
        assert split[interest.Kind.due] == 15_000_00

    def test_moved_to_survives_the_sheet_round_trip(self):
        charge = self.CHARGES[0]
        back = interest.Charge.from_row(dict(zip(interest.COLUMNS, charge.to_row())))
        assert back.moved_to == "Vihar"

    def test_a_row_written_before_the_column_existed_has_not_moved(self):
        old = ["2026-08-01", "Narayana", "15000", "INR", "0", "n", "manual"]
        assert interest.Charge.from_row(dict(zip(interest.COLUMNS, old))).moved_to == ""


class TestAnOrdinaryLoanIsNeverMistakenForTheInterestRow:
    """The bug this class exists for.

    `find_ledger_entry` used to match on shape alone — person, ledger,
    currency, date, direction. Money handed to Vihar on the first of the month
    is *also* a "given" row for Vihar on that date, so saving Narayana's
    interest a second time found that loan and `store.update` wrote the
    interest figure over it. The loan was gone: the row still said "given", but
    for the wrong amount, and nothing anywhere recorded the money actually lent.

    The trail in the note is what says a row came from a charge, so that is
    what is matched.
    """

    CHARGE = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                             amount_minor=15_000_00, kind=interest.Kind.given)

    def loan(self, minor=2_00_000_00, when=date(2026, 7, 1), note="cash at home"):
        """An entry typed by hand — same person, ledger, day and direction."""
        return Entry(date=when, person="Vihar", ledger="VIHAR",
                     direction=Direction.given, amount_minor=minor,
                     currency=Currency.INR, note=note, row=12)

    def test_a_hand_typed_loan_on_the_same_day_is_not_the_interest_row(self):
        assert interest.find_ledger_entry(
            [self.loan()], self.CHARGE, "Vihar", "VIHAR"
        ) is None, "this loan would have been overwritten with the interest"

    def test_a_loan_with_no_note_at_all_is_not_the_interest_row(self):
        assert interest.find_ledger_entry(
            [self.loan(note="")], self.CHARGE, "Vihar", "VIHAR"
        ) is None

    def test_a_note_that_merely_mentions_interest_is_not_enough(self):
        assert interest.find_ledger_entry(
            [self.loan(note="interest money, roughly")], self.CHARGE, "Vihar", "VIHAR"
        ) is None

    def test_the_row_this_bridge_wrote_is_still_found(self):
        written = interest.ledger_entry(self.CHARGE, "Vihar", ledger="VIHAR")
        assert interest.find_ledger_entry(
            [self.loan(), written], self.CHARGE, "Vihar", "VIHAR"
        ) is written

    def test_it_is_found_among_the_loan_whichever_order_they_come_in(self):
        written = interest.ledger_entry(self.CHARGE, "Vihar", ledger="VIHAR")
        assert interest.find_ledger_entry(
            [written, self.loan()], self.CHARGE, "Vihar", "VIHAR"
        ) is written


class TestSyncingTheOneLedgerRow:
    """`sync_ledger_entry` — the single door from interest to the ledger.

    Both the add form and the edit dialog come through it, so what it does to
    the sheet is worth pinning down: append when there is nothing standing,
    correct the row it wrote when the figure changed, and touch nothing at all
    otherwise.
    """

    CHARGE = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                             amount_minor=15_000_00, kind=interest.Kind.given)

    def sheet(self, monkeypatch):
        from ledger import store

        calls = {"append": [], "update": []}
        monkeypatch.setattr(store, "append",
                            lambda e, *_a, **_kw: calls["append"].append(e))
        monkeypatch.setattr(store, "update",
                            lambda o, n, *_a, **_kw: calls["update"].append((o, n)))
        return calls

    def loan(self):
        return Entry(date=date(2026, 7, 1), person="Vihar", ledger="VIHAR",
                     direction=Direction.given, amount_minor=2_00_000_00,
                     currency=Currency.INR, note="cash at home", row=12)

    def test_nothing_standing_means_one_new_row(self, monkeypatch):
        calls = self.sheet(monkeypatch)
        assert interest.sync_ledger_entry([], self.CHARGE, "Vihar", "VIHAR") == "added"
        assert len(calls["append"]) == 1 and not calls["update"]
        assert calls["append"][0].amount_minor == 15_000_00

    def test_saving_the_same_charge_twice_writes_nothing_the_second_time(self, monkeypatch):
        calls = self.sheet(monkeypatch)
        written = interest.ledger_entry(self.CHARGE, "Vihar", ledger="VIHAR")
        assert interest.sync_ledger_entry(
            [written], self.CHARGE, "Vihar", "VIHAR") == "unchanged"
        assert not calls["append"] and not calls["update"]

    def test_a_corrected_figure_updates_the_row_it_wrote(self, monkeypatch):
        calls = self.sheet(monkeypatch)
        written = interest.ledger_entry(self.CHARGE, "Vihar", ledger="VIHAR")
        bigger = interest.Charge(date=date(2026, 7, 1), person="Narayana",
                                 amount_minor=20_000_00, kind=interest.Kind.given)
        assert interest.sync_ledger_entry(
            [written], bigger, "Vihar", "VIHAR") == "updated"
        assert not calls["append"]
        was, now = calls["update"][0]
        assert was is written and now.amount_minor == 20_000_00

    def test_a_hand_typed_loan_is_appended_beside_not_written_over(self, monkeypatch):
        """The whole bug, end to end: the loan must survive the save."""
        calls = self.sheet(monkeypatch)
        assert interest.sync_ledger_entry(
            [self.loan()], self.CHARGE, "Vihar", "VIHAR") == "added"
        assert not calls["update"], "it overwrote a loan it did not write"
        assert len(calls["append"]) == 1
