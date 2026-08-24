"""Interest charges.

The rule that matters most is a negative one: nothing here may reach the
lending ledger's totals. The rest is the suggestion maths and the same
money-never-through-a-float discipline as everywhere else.
"""

from __future__ import annotations

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


class TestTheInterestPageCannotWriteToTheLedger:
    """The one rule this whole feature exists to keep.

    Checked against the page's source rather than its behaviour: a runtime
    test only covers the paths it happens to walk, and what matters here is
    that there is *no* path at all. If a future edit adds one, this fails.
    """

    SOURCE = (
        pathlib.Path(__file__).resolve().parent.parent / "views" / "interest.py"
    ).read_text()

    @pytest.mark.parametrize("forbidden", [
        "store.append",     # writes an entry
        "store.update",     # rewrites one
        "store.delete",     # removes one
        "Entry(",           # builds one
        "transfer_entries", # writes two
    ])
    def test_the_page_never_writes_a_ledger_row(self, forbidden):
        assert forbidden not in self.SOURCE, (
            f"views/interest.py contains {forbidden!r} — interest must never "
            "reach the lending ledger"
        )

    def test_it_says_so_on_screen_too(self):
        """A rule the reader cannot see is a rule they will not trust."""
        assert "added to the ledger" in self.SOURCE

    def test_the_guard_would_actually_catch_something(self):
        """Guard against the file being renamed and this silently passing."""
        assert len(self.SOURCE) > 500
        assert "interest.set_for_month" in self.SOURCE
