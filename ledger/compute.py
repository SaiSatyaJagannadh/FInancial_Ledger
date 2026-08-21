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
    given_paise: int
    received_paise: int
    last_activity: date
    ledgers: int
    open_ledgers: int

    @property
    def net_paise(self) -> int:
        """Positive: they owe you. Negative: you owe them."""
        return self.given_paise - self.received_paise


@dataclass(frozen=True)
class Totals:
    given_paise: int
    received_paise: int
    people: int
    ledgers: int
    open_ledgers: int
    records: int

    @property
    def net_paise(self) -> int:
        return self.given_paise - self.received_paise


def filter_entries(
    entries: list[Entry],
    *,
    period: str = ALL_TIME,
    people: list[str] | None = None,
    today: date | None = None,
) -> list[Entry]:
    """Apply the dashboard's two filters. Order of the entries is preserved."""
    months = PERIODS.get(period)
    if months is None and period not in PERIODS:
        raise ValueError(f"unknown period: {period!r}")

    cutoff = months_ago(today or date.today(), months) if months else None
    wanted = set(people) if people else None

    return [
        e
        for e in entries
        if (cutoff is None or e.date >= cutoff) and (wanted is None or e.person in wanted)
    ]


def _ledger_nets(entries: list[Entry]) -> dict[tuple[str, str], int]:
    nets: dict[tuple[str, str], int] = defaultdict(int)
    for entry in entries:
        nets[entry.key] += entry.signed_paise
    return nets


def totals(entries: list[Entry]) -> Totals:
    given = sum(e.amount_paise for e in entries if e.direction is Direction.given)
    received = sum(e.amount_paise for e in entries if e.direction is Direction.received)
    nets = _ledger_nets(entries)
    return Totals(
        given_paise=given,
        received_paise=received,
        people=len({e.person for e in entries}),
        ledgers=len(nets),
        # A ledger is settled when it nets to zero, whatever passed through it.
        open_ledgers=sum(1 for net in nets.values() if net != 0),
        records=len(entries),
    )


def by_person(entries: list[Entry]) -> list[PersonSummary]:
    """One row per person, biggest net owed first — who to chase, in order."""
    given: dict[str, int] = defaultdict(int)
    received: dict[str, int] = defaultdict(int)
    last: dict[str, date] = {}
    ledgers: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        if entry.direction is Direction.given:
            given[entry.person] += entry.amount_paise
        else:
            received[entry.person] += entry.amount_paise
        ledgers[entry.person].add(entry.ledger)
        if entry.person not in last or entry.date > last[entry.person]:
            last[entry.person] = entry.date

    nets = _ledger_nets(entries)
    open_per_person: dict[str, int] = defaultdict(int)
    for (person, _ledger), net in nets.items():
        if net != 0:
            open_per_person[person] += 1

    rows = [
        PersonSummary(
            person=person,
            given_paise=given.get(person, 0),
            received_paise=received.get(person, 0),
            last_activity=last[person],
            ledgers=len(ledgers[person]),
            open_ledgers=open_per_person.get(person, 0),
        )
        for person in ledgers
    ]
    rows.sort(key=lambda r: (-r.net_paise, r.person))
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
            buckets[(month_key(entry.date), entry.person)] += entry.amount_paise

    return [
        {"month": month, "person": person, "amount_paise": amount}
        for (month, person), amount in sorted(buckets.items())
    ]


def ledger_breakdown(entries: list[Entry]) -> list[dict]:
    """Per-ledger detail, for drilling into a person with more than one."""
    given: dict[tuple[str, str], int] = defaultdict(int)
    received: dict[tuple[str, str], int] = defaultdict(int)
    last: dict[tuple[str, str], date] = {}

    for entry in entries:
        target = given if entry.direction is Direction.given else received
        target[entry.key] += entry.amount_paise
        if entry.key not in last or entry.date > last[entry.key]:
            last[entry.key] = entry.date

    rows = []
    for key in sorted(set(given) | set(received)):
        person, name = key
        net = given.get(key, 0) - received.get(key, 0)
        rows.append(
            {
                "person": person,
                "ledger": name,
                "given_paise": given.get(key, 0),
                "received_paise": received.get(key, 0),
                "net_paise": net,
                "last_activity": last[key],
                "open": net != 0,
            }
        )
    rows.sort(key=lambda r: -r["net_paise"])
    return rows
