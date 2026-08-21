"""The demo data is a fixture the UI is judged against, so it is pinned."""

from datetime import date

from ledger.compute import by_currency, by_person, monthly_given, totals
from ledger.demo import build_demo_entries, split_exact
from ledger.money import Currency, to_minor
import random


def test_split_exact_sums_to_the_total():
    rng = random.Random(1)
    for total, parts in [(100, 1), (1000, 3), (241_800, 6), (7, 5)]:
        pieces = split_exact(total, parts, rng)
        assert len(pieces) == parts
        assert sum(pieces) == total
        assert all(p > 0 for p in pieces)


def test_demo_is_deterministic():
    a, b = build_demo_entries(), build_demo_entries()
    assert [e.to_row() for e in a] == [e.to_row() for e in b]


def test_demo_matches_the_reference_dashboard():
    """The rupee tab is pinned to the reference; dollars are extra, not mixed in."""
    t = totals(by_currency(build_demo_entries(), Currency.INR))
    assert t.records == 32
    assert t.people == 3
    assert t.ledgers == 4
    assert t.open_ledgers == 4
    assert t.given_minor == to_minor(676_800)
    assert t.received_minor == to_minor(304_000)
    assert t.net_minor == to_minor(372_800)


def test_demo_person_rows():
    rows = {r.person: r for r in by_person(by_currency(build_demo_entries(), Currency.INR))}
    assert rows["Father"].net_minor == to_minor(188_900)
    assert rows["Brother"].net_minor == to_minor(138_200)
    assert rows["Ravi (friend)"].net_minor == to_minor(45_700)
    assert rows["Brother"].ledgers == 2
    assert rows["Father"].last_activity == date(2026, 1, 24)
    assert rows["Ravi (friend)"].last_activity == date(2026, 7, 26)


def test_demo_repayments_never_precede_the_first_advance():
    from ledger.models import Direction

    entries = build_demo_entries()
    for key in {e.key for e in entries}:
        legs = [e for e in entries if e.key == key]
        first_given = min(e.date for e in legs if e.direction is Direction.given)
        assert all(
            e.date >= first_given for e in legs if e.direction is Direction.received
        ), f"{key} has a repayment before any money went out"


def test_demo_chart_series_covers_several_months():
    series = monthly_given(by_currency(build_demo_entries(), Currency.INR))
    assert len({r["month"] for r in series}) >= 8
    assert sum(r["amount_minor"] for r in series) == to_minor(676_800)


def test_demo_has_a_separate_dollar_ledger():
    dollars = by_currency(build_demo_entries(), Currency.USD)
    t = totals(dollars)
    assert t.records == 14
    assert t.given_minor == to_minor(7_350)
    assert t.received_minor == to_minor(2_450)
    assert t.net_minor == to_minor(4_900)
    assert t.currency is Currency.USD


def test_the_two_currencies_do_not_overlap():
    entries = build_demo_entries()
    rupees = by_currency(entries, Currency.INR)
    dollars = by_currency(entries, Currency.USD)
    assert len(rupees) + len(dollars) == len(entries)
    assert not ({id(e) for e in rupees} & {id(e) for e in dollars})


def test_the_same_person_can_appear_in_both_currencies():
    """Lending a brother rupees at home and dollars abroad is two arrangements."""
    entries = build_demo_entries()
    rupee_people = {r.person for r in by_person(by_currency(entries, Currency.INR))}
    dollar_people = {r.person for r in by_person(by_currency(entries, Currency.USD))}
    assert "Brother" in rupee_people & dollar_people
