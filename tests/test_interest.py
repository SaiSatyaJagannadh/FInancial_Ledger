"""Interest charges.

The rule that matters most is a negative one: nothing here may reach the
lending ledger's totals. The rest is the suggestion maths and the same
money-never-through-a-float discipline as everywhere else.
"""

from __future__ import annotations

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
