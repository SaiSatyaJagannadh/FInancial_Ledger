"""Clearing a balance to zero by recording the repayment."""

from __future__ import annotations

from datetime import date

import pytest

from ledger.models import Direction, Entry
from ledger.money import Currency
from ledger.settle import balancing_entries, open_ledgers, outstanding


def entry(person, ledger, minor, direction=Direction.given, currency=Currency.INR):
    return Entry(date=date(2026, 1, 1), person=person, ledger=ledger,
                 direction=direction, amount_minor=minor, currency=currency)


ROWS = [
    entry("Vihar", "UK", 500_000),
    entry("Vihar", "UK", 200_000, Direction.received),
    entry("Vihar", "Home", 100_000),
    entry("Nanna", "Home", 50_000),
]


def test_only_ledgers_with_money_owed_are_listed():
    assert open_ledgers(ROWS, "Vihar", Currency.INR) == [("Home", 100_000), ("UK", 300_000)]


def test_outstanding_is_the_sum_of_open_ledgers():
    assert outstanding(ROWS, "Vihar", Currency.INR) == 400_000


def test_settling_lands_on_exactly_zero():
    """Not near zero — exactly. Integer minor units make that reachable."""
    made = balancing_entries(ROWS, "Vihar", Currency.INR, today=date(2026, 8, 22))
    after = ROWS + made
    assert outstanding(after, "Vihar", Currency.INR) == 0
    assert sum(e.signed_minor for e in after if e.person == "Vihar") == 0


def test_one_balancing_entry_per_open_ledger():
    made = balancing_entries(ROWS, "Vihar", Currency.INR)
    assert {e.ledger for e in made} == {"UK", "Home"}
    assert all(e.direction is Direction.received for e in made)


def test_nothing_is_deleted():
    """History is the point of a ledger; settling adds, never removes."""
    made = balancing_entries(ROWS, "Vihar", Currency.INR)
    after = ROWS + made
    assert len(after) == len(ROWS) + 2
    assert all(original in after for original in ROWS)


def test_other_people_are_untouched():
    after = ROWS + balancing_entries(ROWS, "Vihar", Currency.INR)
    assert outstanding(after, "Nanna", Currency.INR) == 50_000


def test_settling_twice_adds_nothing():
    after = ROWS + balancing_entries(ROWS, "Vihar", Currency.INR)
    assert balancing_entries(after, "Vihar", Currency.INR) == []


def test_a_ledger_where_you_owe_them_is_not_settleable():
    """Being repaid cannot clear a debt that runs the other way, and writing
    one would invent a payment that never happened."""
    owing = [entry("Ravi", "Loan", 100_000, Direction.received)]
    assert open_ledgers(owing, "Ravi", Currency.INR) == []
    assert balancing_entries(owing, "Ravi", Currency.INR) == []


def test_currencies_are_settled_separately():
    mixed = [
        entry("Sam", "Books", 100_000),
        entry("Sam", "Books", 4_000, currency=Currency.USD),
    ]
    made = balancing_entries(mixed, "Sam", Currency.INR)
    assert len(made) == 1 and made[0].currency is Currency.INR
    assert outstanding(mixed + made, "Sam", Currency.USD) == 4_000


def test_an_unknown_person_yields_nothing():
    assert balancing_entries(ROWS, "Nobody", Currency.INR) == []


def test_the_balancing_entry_is_marked_as_typed_in():
    from ledger.models import BY_HAND

    assert balancing_entries(ROWS, "Vihar", Currency.INR)[0].source == BY_HAND


def test_a_partially_repaid_ledger_settles_only_the_remainder():
    rows = [entry("A", "L", 1000), entry("A", "L", 400, Direction.received)]
    made = balancing_entries(rows, "A", Currency.INR)
    assert [e.amount_minor for e in made] == [600]
