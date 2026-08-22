"""Clearing a ledger: recording that what was owed has come back.

Settling writes a balancing entry rather than deleting the history. A khata is
a record of what happened, and "he paid me back" is a thing that happened —
erasing the loan would lose the fact that it ever existed, and with it any
answer to "how much has passed between us over the years".

The balancing entry is exactly the outstanding amount, so the net falls to
zero and the name drops off the who-owes-me list.
"""

from __future__ import annotations

from datetime import date

from ledger.models import BY_HAND, Direction, Entry
from ledger.money import Currency


def open_ledgers(
    entries: list[Entry], person: str, currency: Currency | None = None
) -> list[tuple[str, int]]:
    """Each of this person's ledgers that still has money owed, and how much.

    Only positive balances are returned. A ledger where you owe *them* is not
    something you can settle by being repaid, and quietly writing an entry for
    it would invent a payment that never happened.
    """
    balances: dict[str, int] = {}
    for entry in entries:
        if entry.person != person:
            continue
        if currency is not None and entry.currency is not currency:
            continue
        balances[entry.ledger] = balances.get(entry.ledger, 0) + entry.signed_minor
    return [(name, owed) for name, owed in sorted(balances.items()) if owed > 0]


def balancing_entries(
    entries: list[Entry],
    person: str,
    currency: Currency,
    *,
    today: date | None = None,
    note: str = "settled",
) -> list[Entry]:
    """The entries that would bring every open ledger of `person` to zero."""
    today = today or date.today()
    return [
        Entry(
            date=today,
            person=person,
            ledger=name,
            direction=Direction.received,
            amount_minor=owed,
            currency=currency,
            note=note,
            source=BY_HAND,
        )
        for name, owed in open_ledgers(entries, person, currency)
    ]


def outstanding(entries: list[Entry], person: str, currency: Currency) -> int:
    return sum(owed for _, owed in open_ledgers(entries, person, currency))


def demo() -> None:
    """Self-check: settling must land on exactly zero, and invent nothing."""

    def entry(person, ledger, minor, direction=Direction.given, currency=Currency.INR):
        return Entry(date=date(2026, 1, 1), person=person, ledger=ledger,
                     direction=direction, amount_minor=minor, currency=currency)

    rows = [
        entry("Ravi", "UK", 500_000),
        entry("Ravi", "UK", 200_000, Direction.received),
        entry("Ravi", "Home", 100_000),
        entry("Amma", "Home", 50_000),
    ]

    assert open_ledgers(rows, "Ravi", Currency.INR) == [("Home", 100_000), ("UK", 300_000)]
    assert outstanding(rows, "Ravi", Currency.INR) == 400_000

    made = balancing_entries(rows, "Ravi", Currency.INR, today=date(2026, 8, 22))
    assert len(made) == 2
    assert all(e.direction is Direction.received for e in made)
    assert sum(e.amount_minor for e in made) == 400_000

    # After settling, the net is exactly zero — not near it.
    after = rows + made
    assert outstanding(after, "Ravi", Currency.INR) == 0
    assert sum(e.signed_minor for e in after if e.person == "Ravi") == 0
    # And Amma is untouched.
    assert outstanding(after, "Amma", Currency.INR) == 50_000

    # Settling twice adds nothing, because nothing is owed any more.
    assert balancing_entries(after, "Ravi", Currency.INR) == []

    # A ledger where you owe them is not settleable by being repaid.
    owing = [entry("Ravi", "Loan", 100_000, Direction.received)]
    assert open_ledgers(owing, "Ravi", Currency.INR) == []
    assert balancing_entries(owing, "Ravi", Currency.INR) == []

    # Currencies never mix: settling rupees leaves the dollar ledger alone.
    mixed = [
        entry("Sam", "Books", 100_000),
        entry("Sam", "Books", 4_000, currency=Currency.USD),
    ]
    inr = balancing_entries(mixed, "Sam", Currency.INR)
    assert len(inr) == 1 and inr[0].currency is Currency.INR
    assert outstanding(mixed + inr, "Sam", Currency.USD) == 4_000

    print("ledger.settle: all checks passed")


if __name__ == "__main__":
    demo()
