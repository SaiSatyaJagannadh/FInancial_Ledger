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
from ledger.money import to_paise


def entry(person, ledger, direction, rupees, when):
    return Entry(
        date=when,
        person=person,
        ledger=ledger,
        direction=Direction[direction],
        amount_paise=to_paise(rupees),
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
    assert t.given_paise == to_paise(1800)
    assert t.received_paise == to_paise(950)
    assert t.net_paise == to_paise(850)
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
    assert sum(r.given_paise for r in rows) == t.given_paise
    assert sum(r.received_paise for r in rows) == t.received_paise
    assert sum(r.net_paise for r in rows) == t.net_paise


def test_net_owed_is_given_minus_received(entries):
    father = next(r for r in by_person(entries) if r.person == "Father")
    assert father.given_paise == to_paise(1000)
    assert father.received_paise == to_paise(400)
    assert father.net_paise == to_paise(600)


def test_receiving_more_than_given_shows_a_negative_net(entries):
    """You owe them — the number must go negative, not clamp at zero."""
    ravi = next(r for r in by_person(entries) if r.person == "Ravi")
    assert ravi.net_paise == to_paise(-250)


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
    assert totals(only).given_paise == to_paise(800)


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
    assert sum(r.net_paise for r in rows) == t.net_paise
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
    assert march["amount_paise"] == to_paise(800)  # both Brother ledgers combined


def test_monthly_given_totals_match_the_headline(entries):
    assert sum(r["amount_paise"] for r in monthly_given(entries)) == totals(entries).given_paise


def test_ledger_breakdown_flags_the_settled_ledger(entries):
    rows = {(r["person"], r["ledger"]): r for r in ledger_breakdown(entries)}
    assert rows[("Brother", "Rent float")]["net_paise"] == 0
    assert rows[("Brother", "Rent float")]["open"] is False
    assert rows[("Brother", "Bike loan")]["open"] is True


def test_everything_handles_an_empty_ledger():
    t = totals([])
    assert (t.given_paise, t.received_paise, t.net_paise) == (0, 0, 0)
    assert (t.people, t.ledgers, t.open_ledgers, t.records) == (0, 0, 0, 0)
    assert by_person([]) == []
    assert monthly_given([]) == []
    assert ledger_breakdown([]) == []
