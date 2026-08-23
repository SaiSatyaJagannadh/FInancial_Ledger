"""General transactions: money in and out that is nobody's debt.

Deliberately separate from the lending ledger. A debt has a person and a
direction and is eventually settled; rent is none of those things. Mixing them
would make "who owes me what" meaningless, so they live in their own worksheet
and are never summed together.

An expense can run over a period — rent from March to August — so a
transaction carries an optional end date. Everything else about it is one day.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date

from ledger.models import EntryError, parse_date
from ledger.money import Currency, format_money, parse_currency, to_minor

#: The tab this lives in, alongside the ledger's own.
WORKSHEET = "transactions"

COLUMNS = [
    "date", "end_date", "kind", "category", "description",
    "amount", "currency", "note", "source",
]

#: Only these three are required. An expense you have not categorised is still
#: an expense, and refusing it would just mean it never gets recorded.
REQUIRED = ("date", "category", "amount")


class Kind(str, enum.Enum):
    spent = "spent"      # money out
    earned = "earned"    # money in

    @property
    def label(self) -> str:
        return {"spent": "Spent", "earned": "Earned"}[self.value]


#: Starting points, not a closed list — you can type anything.
CATEGORIES = [
    "Rent", "Food", "Travel", "Fees", "Medical", "Shopping",
    "Bills", "Salary", "Refund", "Other",
]


def parse_kind(value) -> Kind:
    text = str(value or "").strip().lower()
    if text in ("spent", "spend", "expense", "out", "paid", "debit"):
        return Kind.spent
    if text in ("earned", "earn", "income", "in", "received", "credit"):
        return Kind.earned
    raise EntryError(f"kind must be 'spent' or 'earned', got {value!r}")


@dataclass(frozen=True)
class Transaction:
    date: date
    category: str
    amount_minor: int
    kind: Kind = Kind.spent
    currency: Currency = Currency.INR
    description: str = ""
    #: Set when the cost runs over a period rather than falling on one day.
    end_date: date | None = None
    note: str = ""
    source: str = ""
    row: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.category.strip():
            raise EntryError("category is required")
        if self.amount_minor <= 0:
            raise EntryError("amount must be more than zero")
        if self.end_date is not None and self.end_date < self.date:
            raise EntryError("the end date cannot be before the start date")

    @property
    def signed_minor(self) -> int:
        """Money out is negative here, unlike the ledger where out is positive.

        A ledger tracks what is owed to you; this tracks what you have. They
        are opposite questions, so they read the sign in opposite directions.
        """
        return -self.amount_minor if self.kind is Kind.spent else self.amount_minor

    @property
    def ongoing(self) -> bool:
        return self.end_date is not None and self.end_date != self.date

    @property
    def period(self) -> str:
        if not self.ongoing:
            return f"{self.date:%d %b %Y}"
        return f"{self.date:%d %b %Y} → {self.end_date:%d %b %Y}"

    @property
    def year(self) -> int:
        return self.date.year

    def money(self) -> str:
        return format_money(self.amount_minor, self.currency)

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Transaction:
        missing = [c for c in REQUIRED if not str(row.get(c, "")).strip()]
        if missing:
            raise EntryError(f"missing: {', '.join(missing)}")
        try:
            amount = to_minor(row["amount"])
        except ValueError as exc:
            raise EntryError(str(exc)) from exc

        raw_end = str(row.get("end_date") or "").strip()
        return cls(
            date=parse_date(row["date"]),
            end_date=parse_date(raw_end) if raw_end else None,
            kind=parse_kind(row.get("kind") or "spent"),
            category=str(row["category"]).strip(),
            description=str(row.get("description") or "").strip(),
            amount_minor=amount,
            currency=parse_currency(row.get("currency")),
            note=str(row.get("note") or "").strip(),
            source=str(row.get("source") or "").strip().lower(),
            row=row_number,
        )

    def to_row(self) -> list[str]:
        whole, frac = divmod(self.amount_minor, 100)
        return [
            self.date.isoformat(),
            self.end_date.isoformat() if self.end_date else "",
            self.kind.value,
            self.category,
            self.description,
            f"{whole}.{frac:02d}",
            self.currency.value,
            self.note,
            self.source,
        ]


@dataclass(frozen=True)
class Totals:
    spent_minor: int
    earned_minor: int
    count: int
    currency: Currency

    @property
    def net_minor(self) -> int:
        return self.earned_minor - self.spent_minor


def totals(rows: list[Transaction], currency: Currency) -> Totals:
    subset = [t for t in rows if t.currency is currency]
    return Totals(
        spent_minor=sum(t.amount_minor for t in subset if t.kind is Kind.spent),
        earned_minor=sum(t.amount_minor for t in subset if t.kind is Kind.earned),
        count=len(subset),
        currency=currency,
    )


def by_category(rows: list[Transaction], currency: Currency) -> list[dict]:
    """Spend per category, biggest first. Earnings are counted separately."""
    buckets: dict[str, dict] = {}
    for t in rows:
        if t.currency is not currency:
            continue
        bucket = buckets.setdefault(
            t.category, {"category": t.category, "spent_minor": 0,
                         "earned_minor": 0, "count": 0}
        )
        key = "spent_minor" if t.kind is Kind.spent else "earned_minor"
        bucket[key] += t.amount_minor
        bucket["count"] += 1
    return sorted(buckets.values(), key=lambda b: b["spent_minor"], reverse=True)


def years(rows: list[Transaction]) -> list[int]:
    """Every year the transactions touch, newest first.

    An ongoing cost belongs to every year it runs through, not only the one it
    started in — a rent that begins in December is mostly next year's money.
    """
    seen: set[int] = set()
    for t in rows:
        last = (t.end_date or t.date).year
        seen.update(range(t.date.year, last + 1))
    return sorted(seen, reverse=True)


def in_year(rows: list[Transaction], year: int) -> list[Transaction]:
    return [t for t in rows if t.date.year <= year <= (t.end_date or t.date).year]


def demo() -> None:
    """Self-check for the parts that carry money or dates."""
    t = Transaction(date=date(2026, 3, 1), category="Rent", amount_minor=1_500_000)
    assert t.kind is Kind.spent
    assert t.signed_minor == -1_500_000        # spending reduces what you have
    assert not t.ongoing
    assert t.period == "01 Mar 2026"

    run = Transaction(date=date(2026, 3, 1), end_date=date(2026, 8, 31),
                      category="Rent", amount_minor=1_500_000)
    assert run.ongoing
    assert "→" in run.period

    earned = Transaction(date=date(2026, 3, 5), category="Salary",
                         amount_minor=9_000_000, kind=Kind.earned)
    assert earned.signed_minor == 9_000_000

    # An end date before the start is a typo, not a period.
    try:
        Transaction(date=date(2026, 5, 1), end_date=date(2026, 1, 1),
                    category="Rent", amount_minor=100)
    except EntryError:
        pass
    else:
        raise AssertionError("backwards period should raise")

    # Only date, category and amount are required.
    made = Transaction.from_row({"date": "2026-03-01", "category": "Food", "amount": "250"})
    assert made.amount_minor == 25_000 and made.kind is Kind.spent
    for absent in ("date", "category", "amount"):
        row = {"date": "2026-03-01", "category": "Food", "amount": "250"}
        row[absent] = ""
        try:
            Transaction.from_row(row)
        except EntryError as exc:
            assert absent in str(exc), (absent, exc)
        else:
            raise AssertionError(f"{absent} should be required")

    assert Transaction.from_row(dict(zip(COLUMNS, run.to_row()))).end_date == run.end_date

    summary = totals([t, earned, run], Currency.INR)
    assert summary.spent_minor == 3_000_000
    assert summary.earned_minor == 9_000_000
    assert summary.net_minor == 6_000_000

    # An ongoing cost shows up in every year it spans.
    spanning = Transaction(date=date(2025, 12, 1), end_date=date(2026, 2, 1),
                           category="Rent", amount_minor=100)
    assert years([spanning]) == [2026, 2025]
    assert in_year([spanning], 2026) == [spanning]
    assert in_year([spanning], 2024) == []

    print("ledger.spend: all checks passed")


if __name__ == "__main__":
    demo()


# ---------------------------------------------------------------- persistence
# Same workbook, its own tab. The read/write guards mirror the ledger's: a row
# is re-read before it is changed, because rows shift.

def load(secrets: dict | None = None) -> tuple[list[Transaction], list[str]]:
    """Every transaction, plus a message for each row that could not be read."""
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return [], []
    try:
        sheet = store._open_worksheet(secrets, WORKSHEET)
        records = sheet.get_all_records()
    except Exception as exc:  # noqa: BLE001 — an unreachable sheet is not a crash
        return [], [f"Could not reach the transactions tab. {store._why(exc)}"]

    rows: list[Transaction] = []
    problems: list[str] = []
    for offset, raw in enumerate(records):
        number = offset + 2
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue
        try:
            rows.append(Transaction.from_row(cleaned, row_number=number))
        except EntryError as exc:
            problems.append(f"row {number}: {exc}")
    rows.sort(key=lambda t: (t.date, t.category))
    return rows, problems


def _sheet(secrets: dict):
    from ledger import store

    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to write to.")
    sheet = store._open_worksheet(secrets, WORKSHEET)
    first = []
    try:
        first = sheet.row_values(1)
    except Exception:  # noqa: BLE001 — a brand new tab has no rows at all
        first = []
    if not any(str(v).strip() for v in first):
        sheet.update(values=[COLUMNS], range_name="A1")
    return sheet


def add(transaction: Transaction, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    _sheet(secrets).append_row(transaction.to_row(), value_input_option="USER_ENTERED")


def _matches(cells: list[str], transaction: Transaction) -> bool:
    """Does this raw row still describe `transaction`? Amount compared as a
    number, since Sheets returns "42" for what we wrote as "42.00"."""
    if len(cells) < 6:
        return False
    try:
        return (
            parse_date(cells[0]) == transaction.date
            and cells[3].strip() == transaction.category
            and to_minor(cells[5]) == transaction.amount_minor
        )
    except (EntryError, ValueError):
        return False


def remove(transaction: Transaction, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if transaction.row is None:
        raise RuntimeError("This transaction has no sheet row, so it cannot be deleted.")
    sheet = _sheet(secrets)
    if not _matches(sheet.row_values(transaction.row), transaction):
        raise RuntimeError(
            f"Row {transaction.row} no longer matches — the sheet changed since "
            "it was loaded. Reload and try again."
        )
    sheet.delete_rows(transaction.row)


def replace_row(original: Transaction, edited: Transaction, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if original.row is None:
        raise RuntimeError("This transaction has no sheet row, so it cannot be edited.")
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
