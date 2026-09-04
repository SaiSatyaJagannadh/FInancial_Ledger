"""What was deleted, when, and by whom — and how to put it back.

`store.delete` and `interest.remove` take a row off the sheet permanently, and
a spreadsheet has no undo of its own. Asking twice helps; it does not help the
person who meant the row below. So every deletion is written here first, into a
`deleted` tab, and can be restored from it.

**The archive is written before the row is removed, and a failure to archive
stops the deletion.** That is the opposite of how `notify` behaves, and
deliberately so: a notification that fails costs a message, while a deletion
that fails to archive costs the record itself. When the two cannot both happen,
the safe one is to keep the row.

The original cells are stored as JSON in a single column rather than spread
across the sheet. The ledger and the interest tab have different shapes and
both gain columns over time; one opaque column survives that, and restoring
needs the row exactly as it was, not a helpful interpretation of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from ledger.models import EntryError, parse_date

WORKSHEET = "deleted"

#: Metadata first so the tab is readable at a glance, payload last.
COLUMNS = ["deleted_at", "kind", "by", "summary", "source_row", "data"]

#: What was removed. The value is stored, so do not rename these.
ENTRY = "entry"
INTEREST = "interest"


@dataclass(frozen=True)
class Deletion:
    """One removed record, enough to read it and to put it back."""

    deleted_at: datetime
    kind: str
    by: str
    summary: str
    source_row: int | None
    data: list[str]
    row: int | None = field(default=None, compare=False)

    @property
    def when(self) -> str:
        return f"{self.deleted_at:%d %b %Y %H:%M}"

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Deletion:
        raw = str(row.get("data") or "")
        try:
            data = json.loads(raw) if raw else []
        except ValueError:
            # A hand-edited cell should cost this one row, not the whole page.
            data = []
        stamp = str(row.get("deleted_at") or "").strip()
        try:
            when = datetime.fromisoformat(stamp)
        except ValueError:
            try:
                when = datetime.combine(parse_date(stamp), datetime.min.time())
            except EntryError as exc:
                raise EntryError(f"unreadable deleted_at: {stamp!r}") from exc
        try:
            source_row = int(str(row.get("source_row") or "").strip() or 0) or None
        except ValueError:
            source_row = None
        return cls(
            deleted_at=when,
            kind=str(row.get("kind") or "").strip().lower(),
            by=str(row.get("by") or "").strip(),
            summary=str(row.get("summary") or "").strip(),
            source_row=source_row,
            data=[str(cell) for cell in data],
            row=row_number,
        )

    def to_row(self) -> list[str]:
        return [
            self.deleted_at.isoformat(timespec="seconds"),
            self.kind,
            self.by,
            self.summary,
            str(self.source_row or ""),
            json.dumps(self.data, ensure_ascii=False),
        ]


def summarise(record) -> str:
    """A line a person can read six months later without decoding JSON."""
    from ledger.money import format_money

    try:
        who = getattr(record, "person", "") or ""
        amount = format_money(record.amount_minor, record.currency)
        where = getattr(record, "ledger", "") or getattr(record, "month_label", "")
        direction = getattr(record, "direction", None)
        verb = ""
        if direction is not None:
            verb = " gave" if getattr(direction, "value", "") == "given" else " got back"
        return f"{who} · {amount}{verb}" + (f" · {where}" if where else "")
    except Exception:  # noqa: BLE001 — a summary must never block an archive
        return ""


def record(kind: str, item, secrets: dict | None = None) -> None:
    """Write one deletion to the archive. Raises if it cannot.

    Raising is the point: the caller deletes only after this returns, so a
    broken archive means the row stays on the sheet instead of vanishing with
    no trace of it anywhere.
    """
    from ledger import auth, store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is nothing to archive.")

    entry = Deletion(
        deleted_at=datetime.now(),
        kind=kind,
        by=auth.current_user(),
        summary=summarise(item),
        source_row=getattr(item, "row", None),
        data=list(item.to_row()),
    )
    store.append_rows(_sheet(secrets), [entry.to_row()], value_input_option="RAW")


def load(secrets: dict | None = None) -> tuple[list[Deletion], list[str]]:
    """Everything deleted, newest first, with unreadable rows reported."""
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return [], []
    try:
        rows = store.records(_sheet(secrets), COLUMNS)
    except Exception as exc:  # noqa: BLE001 — an absent tab is not a crash
        return [], [f"Could not read the deleted tab: {type(exc).__name__}: {exc}"]

    out, problems = [], []
    for offset, raw in enumerate(rows):
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue
        try:
            out.append(Deletion.from_row(cleaned, row_number=offset + 2))
        except EntryError as exc:
            problems.append(f"row {offset + 2}: {exc}")
    out.sort(key=lambda d: d.deleted_at, reverse=True)
    return out, problems


def rebuild(deletion: Deletion):
    """The Entry or Charge this deletion holds, ready to be written again.

    Goes back through the same `from_row` a sheet row goes through, so a
    restored record is validated exactly as if it had been read from the tab it
    is going back into. A row that cannot be rebuilt says so rather than
    producing something subtly different from what was removed.
    """
    if not deletion.data:
        raise EntryError("this deletion recorded no data, so it cannot be restored")

    if deletion.kind == ENTRY:
        from ledger.models import COLUMNS as SHAPE, Entry

        builder = Entry
    elif deletion.kind == INTEREST:
        from ledger.interest import COLUMNS as SHAPE, Charge

        builder = Charge
    else:
        raise EntryError(f"unknown kind {deletion.kind!r}")

    cells = list(deletion.data) + [""] * (len(SHAPE) - len(deletion.data))
    return builder.from_row(dict(zip(SHAPE, cells)))


def restore(deletion: Deletion, secrets: dict | None = None):
    """Put a deleted record back, and take it off the archive.

    Appended rather than written to its old row number: everything below that
    row moved up when it was removed, so the number means nothing now — the
    same reason `store.update` refuses to trust a stale one.
    """
    from ledger import interest, store

    secrets = store._secrets() if secrets is None else secrets
    record_ = rebuild(deletion)

    if deletion.kind == ENTRY:
        store.append(record_, secrets)
    else:
        interest.add(record_, secrets)

    _forget(deletion, secrets)
    return record_


def _forget(deletion: Deletion, secrets: dict) -> None:
    """Drop a restored row from the archive, confirming it first.

    Rows shift here exactly as they do everywhere else, so the same guard the
    ledger uses applies: re-read and check before removing.
    """
    from ledger import store

    if deletion.row is None:
        return
    sheet = _sheet(secrets)
    cells = sheet.row_values(deletion.row)
    if len(cells) < 2 or str(cells[1]).strip().lower() != deletion.kind:
        raise RuntimeError(
            f"Archive row {deletion.row} no longer matches — the tab changed "
            "since it was loaded. Reload and try again."
        )
    sheet.delete_rows(deletion.row)


def _sheet(secrets: dict):
    from ledger import store

    sheet = store._open_worksheet(secrets, WORKSHEET)
    try:
        first = sheet.row_values(1)
    except Exception:  # noqa: BLE001
        first = []
    if not any(str(v).strip() for v in first):
        sheet.update(values=[COLUMNS], range_name="A1")
    return sheet


def demo() -> None:
    """Self-check for the round trip, which is the part that loses a record."""
    from datetime import date

    from ledger.models import COLUMNS as SHAPE, Direction, Entry

    original = Entry(date=date(2026, 8, 27), person="Narayana Rao D",
                     ledger="Nanna", direction=Direction.given,
                     amount_minor=2_00_000_00, note="uncle", row=46)

    kept = Deletion(deleted_at=datetime(2026, 9, 3, 14, 22), kind=ENTRY,
                    by="ravi@example.com", summary=summarise(original),
                    source_row=46, data=original.to_row())

    # A deletion must survive the sheet exactly, or a restore is a guess.
    again = Deletion.from_row(dict(zip(COLUMNS, kept.to_row())))
    assert again.data == original.to_row(), again.data
    assert again.kind == ENTRY and again.by == "ravi@example.com"
    assert again.deleted_at == kept.deleted_at
    assert again.source_row == 46

    back = rebuild(again)
    assert back.person == original.person
    assert back.amount_minor == original.amount_minor, "the figure must come back exact"
    assert back.direction is original.direction
    assert back.to_row() == original.to_row(), "a restore is not an approximation"

    # The summary is for reading, and must survive a value it cannot parse.
    assert "Narayana Rao D" in kept.summary and "2,00,000" in kept.summary
    assert summarise(object()) == ""

    # An interest charge goes the same way, through its own shape.
    from ledger.interest import Charge

    charge = Charge(date=date(2026, 7, 1), person="Narayana",
                    amount_minor=15_000_00, note="monthly", row=7)
    kept_charge = Deletion(deleted_at=datetime(2026, 9, 3, 9, 0), kind=INTEREST,
                           by="", summary=summarise(charge), source_row=7,
                           data=charge.to_row())
    rebuilt = rebuild(Deletion.from_row(dict(zip(COLUMNS, kept_charge.to_row()))))
    assert rebuilt.to_row() == charge.to_row()
    assert rebuilt.person == "Narayana"

    # Nonsense must say so rather than restore something plausible.
    for bad in (Deletion(datetime.now(), "nonsense", "", "", None, ["x"]),
                Deletion(datetime.now(), ENTRY, "", "", None, [])):
        try:
            rebuild(bad)
        except EntryError:
            pass
        else:
            raise AssertionError(f"should have refused: {bad.kind!r} {bad.data}")

    # A corrupted data cell costs that row, not the page.
    assert Deletion.from_row({"deleted_at": "2026-09-03T14:22:00", "kind": ENTRY,
                              "data": "{not json"}).data == []

    print("ledger.archive: all checks passed")


if __name__ == "__main__":
    demo()
