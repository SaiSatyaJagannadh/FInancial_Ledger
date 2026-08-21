"""Deterministic sample data for demo mode.

The figures are pinned to a known reference dashboard: 32 entries, 3 people,
4 open ledgers, ₹6,76,800 given and ₹3,04,000 received. `build_demo_entries`
asserts those totals, so a change to the generator that shifts a number fails
loudly instead of quietly redrawing the demo.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from ledger.models import Direction, Entry
from ledger.money import to_paise

SEED = 20260821

#: (person, ledger, rupees given, rupees received, n given, n received, last activity)
PLAN = [
    ("Father", "House repair", 241_800, 52_900, 6, 3, date(2026, 1, 24)),
    ("Brother", "Bike loan", 168_000, 84_600, 5, 3, date(2026, 7, 24)),
    ("Brother", "Rent float", 101_800, 47_000, 3, 2, date(2026, 5, 12)),
    ("Ravi (friend)", "Business help", 165_200, 119_500, 6, 4, date(2026, 7, 26)),
]

NOTES_GIVEN = ["transfer", "cash", "UPI", "cheque", "NEFT", "handed over"]
NOTES_RECEIVED = ["part repayment", "UPI back", "cash returned", "settled part"]

WINDOW_START = date(2024, 12, 1)


def split_exact(total: int, parts: int, rng: random.Random) -> list[int]:
    """Split `total` into `parts` positive integers summing to exactly `total`.

    Weighted rather than even, so the demo chart has shape; the last part
    absorbs the rounding so the sum is exact by construction.
    """
    if parts < 1:
        raise ValueError("parts must be >= 1")
    if total < parts:
        raise ValueError("total too small to split into positive parts")

    weights = [rng.uniform(0.6, 1.8) for _ in range(parts)]
    scale = total / sum(weights)
    amounts = [max(1, int(w * scale)) for w in weights[:-1]]
    amounts.append(total - sum(amounts))

    # A greedy split can leave the tail non-positive; pull from the largest.
    while amounts[-1] < 1:
        donor = amounts.index(max(amounts))
        amounts[donor] -= 1
        amounts[-1] += 1
    return amounts


def _dates(count: int, last: date, rng: random.Random) -> list[date]:
    """`count` dates ending exactly on `last`, spread back over the window."""
    span = max((last - WINDOW_START).days, count)
    offsets = sorted(rng.sample(range(span), min(count - 1, span))) if count > 1 else []
    days = [WINDOW_START + timedelta(days=o) for o in offsets]
    return days + [last]


def build_demo_entries() -> list[Entry]:
    rng = random.Random(SEED)
    entries: list[Entry] = []

    for person, name, given, received, n_given, n_received, last in PLAN:
        given_dates = _dates(n_given, last, rng)
        for amount, when in zip(split_exact(given, n_given, rng), given_dates):
            entries.append(
                Entry(
                    date=when,
                    person=person,
                    ledger=name,
                    direction=Direction.given,
                    amount_paise=to_paise(amount),
                    note=rng.choice(NOTES_GIVEN),
                )
            )

        # Repayments land after the first outgoing, never before it.
        first_given = min(given_dates)
        span = max((last - first_given).days, n_received)
        received_dates = [
            first_given + timedelta(days=o)
            for o in sorted(rng.sample(range(1, span + 1), n_received))
        ]
        for amount, when in zip(split_exact(received, n_received, rng), received_dates):
            entries.append(
                Entry(
                    date=when,
                    person=person,
                    ledger=name,
                    direction=Direction.received,
                    amount_paise=to_paise(amount),
                    note=rng.choice(NOTES_RECEIVED),
                )
            )

    entries.sort(key=lambda e: (e.date, e.person, e.ledger))

    _assert_reference_totals(entries)
    return entries


def _assert_reference_totals(entries: list[Entry]) -> None:
    from ledger.compute import by_person, totals

    t = totals(entries)
    assert t.records == 32, f"expected 32 records, got {t.records}"
    assert t.given_paise == to_paise(676_800), t.given_paise
    assert t.received_paise == to_paise(304_000), t.received_paise
    assert t.net_paise == to_paise(372_800), t.net_paise
    assert t.people == 3 and t.ledgers == 4 and t.open_ledgers == 4

    expected = {
        "Father": (241_800, 52_900, date(2026, 1, 24), 1),
        "Brother": (269_800, 131_600, date(2026, 7, 24), 2),
        "Ravi (friend)": (165_200, 119_500, date(2026, 7, 26), 1),
    }
    for row in by_person(entries):
        given, received, last, ledgers = expected[row.person]
        assert row.given_paise == to_paise(given), (row.person, row.given_paise)
        assert row.received_paise == to_paise(received), (row.person, row.received_paise)
        assert row.last_activity == last, (row.person, row.last_activity)
        assert row.ledgers == ledgers, (row.person, row.ledgers)
