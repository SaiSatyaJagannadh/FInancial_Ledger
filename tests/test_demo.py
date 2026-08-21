"""The demo data is a fixture the UI is judged against, so it is pinned."""

from datetime import date

from ledger.compute import by_person, monthly_given, totals
from ledger.demo import build_demo_entries, split_exact
from ledger.money import to_paise
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
    t = totals(build_demo_entries())
    assert t.records == 32
    assert t.people == 3
    assert t.ledgers == 4
    assert t.open_ledgers == 4
    assert t.given_paise == to_paise(676_800)
    assert t.received_paise == to_paise(304_000)
    assert t.net_paise == to_paise(372_800)


def test_demo_person_rows():
    rows = {r.person: r for r in by_person(build_demo_entries())}
    assert rows["Father"].net_paise == to_paise(188_900)
    assert rows["Brother"].net_paise == to_paise(138_200)
    assert rows["Ravi (friend)"].net_paise == to_paise(45_700)
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
    series = monthly_given(build_demo_entries())
    assert len({r["month"] for r in series}) >= 8
    assert sum(r["amount_paise"] for r in series) == to_paise(676_800)
