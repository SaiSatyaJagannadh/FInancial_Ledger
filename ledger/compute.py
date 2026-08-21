"""Every number the dashboard shows is computed here, in integer paise.

The UI formats; it never sums. Keeping the arithmetic in one module means the
tests cover exactly what the screen displays.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from dateutil_shim import months_ago
from ledger.models import Direction, Entry
from ledger.money import Currency

ALL_TIME = "All time"
PERIODS: dict[str, int | None] = {
    "Last 6 months": 6,
    "Last 12 months": 12,
    "Last 24 months": 24,
    ALL_TIME: None,
}


@dataclass(frozen=True)
class PersonSummary:
    person: str
    currency: Currency
    given_minor: int
    received_minor: int
    last_activity: date
    ledgers: int
    open_ledgers: int

    @property
    def net_minor(self) -> int:
        """Positive: they owe you. Negative: you owe them."""
        return self.given_minor - self.received_minor


@dataclass(frozen=True)
class Totals:
    currency: Currency
    given_minor: int
    received_minor: int
    people: int
    ledgers: int
    open_ledgers: int
    records: int

    @property
    def net_minor(self) -> int:
        return self.given_minor - self.received_minor


def by_currency(entries: list[Entry], currency: Currency) -> list[Entry]:
    """One currency's entries. Every figure on a tab comes through here."""
    return [e for e in entries if e.currency is currency]


def currencies_present(entries: list[Entry]) -> list[Currency]:
    """Which currencies actually have entries, in a stable order."""
    seen = {e.currency for e in entries}
    return [c for c in Currency if c in seen]


def filter_entries(
    entries: list[Entry],
    *,
    period: str = ALL_TIME,
    people: list[str] | None = None,
    currency: Currency | None = None,
    today: date | None = None,
) -> list[Entry]:
    """Apply the dashboard's filters. Order of the entries is preserved."""
    months = PERIODS.get(period)
    if months is None and period not in PERIODS:
        raise ValueError(f"unknown period: {period!r}")

    cutoff = months_ago(today or date.today(), months) if months else None
    wanted = set(people) if people else None

    return [
        e
        for e in entries
        if (cutoff is None or e.date >= cutoff)
        and (wanted is None or e.person in wanted)
        and (currency is None or e.currency is currency)
    ]


def _ledger_nets(entries: list[Entry]) -> dict[tuple[str, str, Currency], int]:
    nets: dict[tuple[str, str, Currency], int] = defaultdict(int)
    for entry in entries:
        nets[entry.key] += entry.signed_minor
    return nets


def totals(entries: list[Entry], currency: Currency | None = None) -> Totals:
    """Aggregate one currency's entries.

    Mixing currencies is a bug, not a feature, so this refuses rather than
    producing a meaningless sum.
    """
    found = {e.currency for e in entries}
    if len(found) > 1:
        raise ValueError(
            f"cannot total across currencies: {sorted(c.value for c in found)}. "
            "Filter to one currency first."
        )
    currency = currency or (found.pop() if found else Currency.INR)

    given = sum(e.amount_minor for e in entries if e.direction is Direction.given)
    received = sum(e.amount_minor for e in entries if e.direction is Direction.received)
    nets = _ledger_nets(entries)
    return Totals(
        currency=currency,
        given_minor=given,
        received_minor=received,
        people=len({e.person for e in entries}),
        ledgers=len(nets),
        # A ledger is settled when it nets to zero, whatever passed through it.
        open_ledgers=sum(1 for net in nets.values() if net != 0),
        records=len(entries),
    )


def by_person(entries: list[Entry], currency: Currency | None = None) -> list[PersonSummary]:
    """One row per person, biggest net owed first — who to chase, in order.

    Single-currency only, for the same reason as `totals`.
    """
    found = {e.currency for e in entries}
    if len(found) > 1:
        raise ValueError(
            f"cannot summarise across currencies: {sorted(c.value for c in found)}"
        )
    currency = currency or (found.pop() if found else Currency.INR)

    given: dict[str, int] = defaultdict(int)
    received: dict[str, int] = defaultdict(int)
    last: dict[str, date] = {}
    ledgers: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        if entry.direction is Direction.given:
            given[entry.person] += entry.amount_minor
        else:
            received[entry.person] += entry.amount_minor
        ledgers[entry.person].add(entry.ledger)
        if entry.person not in last or entry.date > last[entry.person]:
            last[entry.person] = entry.date

    nets = _ledger_nets(entries)
    open_per_person: dict[str, int] = defaultdict(int)
    for (person, _ledger, _currency), net in nets.items():
        if net != 0:
            open_per_person[person] += 1

    rows = [
        PersonSummary(
            person=person,
            currency=currency,
            given_minor=given.get(person, 0),
            received_minor=received.get(person, 0),
            last_activity=last[person],
            ledgers=len(ledgers[person]),
            open_ledgers=open_per_person.get(person, 0),
        )
        for person in ledgers
    ]
    rows.sort(key=lambda r: (-r.net_minor, r.person))
    return rows


def month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def monthly_given(entries: list[Entry]) -> list[dict]:
    """Money *given* per person per month — the outflow the chart plots.

    Only `given` entries: the chart answers "how much did I put out, and to
    whom", which repayments would muddy rather than inform.
    """
    buckets: dict[tuple[str, str], int] = defaultdict(int)
    for entry in entries:
        if entry.direction is Direction.given:
            buckets[(month_key(entry.date), entry.person)] += entry.amount_minor

    return [
        {"month": month, "person": person, "amount_minor": amount}
        for (month, person), amount in sorted(buckets.items())
    ]


def ledger_breakdown(entries: list[Entry]) -> list[dict]:
    """Per-ledger detail, for drilling into a person with more than one."""
    given: dict[tuple[str, str, Currency], int] = defaultdict(int)
    received: dict[tuple[str, str, Currency], int] = defaultdict(int)
    last: dict[tuple[str, str, Currency], date] = {}

    for entry in entries:
        target = given if entry.direction is Direction.given else received
        target[entry.key] += entry.amount_minor
        if entry.key not in last or entry.date > last[entry.key]:
            last[entry.key] = entry.date

    rows = []
    for key in sorted(set(given) | set(received), key=lambda k: (k[0], k[1], k[2].value)):
        person, name, currency = key
        net = given.get(key, 0) - received.get(key, 0)
        rows.append(
            {
                "person": person,
                "ledger": name,
                "currency": currency,
                "given_minor": given.get(key, 0),
                "received_minor": received.get(key, 0),
                "net_minor": net,
                "last_activity": last[key],
                "open": net != 0,
            }
        )
    rows.sort(key=lambda r: -r["net_minor"])
    return rows
