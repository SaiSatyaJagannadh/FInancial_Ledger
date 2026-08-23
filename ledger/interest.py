"""Interest charged on money already lent.

Its own tab, and **never added into the lending ledger**. The two answer
different questions: the ledger says how much of your money is out there, and
this says what it earned while it was. Adding them would inflate "who owes me
what" with a figure that was never handed to anybody, and once merged the two
cannot be told apart again.

A charge is recorded per person per month. The rate suggests the figure — on
what that person still owes, using the same compounding as
`ledger/invest.py` — and then you can change it before saving, because the
rate is usually an understanding rather than a contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ledger.models import EntryError, parse_date
from ledger.money import Currency, format_money, parse_currency, to_minor

#: The tab this lives in. Alongside the ledger's, never inside it.
WORKSHEET = "interest"

COLUMNS = [
    "date", "person", "amount", "currency", "rate_percent", "note", "source",
]

REQUIRED = ("date", "person", "amount")

#: What a month's interest is charged on: the outstanding balance at the time.
DEFAULT_RATE = 2.0


@dataclass(frozen=True)
class Charge:
    """One month's interest against one person."""

    date: date
    person: str
    amount_minor: int
    currency: Currency = Currency.INR
    rate_percent: float = 0.0
    note: str = ""
    source: str = ""
    row: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.person.strip():
            raise EntryError("person is required")
        if self.amount_minor <= 0:
            raise EntryError("interest must be more than zero")

    @property
    def month(self) -> str:
        return f"{self.date:%Y-%m}"

    @property
    def month_label(self) -> str:
        return f"{self.date:%b %Y}"

    def money(self) -> str:
        return format_money(self.amount_minor, self.currency)

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Charge:
        missing = [c for c in REQUIRED if not str(row.get(c, "")).strip()]
        if missing:
            raise EntryError(f"missing: {', '.join(missing)}")
        try:
            amount = to_minor(row["amount"])
        except ValueError as exc:
            raise EntryError(str(exc)) from exc
        try:
            rate = float(str(row.get("rate_percent") or 0) or 0)
        except ValueError:
            rate = 0.0
        return cls(
            date=parse_date(row["date"]),
            person=str(row["person"]).strip(),
            amount_minor=amount,
            currency=parse_currency(row.get("currency")),
            rate_percent=rate,
            note=str(row.get("note") or "").strip(),
            source=str(row.get("source") or "").strip().lower(),
            row=row_number,
        )

    def to_row(self) -> list[str]:
        # divmod, not / 100 — a float must not reach the sheet.
        whole, frac = divmod(self.amount_minor, 100)
        return [
            self.date.isoformat(),
            self.person,
            f"{whole}.{frac:02d}",
            self.currency.value,
            f"{self.rate_percent:g}",
            self.note,
            self.source,
        ]


def month_start(when: date) -> date:
    return when.replace(day=1)


def suggest(entries: list, person: str, *, rate_percent: float,
            currency: Currency = Currency.INR, on: date | None = None) -> int:
    """A month's interest on what `person` still owes, in minor units.

    The balance is what the ledger says is outstanding for them in this
    currency; the charge is one month of `rate_percent`. Computed here rather
    than typed from memory, but only ever *offered* — the page lets it be
    overridden before it is saved.
    """
    from ledger.compute import by_person

    mine = [
        e for e in entries
        if e.currency is currency and e.person == person
        and e.date <= (on or date.today())
    ]
    if not mine:
        return 0
    summary = next((s for s in by_person(mine, currency) if s.person == person), None)
    if summary is None or summary.net_minor <= 0:
        return 0
    # Round half-up on the paise, so a suggestion never quietly loses one.
    return int(summary.net_minor * rate_percent / 100 + 0.5)


def already_charged(charges: list[Charge], person: str, when: date,
                    currency: Currency = Currency.INR) -> Charge | None:
    """This person's charge for that month, if one is already recorded.

    Interest is monthly, so a second row for the same month is nearly always a
    double click rather than an intention.
    """
    wanted = f"{when:%Y-%m}"
    return next(
        (c for c in charges
         if c.person == person and c.month == wanted and c.currency is currency),
        None,
    )


def months_back(count: int = 18, *, today: date | None = None) -> list[date]:
    """The last `count` month-starts, newest first — what the month picker offers."""
    cursor = month_start(today or date.today())
    out = [cursor]
    for _ in range(count - 1):
        cursor = month_start(cursor - timedelta(days=1))
        out.append(cursor)
    return out


def for_month(charges: list[Charge], when: date,
              currency: Currency = Currency.INR) -> dict[str, Charge]:
    """person -> their charge for that month, for the people who have one."""
    wanted = f"{when:%Y-%m}"
    return {
        c.person: c for c in charges
        if c.month == wanted and c.currency is currency
    }


def set_for_month(person: str, when: date, amount_minor: int, *,
                  currency: Currency = Currency.INR, rate_percent: float = 0.0,
                  note: str = "", source: str = "manual",
                  secrets: dict | None = None) -> str:
    """Set one person's interest for one month. Returns what it did.

    One figure per person per month, so this is an upsert rather than an
    append: typing over August's number has to *change* August, not add a
    second August row that silently doubles the month.

    An amount of zero removes the charge instead of storing a zero — a row
    saying "no interest" and no row at all mean the same thing, and only one of
    them can drift out of step with the other.
    """
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    existing = for_month(load(secrets)[0], when, currency).get(person)

    if amount_minor <= 0:
        if existing is None:
            return "unchanged"
        remove(existing, secrets)
        return "removed"

    wanted = Charge(
        date=month_start(when), person=person, amount_minor=amount_minor,
        currency=currency, rate_percent=rate_percent, note=note, source=source,
    )
    if existing is None:
        add(wanted, secrets)
        return "added"
    if existing.amount_minor == amount_minor and existing.note == wanted.note:
        return "unchanged"
    replace_row(existing, wanted, secrets)
    return "updated"


def totals(charges: list[Charge], currency: Currency) -> int:
    """All interest in one currency. Currencies are never added together."""
    return sum(c.amount_minor for c in charges if c.currency is currency)


def by_person(charges: list[Charge], currency: Currency) -> list[dict]:
    """Interest per person, most first."""
    totals_by: dict[str, int] = {}
    months: dict[str, int] = {}
    last: dict[str, date] = {}
    for charge in charges:
        if charge.currency is not currency:
            continue
        totals_by[charge.person] = totals_by.get(charge.person, 0) + charge.amount_minor
        months[charge.person] = months.get(charge.person, 0) + 1
        if charge.person not in last or charge.date > last[charge.person]:
            last[charge.person] = charge.date
    rows = [
        {"person": person, "total_minor": total, "months": months[person],
         "last": last[person], "currency": currency}
        for person, total in totals_by.items()
    ]
    rows.sort(key=lambda r: -r["total_minor"])
    return rows


def by_month(charges: list[Charge], currency: Currency) -> list[dict]:
    """Interest per month, oldest first — the shape a chart wants."""
    buckets: dict[str, int] = {}
    for charge in charges:
        if charge.currency is currency:
            buckets[charge.month] = buckets.get(charge.month, 0) + charge.amount_minor
    return [{"month": m, "total_minor": t} for m, t in sorted(buckets.items())]


def years(charges: list[Charge]) -> list[int]:
    return sorted({c.date.year for c in charges}, reverse=True)


# ---------------------------------------------------------------- persistence
# Its own tab, the same read-then-confirm guards as the ledger: rows shift, and
# a stale row number would rewrite somebody else's charge.

def load(secrets: dict | None = None) -> tuple[list[Charge], list[str]]:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return [], []
    try:
        sheet = store._open_worksheet(secrets, WORKSHEET)
        records = sheet.get_all_records()
    except Exception as exc:  # noqa: BLE001 — an unreachable tab is not a crash
        return [], [f"Could not reach the interest tab. {store._why(exc)}"]

    rows: list[Charge] = []
    problems: list[str] = []
    for offset, raw in enumerate(records):
        number = offset + 2
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue
        try:
            rows.append(Charge.from_row(cleaned, row_number=number))
        except EntryError as exc:
            problems.append(f"row {number}: {exc}")
    rows.sort(key=lambda c: (c.date, c.person))
    return rows, problems


def _sheet(secrets: dict):
    from ledger import store

    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to write to.")
    sheet = store._open_worksheet(secrets, WORKSHEET)
    try:
        first = sheet.row_values(1)
    except Exception:  # noqa: BLE001 — a brand new tab has no rows at all
        first = []
    if not any(str(v).strip() for v in first):
        sheet.update(values=[COLUMNS], range_name="A1")
    return sheet


def add(charge: Charge, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    _sheet(secrets).append_row(charge.to_row(), value_input_option="USER_ENTERED")


def _matches(cells: list[str], charge: Charge) -> bool:
    """Amount compared as a number: Sheets hands back "42" for "42.00"."""
    if len(cells) < 3:
        return False
    try:
        return (
            parse_date(cells[0]) == charge.date
            and str(cells[1]).strip() == charge.person
            and to_minor(cells[2]) == charge.amount_minor
        )
    except (EntryError, ValueError):
        return False


def remove(charge: Charge, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if charge.row is None:
        raise RuntimeError("This charge has no sheet row, so it cannot be deleted.")
    sheet = _sheet(secrets)
    if not _matches(sheet.row_values(charge.row), charge):
        raise RuntimeError(
            f"Row {charge.row} no longer matches — the sheet changed since it "
            "was loaded. Reload and try again."
        )
    sheet.delete_rows(charge.row)


def replace_row(original: Charge, edited: Charge, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if original.row is None:
        raise RuntimeError("This charge has no sheet row, so it cannot be edited.")
    sheet = _sheet(secrets)
    if not _matches(sheet.row_values(original.row), original):
        raise RuntimeError(
            f"Row {original.row} no longer matches — the sheet changed since it "
            "was loaded. Reload and try again."
        )
    row = edited.to_row()
    last = store._column_letter(len(row))
    sheet.update(
        values=[row], range_name=f"A{original.row}:{last}{original.row}",
        value_input_option="USER_ENTERED",
    )


def demo() -> None:
    """Self-check: the suggestion maths, and the round trip through a row."""
    from ledger.models import Direction, Entry

    entries = [
        Entry(date=date(2026, 1, 1), person="Chaitu", ledger="Loan",
              direction=Direction.given, amount_minor=50_000_00,
              currency=Currency.INR, note=""),
        Entry(date=date(2026, 2, 1), person="Chaitu", ledger="Loan",
              direction=Direction.received, amount_minor=10_000_00,
              currency=Currency.INR, note=""),
    ]

    # 2% of the ₹40,000 still outstanding, not of the ₹50,000 first handed over.
    assert suggest(entries, "Chaitu", rate_percent=2.0, on=date(2026, 3, 1)) == 800_00
    assert suggest(entries, "Nobody", rate_percent=2.0) == 0

    # A settled person is charged nothing rather than a negative.
    settled = entries + [
        Entry(date=date(2026, 3, 1), person="Chaitu", ledger="Loan",
              direction=Direction.received, amount_minor=40_000_00,
              currency=Currency.INR, note=""),
    ]
    assert suggest(settled, "Chaitu", rate_percent=2.0, on=date(2026, 4, 1)) == 0

    # A row survives the trip to the sheet and back unchanged.
    charge = Charge(date=date(2026, 3, 31), person="Chaitu", amount_minor=800_00,
                    rate_percent=2.0, note="March")
    rebuilt = Charge.from_row(dict(zip(COLUMNS, charge.to_row())))
    assert rebuilt.amount_minor == charge.amount_minor
    assert rebuilt.person == charge.person and rebuilt.date == charge.date
    assert rebuilt.rate_percent == 2.0

    # The amount never goes through a float on the way out.
    assert Charge(date=date(2026, 3, 1), person="X",
                  amount_minor=1_234_56).to_row()[2] == "1234.56"

    # One charge per person per month.
    charges = [charge]
    assert already_charged(charges, "Chaitu", date(2026, 3, 5)) is charge
    assert already_charged(charges, "Chaitu", date(2026, 4, 5)) is None
    assert already_charged(charges, "Sirisha", date(2026, 3, 5)) is None

    # Currencies are counted apart, never together.
    mixed = charges + [Charge(date=date(2026, 3, 31), person="Sam",
                              amount_minor=50_00, currency=Currency.USD)]
    assert totals(mixed, Currency.INR) == 800_00
    assert totals(mixed, Currency.USD) == 50_00

    try:
        Charge(date=date(2026, 1, 1), person="X", amount_minor=0)
    except EntryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a zero charge must be refused")

    print("ledger.interest: all checks passed")


if __name__ == "__main__":
    demo()
