"""What the money would have earned had it been invested instead of lent.

This is the opportunity cost of lending: an amount handed over on some date and
still outstanding today could have sat in a fixed deposit earning interest. The
answer is only ever a comparison — nothing here touches the ledger itself.

All money stays in integer minor units (paise/cents). Interest is computed in
Decimal and rounded once, at the end, so a long compounding chain cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from ledger.models import Entry
from ledger.money import Currency

#: Compounding frequencies, as periods per year. Indian FDs are quarterly by
#: convention, which is why that is the default rather than annual.
FREQUENCIES: dict[str, int] = {
    "Quarterly (typical FD)": 4,
    "Monthly": 12,
    "Half-yearly": 2,
    "Annually": 1,
    "Simple interest": 0,
}

DEFAULT_FREQUENCY = "Quarterly (typical FD)"

DAYS_PER_YEAR = Decimal(365)


@dataclass(frozen=True)
class Growth:
    """One outstanding amount, and what it could have become."""

    principal_minor: int
    value_minor: int
    days: int
    currency: Currency

    @property
    def interest_minor(self) -> int:
        return self.value_minor - self.principal_minor

    @property
    def years(self) -> float:
        return self.days / 365.0


def grow(
    principal_minor: int,
    *,
    rate_percent: float,
    since: date,
    until: date | None = None,
    periods_per_year: int = 4,
) -> Growth:
    """Compound `principal_minor` from `since` to `until` at `rate_percent` a year.

    `periods_per_year=0` means simple interest. A principal held for negative
    time (a future-dated entry) earns nothing rather than shrinking.
    """
    until = until or date.today()
    days = (until - since).days
    if days <= 0 or principal_minor <= 0 or rate_percent <= 0:
        return Growth(principal_minor, principal_minor, max(days, 0), Currency.INR)

    principal = Decimal(principal_minor)
    rate = Decimal(str(rate_percent)) / Decimal(100)
    years = Decimal(days) / DAYS_PER_YEAR

    if periods_per_year <= 0:
        value = principal * (Decimal(1) + rate * years)
    else:
        n = Decimal(periods_per_year)
        value = principal * (Decimal(1) + rate / n) ** (n * years)

    rounded = int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return Growth(principal_minor, rounded, days, Currency.INR)


def _outstanding_by_start(entries: list[Entry]) -> list[tuple[int, date]]:
    """Net still-owed amounts, each paired with the date it went out.

    Repayments are applied oldest-first, so what remains outstanding is the most
    recent money — which is the conservative reading: it has been out the
    shortest time and therefore earns the least in the comparison.
    """
    given = sorted(
        [(e.amount_minor, e.date) for e in entries if e.signed_minor > 0],
        key=lambda pair: pair[1],
    )
    repaid = sum(-e.signed_minor for e in entries if e.signed_minor < 0)

    remaining: list[tuple[int, date]] = []
    for amount, when in given:
        if repaid >= amount:
            repaid -= amount
            continue
        remaining.append((amount - repaid, when))
        repaid = 0
    return remaining


def what_if(
    entries: list[Entry],
    *,
    rate_percent: float,
    periods_per_year: int = 4,
    today: date | None = None,
) -> Growth:
    """Total opportunity cost across every still-outstanding amount.

    Each outstanding tranche compounds from its own date, because ₹1,000 lent in
    2020 and ₹1,000 lent last month have not been out of your pocket equally.
    """
    today = today or date.today()
    currency = entries[0].currency if entries else Currency.INR

    principal = 0
    value = 0
    oldest = today
    for amount, when in _outstanding_by_start(entries):
        step = grow(
            amount,
            rate_percent=rate_percent,
            since=when,
            until=today,
            periods_per_year=periods_per_year,
        )
        principal += step.principal_minor
        value += step.value_minor
        oldest = min(oldest, when)

    return Growth(principal, value, (today - oldest).days, currency)


def demo() -> None:
    """Self-check: the arithmetic that matters, verified by hand-known values."""
    # ₹1,00,000 for exactly one year at 10%, compounded quarterly, is the
    # textbook 1.10381 factor.
    one_year = grow(
        10_000_000, rate_percent=10, since=date(2025, 1, 1), until=date(2026, 1, 1),
        periods_per_year=4,
    )
    assert one_year.value_minor == 11_038_129, one_year.value_minor

    # Simple interest on the same money is exactly 10%.
    simple = grow(
        10_000_000, rate_percent=10, since=date(2025, 1, 1), until=date(2026, 1, 1),
        periods_per_year=0,
    )
    assert simple.value_minor == 11_000_000, simple.value_minor

    # No time, no rate, or a future date must never invent money.
    same_day = grow(500, rate_percent=9, since=date(2026, 1, 1), until=date(2026, 1, 1))
    assert same_day.value_minor == 500
    backwards = grow(500, rate_percent=9, since=date(2026, 6, 1), until=date(2026, 1, 1))
    assert backwards.value_minor == 500
    assert grow(500, rate_percent=0, since=date(2020, 1, 1)).value_minor == 500

    # Repayments retire the oldest money first, so a fully repaid ledger has
    # nothing left to compound.
    from ledger.models import Direction

    def entry(day: date, minor: int, direction: Direction) -> Entry:
        return Entry(date=day, person="P", ledger="L", direction=direction,
                     amount_minor=minor, currency=Currency.INR)

    settled = [
        entry(date(2020, 1, 1), 1000, Direction.given),
        entry(date(2021, 1, 1), 1000, Direction.received),
    ]
    assert what_if(settled, rate_percent=10).interest_minor == 0

    partial = [
        entry(date(2020, 1, 1), 1000, Direction.given),
        entry(date(2024, 1, 1), 2000, Direction.given),
        entry(date(2025, 1, 1), 1000, Direction.received),
    ]
    # The 2020 tranche is cleared by the repayment; only the 2024 one remains.
    assert [pair[1] for pair in _outstanding_by_start(partial)] == [date(2024, 1, 1)]

    print("ledger.invest: all checks passed")


if __name__ == "__main__":
    demo()
