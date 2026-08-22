"""The opportunity-cost arithmetic, and the rules that keep it honest."""

from __future__ import annotations

from datetime import date

import pytest

from ledger.invest import FREQUENCIES, grow, what_if, _outstanding_by_start
from ledger.models import Direction, Entry
from ledger.money import Currency


def entry(day: date, minor: int, direction: Direction = Direction.given, person: str = "P") -> Entry:
    return Entry(
        date=day, person=person, ledger="L", direction=direction,
        amount_minor=minor, currency=Currency.INR,
    )


def test_quarterly_compounding_matches_the_textbook_factor():
    """₹1,00,000 at 10% compounded quarterly for a year is ₹1,10,381.29."""
    got = grow(10_000_000, rate_percent=10, since=date(2025, 1, 1), until=date(2026, 1, 1))
    assert got.value_minor == 11_038_129


def test_simple_interest_is_exactly_the_rate():
    got = grow(
        10_000_000, rate_percent=10, since=date(2025, 1, 1), until=date(2026, 1, 1),
        periods_per_year=0,
    )
    assert got.interest_minor == 1_000_000


@pytest.mark.parametrize("periods", sorted(set(FREQUENCIES.values())))
def test_no_elapsed_time_never_creates_money(periods):
    got = grow(
        123_456, rate_percent=12, since=date(2026, 1, 1), until=date(2026, 1, 1),
        periods_per_year=periods,
    )
    assert got.value_minor == 123_456
    assert got.interest_minor == 0


def test_a_future_dated_entry_does_not_shrink():
    """Negative elapsed time must not run the compounding backwards."""
    got = grow(1000, rate_percent=10, since=date(2026, 12, 1), until=date(2026, 1, 1))
    assert got.value_minor == 1000


def test_zero_rate_earns_nothing():
    assert grow(1000, rate_percent=0, since=date(2020, 1, 1)).interest_minor == 0


def test_more_frequent_compounding_earns_more():
    kwargs = dict(rate_percent=9, since=date(2020, 1, 1), until=date(2026, 1, 1))
    annual = grow(1_000_000, periods_per_year=1, **kwargs).value_minor
    quarterly = grow(1_000_000, periods_per_year=4, **kwargs).value_minor
    monthly = grow(1_000_000, periods_per_year=12, **kwargs).value_minor
    assert annual < quarterly < monthly


def test_a_settled_ledger_has_no_opportunity_cost():
    settled = [
        entry(date(2020, 1, 1), 5000),
        entry(date(2021, 1, 1), 5000, Direction.received),
    ]
    got = what_if(settled, rate_percent=10)
    assert got.principal_minor == 0
    assert got.interest_minor == 0


def test_repayments_retire_the_oldest_money_first():
    entries = [
        entry(date(2020, 1, 1), 1000),
        entry(date(2024, 1, 1), 2000),
        entry(date(2025, 1, 1), 1000, Direction.received),
    ]
    assert _outstanding_by_start(entries) == [(2000, date(2024, 1, 1))]


def test_partial_repayment_leaves_the_remainder_outstanding():
    entries = [
        entry(date(2020, 1, 1), 1000),
        entry(date(2025, 1, 1), 400, Direction.received),
    ]
    assert _outstanding_by_start(entries) == [(600, date(2020, 1, 1))]


def test_overpayment_does_not_go_negative():
    """Being repaid more than you lent leaves nothing outstanding, not a debt."""
    entries = [
        entry(date(2020, 1, 1), 1000),
        entry(date(2025, 1, 1), 2500, Direction.received),
    ]
    assert _outstanding_by_start(entries) == []
    assert what_if(entries, rate_percent=10).value_minor == 0


def test_each_tranche_compounds_from_its_own_date():
    """Old money must earn more than new money, not be averaged with it."""
    today = date(2026, 1, 1)
    old = what_if([entry(date(2016, 1, 1), 1000)], rate_percent=10, today=today)
    new = what_if([entry(date(2025, 1, 1), 1000)], rate_percent=10, today=today)
    assert old.interest_minor > new.interest_minor

    both = what_if(
        [entry(date(2016, 1, 1), 1000), entry(date(2025, 1, 1), 1000)],
        rate_percent=10, today=today,
    )
    assert both.interest_minor == old.interest_minor + new.interest_minor


def test_empty_ledger_is_all_zeroes():
    got = what_if([], rate_percent=8)
    assert (got.principal_minor, got.value_minor, got.interest_minor) == (0, 0, 0)
