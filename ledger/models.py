"""The entry record and its validation. One row of the sheet is one Entry."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime

from ledger.money import to_paise

#: Sheet header, in order. Changing this changes the sheet contract.
COLUMNS = ["date", "person", "ledger", "direction", "amount", "note"]

_DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%d %b %Y", "%d %B %Y")


class Direction(str, enum.Enum):
    given = "given"        # money out, to them
    received = "received"  # money back, from them


class EntryError(ValueError):
    """The row cannot be trusted as a ledger entry."""


def parse_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        raise EntryError("date is required")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise EntryError(f"unrecognised date: {value!r}")


def parse_direction(value: str) -> Direction:
    text = str(value).strip().lower()
    # Sheets get filled in by hand, so accept the words people actually type.
    if text in ("given", "give", "gave", "out", "lent", "paid"):
        return Direction.given
    if text in ("received", "receive", "recd", "got", "in", "repaid", "returned"):
        return Direction.received
    raise EntryError(f"direction must be 'given' or 'received', got {value!r}")


@dataclass(frozen=True)
class Entry:
    date: date
    person: str
    ledger: str
    direction: Direction
    amount_paise: int
    note: str = ""
    #: 1-based row in the sheet, when the entry came from one.
    row: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.person.strip():
            raise EntryError("person is required")
        if not self.ledger.strip():
            raise EntryError("ledger is required")
        if self.amount_paise <= 0:
            # Direction carries the sign; a negative amount would double-negate.
            raise EntryError("amount must be positive — use direction for the sign")

    @property
    def signed_paise(self) -> int:
        """Contribution to net owed: given adds, received subtracts."""
        return self.amount_paise if self.direction is Direction.given else -self.amount_paise

    @property
    def key(self) -> tuple[str, str]:
        """Identifies the ledger this entry belongs to."""
        return (self.person, self.ledger)

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Entry:
        """Build from a sheet row. Raises EntryError with the offending field."""
        missing = [c for c in ("date", "person", "ledger", "direction", "amount") if c not in row]
        if missing:
            raise EntryError(f"missing column(s): {', '.join(missing)}")
        try:
            amount = to_paise(row["amount"])
        except ValueError as exc:
            raise EntryError(str(exc)) from exc
        return cls(
            date=parse_date(row["date"]),
            person=str(row["person"]).strip(),
            ledger=str(row["ledger"]).strip(),
            direction=parse_direction(row["direction"]),
            amount_paise=amount,
            note=str(row.get("note") or "").strip(),
            row=row_number,
        )

    def to_row(self) -> list[str]:
        """Serialise for the sheet, in COLUMNS order.

        The amount is rendered by integer divmod, not `paise / 100`: this is the
        persistence boundary, and it is the one place a float must not appear.
        """
        whole, frac = divmod(self.amount_paise, 100)
        return [
            self.date.isoformat(),
            self.person,
            self.ledger,
            self.direction.value,
            f"{whole}.{frac:02d}",
            self.note,
        ]
