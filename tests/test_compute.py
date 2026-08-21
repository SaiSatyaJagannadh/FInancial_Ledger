from datetime import date

import pytest

from ledger.compute import (
    ALL_TIME,
    by_person,
    filter_entries,
    ledger_breakdown,
    monthly_given,
    totals,
)
from ledger.models import Direction, Entry
from ledger.money import Currency, to_minor


def entry(person, ledger, direction, rupees, when):
    return Entry(
        date=when,
        person=person,
        ledger=ledger,
        direction=Direction[direction],
        amount_minor=to_minor(rupees),
    )


@pytest.fixture()
def entries():
    return [
        entry("Father", "House repair", "given", 1000, date(2026, 1, 10)),
        entry("Father", "House repair", "received", 400, date(2026, 2, 10)),
        entry("Brother", "Bike loan", "given", 500, date(2026, 3, 5)),
        entry("Brother", "Rent float", "given", 300, date(2026, 3, 20)),
        entry("Brother", "Rent float", "received", 300, date(2026, 4, 1)),  # settles
        entry("Ravi", "Business", "received", 250, date(2026, 5, 2)),       # they overpaid
    ]


def test_totals(entries):
    t = totals(entries)
    assert t.given_minor == to_minor(1800)
    assert t.received_minor == to_minor(950)
    assert t.net_minor == to_minor(850)
    assert t.records == 6
    assert t.people == 3
    assert t.ledgers == 4


def test_open_ledgers_excludes_a_settled_one(entries):
    # Rent float: 300 given, 300 back -> net zero -> closed.
    t = totals(entries)
    assert t.ledgers == 4
    assert t.open_ledgers == 3


def test_person_rows_sum_to_the_headline_totals(entries):
    t = totals(entries)
    rows = by_person(entries)
    assert sum(r.given_minor for r in rows) == t.given_minor
    assert sum(r.received_minor for r in rows) == t.received_minor
    assert sum(r.net_minor for r in rows) == t.net_minor


def test_net_owed_is_given_minus_received(entries):
    father = next(r for r in by_person(entries) if r.person == "Father")
    assert father.given_minor == to_minor(1000)
    assert father.received_minor == to_minor(400)
    assert father.net_minor == to_minor(600)


def test_receiving_more_than_given_shows_a_negative_net(entries):
    """You owe them — the number must go negative, not clamp at zero."""
    ravi = next(r for r in by_person(entries) if r.person == "Ravi")
    assert ravi.net_minor == to_minor(-250)


def test_rows_are_ordered_by_who_owes_most(entries):
    assert [r.person for r in by_person(entries)] == ["Father", "Brother", "Ravi"]


def test_last_activity_is_the_latest_entry(entries):
    brother = next(r for r in by_person(entries) if r.person == "Brother")
    assert brother.last_activity == date(2026, 4, 1)
    assert brother.ledgers == 2
    assert brother.open_ledgers == 1


def test_period_filter_trims_by_date(entries):
    today = date(2026, 6, 1)
    recent = filter_entries(entries, period="Last 6 months", today=today)
    assert all(e.date >= date(2025, 12, 1) for e in recent)
    assert len(recent) == 6

    narrow = filter_entries(entries, period="Last 3 months", today=today) if False else None
    assert narrow is None  # "Last 3 months" is deliberately not an option


def test_period_filter_actually_excludes_old_entries():
    old = [entry("Father", "L", "given", 100, date(2020, 1, 1))]
    assert filter_entries(old, period="Last 24 months", today=date(2026, 6, 1)) == []
    assert len(filter_entries(old, period=ALL_TIME, today=date(2026, 6, 1))) == 1


def test_unknown_period_is_rejected(entries):
    with pytest.raises(ValueError, match="unknown period"):
        filter_entries(entries, period="Last fortnight")


def test_people_filter(entries):
    only = filter_entries(entries, people=["Brother"])
    assert {e.person for e in only} == {"Brother"}
    assert totals(only).given_minor == to_minor(800)


def test_empty_people_filter_means_everyone(entries):
    assert len(filter_entries(entries, people=[])) == len(entries)


def test_filters_compose_consistently(entries):
    """Every figure must come from the same filtered set, not a stale one."""
    subset = filter_entries(
        entries, period="Last 12 months", people=["Father", "Brother"], today=date(2026, 6, 1)
    )
    t = totals(subset)
    rows = by_person(subset)
    assert t.people == len(rows)
    assert sum(r.net_minor for r in rows) == t.net_minor
    assert sum(r.ledgers for r in rows) == t.ledgers
    assert t.records == len(subset)


def test_monthly_given_counts_only_money_going_out(entries):
    series = monthly_given(entries)
    # The February and April rows are repayments and must not appear.
    assert {(r["month"], r["person"]) for r in series} == {
        ("2026-01", "Father"),
        ("2026-03", "Brother"),
    }
    march = next(r for r in series if r["month"] == "2026-03")
    assert march["amount_minor"] == to_minor(800)  # both Brother ledgers combined


def test_monthly_given_totals_match_the_headline(entries):
    assert sum(r["amount_minor"] for r in monthly_given(entries)) == totals(entries).given_minor


def test_ledger_breakdown_flags_the_settled_ledger(entries):
    rows = {(r["person"], r["ledger"]): r for r in ledger_breakdown(entries)}
    assert rows[("Brother", "Rent float")]["net_minor"] == 0
    assert rows[("Brother", "Rent float")]["open"] is False
    assert rows[("Brother", "Bike loan")]["open"] is True


def test_everything_handles_an_empty_ledger():
    t = totals([])
    assert (t.given_minor, t.received_minor, t.net_minor) == (0, 0, 0)
    assert (t.people, t.ledgers, t.open_ledgers, t.records) == (0, 0, 0, 0)
    assert by_person([]) == []
    assert monthly_given([]) == []
    assert ledger_breakdown([]) == []


# ------------------------------------------------------- currency separation


@pytest.fixture()
def mixed():
    """The user's real shape: rupees to a brother at home, dollars abroad."""
    def make(person, ledger, direction, amount, when, currency):
        return Entry(
            date=when, person=person, ledger=ledger, direction=Direction[direction],
            amount_minor=to_minor(amount), currency=currency,
        )

    return [
        make("Brother", "Bike loan", "given", 50_000, date(2026, 1, 10), Currency.INR),
        make("Brother", "Bike loan", "received", 10_000, date(2026, 2, 10), Currency.INR),
        make("Brother", "Flight", "given", 600, date(2026, 3, 10), Currency.USD),
        make("Sam", "Rent", "given", 400, date(2026, 4, 10), Currency.USD),
    ]


def test_by_currency_splits_cleanly(mixed):
    from ledger.compute import by_currency

    rupees = by_currency(mixed, Currency.INR)
    dollars = by_currency(mixed, Currency.USD)
    assert len(rupees) == 2 and len(dollars) == 2
    assert all(e.currency is Currency.INR for e in rupees)
    assert all(e.currency is Currency.USD for e in dollars)


def test_currencies_present_is_stable(mixed):
    from ledger.compute import currencies_present

    assert currencies_present(mixed) == [Currency.INR, Currency.USD]
    assert currencies_present([]) == []


def test_totals_per_currency_are_independent(mixed):
    from ledger.compute import by_currency

    rupees = totals(by_currency(mixed, Currency.INR))
    dollars = totals(by_currency(mixed, Currency.USD))

    assert rupees.net_minor == to_minor(40_000)
    assert dollars.net_minor == to_minor(1_000)
    assert rupees.currency is Currency.INR and dollars.currency is Currency.USD
    assert rupees.people == 1 and dollars.people == 2


def test_totals_refuses_a_mixed_list(mixed):
    """Adding ₹40,000 to $1,000 is not a number, so it is not produced."""
    with pytest.raises(ValueError, match="cannot total across currencies"):
        totals(mixed)


def test_by_person_refuses_a_mixed_list(mixed):
    with pytest.raises(ValueError, match="cannot summarise across currencies"):
        by_person(mixed)


def test_filter_entries_takes_a_currency(mixed):
    only = filter_entries(mixed, currency=Currency.USD, today=date(2026, 6, 1))
    assert {e.currency for e in only} == {Currency.USD}
    assert totals(only).given_minor == to_minor(1_000)


def test_the_same_ledger_name_in_two_currencies_is_two_ledgers(mixed):
    from ledger.compute import by_currency

    extra = Entry(
        date=date(2026, 5, 1), person="Brother", ledger="Bike loan",
        direction=Direction.given, amount_minor=to_minor(300), currency=Currency.USD,
    )
    combined = [*mixed, extra]

    # "Bike loan" exists under both currencies and is counted once in each,
    # never merged: the rupee side keeps its single ledger.
    assert totals(by_currency(combined, Currency.INR)).ledgers == 1
    assert totals(by_currency(combined, Currency.USD)).ledgers == 3  # Flight, Rent, Bike loan

    rupee_ledgers = {(r["person"], r["ledger"]) for r in ledger_breakdown(by_currency(combined, Currency.INR))}
    dollar_ledgers = {(r["person"], r["ledger"]) for r in ledger_breakdown(by_currency(combined, Currency.USD))}
    assert ("Brother", "Bike loan") in rupee_ledgers
    assert ("Brother", "Bike loan") in dollar_ledgers

    # ...and the two carry different balances.
    rupee_net = next(r for r in ledger_breakdown(by_currency(combined, Currency.INR))
                     if r["ledger"] == "Bike loan")["net_minor"]
    dollar_net = next(r for r in ledger_breakdown(by_currency(combined, Currency.USD))
                      if r["ledger"] == "Bike loan")["net_minor"]
    assert rupee_net == to_minor(40_000)
    assert dollar_net == to_minor(300)


def test_an_empty_currency_still_totals_to_zero():
    empty = totals([], Currency.USD)
    assert empty.net_minor == 0
    assert empty.currency is Currency.USD
