"""Old debts cleared between people — a record, and only a record.

Somebody hands somebody else money to clear an old debt of theirs. The debt
predates this app, or was settled in cash, or was never a ledger row at all —
so there is nothing here to net off against. Writing it into `entries` would
invent a loan that was never made, and adding it to a total would move a figure
that describes the ledger's own rows.

So this tab is **never summed into anything else**: not the ledger, not
spending, not interest. `compute.py` does not import it and must not. The only
arithmetic here is over these rows alone, to answer "how much has been handed
over under each name" — which is what the page is for.

Two names per row, because a clearance has two ends:

* `payer`  — who handed the money over.
* `under`  — the name it was given under, whose old debt it clears. The rows
             are grouped by this, since that is the question being asked.

They may be the same person: clearing your own old debt through this record is
a legitimate thing to write down, so it is not refused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ledger.models import EntryError, parse_date
from ledger.money import Currency, format_money, parse_currency, to_minor

#: Its own tab, alongside the ledger's. Never merged with one of the others.
WORKSHEET = "clearances"

#: `attachment` is appended **last**, not slotted in beside `currency` where it
#: would read better. A row written before it existed is seven cells long, and
#: if the header is ever unreadable `store.records` falls back to reading by
#: position — where an inserted column would have shifted `source` and `note`
#: one place along and quietly relabelled them. A column added at the end can
#: only ever be missing, which reads as "".
COLUMNS = ["date", "payer", "under", "amount", "currency", "note", "source",
           "attachment"]

#: A record with no names or no figure records nothing.
REQUIRED = ("date", "payer", "under", "amount")


@dataclass(frozen=True)
class Clearance:
    date: date
    payer: str
    under: str
    amount_minor: int
    currency: Currency = Currency.INR
    note: str = ""
    source: str = ""
    #: A receipt or a photo of the handover. Either `sheet:<id>` for a file in
    #: the workbook, or an http(s) link somebody keeps elsewhere.
    attachment: str = ""
    row: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.payer.strip():
            raise EntryError("who gave the money is required")
        if not self.under.strip():
            raise EntryError("the name it was given under is required")
        if self.amount_minor <= 0:
            raise EntryError("amount must be more than zero")

    def money(self) -> str:
        return format_money(self.amount_minor, self.currency)

    @property
    def year(self) -> int:
        return self.date.year

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Clearance:
        missing = [c for c in REQUIRED if not str(row.get(c, "")).strip()]
        if missing:
            raise EntryError(f"missing: {', '.join(missing)}")
        try:
            amount = to_minor(row["amount"])
        except ValueError as exc:
            raise EntryError(str(exc)) from exc
        return cls(
            date=parse_date(row["date"]),
            payer=str(row["payer"]).strip(),
            under=str(row["under"]).strip(),
            amount_minor=amount,
            currency=parse_currency(row.get("currency")),
            note=str(row.get("note") or "").strip(),
            source=str(row.get("source") or "").strip().lower(),
            attachment=str(row.get("attachment") or "").strip(),
            row=row_number,
        )

    def to_row(self) -> list[str]:
        # divmod, not `/ 100` — a float must not reach the sheet.
        whole, frac = divmod(self.amount_minor, 100)
        return [
            self.date.isoformat(),
            self.payer,
            self.under,
            f"{whole}.{frac:02d}",
            self.currency.value,
            self.note,
            self.source,
            self.attachment,
        ]


def by_under(rows: list[Clearance], currency: Currency) -> list[dict]:
    """What has been handed over under each name, **sorted by that name**.

    Alphabetical rather than biggest-first: this is a filing cabinet, and the
    question is "what is under Vihar", not "who is the largest". Currencies are
    never mixed, so one currency at a time.
    """
    buckets: dict[str, dict] = {}
    for c in rows:
        if c.currency is not currency:
            continue
        bucket = buckets.setdefault(
            c.under, {"under": c.under, "total_minor": 0, "count": 0, "payers": []}
        )
        bucket["total_minor"] += c.amount_minor
        bucket["count"] += 1
        if c.payer not in bucket["payers"]:
            bucket["payers"].append(c.payer)
    for bucket in buckets.values():
        bucket["payers"].sort(key=str.casefold)
    return sorted(buckets.values(), key=lambda b: b["under"].casefold())


def recorded_total(rows: list[Clearance], currency: Currency) -> int:
    """The sum of these rows, for this page's own heading and nowhere else."""
    return sum(c.amount_minor for c in rows if c.currency is currency)


def years(rows: list[Clearance]) -> list[int]:
    return sorted({c.date.year for c in rows}, reverse=True)


# ---------------------------------------------------------------- persistence
# Same workbook, its own tab. Reads go through `store.records`, appends through
# `store.append_rows`, and a row is re-read before it is touched — rows shift.

def load(secrets: dict | None = None) -> tuple[list[Clearance], list[str]]:
    """Every clearance, plus a message for each row that could not be read."""
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return [], []
    try:
        sheet = store._open_worksheet(secrets, WORKSHEET)
        records = store.records(sheet, COLUMNS)
    except Exception as exc:  # noqa: BLE001 — an unreachable sheet is not a crash
        return [], [f"Could not reach the clearances tab. {store._why(exc)}"]

    rows: list[Clearance] = []
    problems: list[str] = []
    for offset, raw in enumerate(records):
        number = offset + 2
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue
        try:
            rows.append(Clearance.from_row(cleaned, row_number=number))
        except EntryError as exc:
            problems.append(f"row {number}: {exc}")
    rows.sort(key=lambda c: (c.under.casefold(), c.date))
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


def _announce(action: str, *, before=None, after=None, secrets: dict | None = None) -> None:
    """Best-effort notice. A broken notifier must never undo a write."""
    try:
        from ledger import notify

        notify.changed("Debt clearance", action, before=before, after=after,
                       columns=COLUMNS, secrets=secrets)
    except Exception:  # noqa: BLE001
        pass


def add(record: Clearance, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    store.append_rows(_sheet(secrets), [record.to_row()])
    _announce("added", after=record, secrets=secrets)


def _matches(cells: list[str], record: Clearance) -> bool:
    """Does this raw row still describe `record`? The amount is compared as a
    number, since Sheets returns "42" for what was written as "42.00"."""
    if len(cells) < 4:
        return False
    try:
        return (
            parse_date(cells[0]) == record.date
            and cells[1].strip() == record.payer
            and cells[2].strip() == record.under
            and to_minor(cells[3]) == record.amount_minor
        )
    except (EntryError, ValueError):
        return False


def remove(record: Clearance, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if record.row is None:
        raise RuntimeError("This record has no sheet row, so it cannot be deleted.")
    sheet = _sheet(secrets)
    if not _matches(sheet.row_values(record.row), record):
        raise RuntimeError(
            f"Row {record.row} no longer matches — the sheet changed since it "
            "was loaded. Reload and try again."
        )
    # Archived *before* the row goes, and a failure here stops the deletion —
    # the same bargain the ledger and the interest tab make. A notice that
    # fails costs a message; an archive that fails costs the record.
    from ledger import archive

    archive.record(archive.CLEARANCE, record, secrets)
    sheet.delete_rows(record.row)
    _announce("deleted", before=record, secrets=secrets)


def demo() -> None:
    """Self-check for the parts that carry money, names or dates."""
    c = Clearance(date=date(2026, 3, 1), payer="RAVI", under="VIHAR",
                  amount_minor=250_000)
    assert c.money() == format_money(250_000, Currency.INR)

    # Both names and a positive figure are required.
    for bad in ({"payer": ""}, {"under": ""}, {"amount_minor": 0}):
        fields = dict(date=date(2026, 3, 1), payer="RAVI", under="VIHAR",
                      amount_minor=250_000)
        fields.update(bad)
        try:
            Clearance(**fields)
        except EntryError:
            pass
        else:
            raise AssertionError(f"{bad} should raise")

    # Clearing your own old debt is a real thing to record, not a typo.
    assert Clearance(date=date(2026, 3, 1), payer="RAVI", under="RAVI",
                     amount_minor=100).under == "RAVI"

    # An attachment survives the round trip, and a row written before the
    # column existed still reads — it is simply missing, which is "".
    with_file = Clearance(date=date(2026, 3, 1), payer="RAVI", under="VIHAR",
                          amount_minor=250_000, attachment="sheet:ab12")
    assert Clearance.from_row(
        dict(zip(COLUMNS, with_file.to_row()))).attachment == "sheet:ab12"
    seven = dict(zip(COLUMNS, with_file.to_row()[:7]))
    assert Clearance.from_row(seven).attachment == ""

    # Round-trips through the same door a sheet row goes through.
    back = Clearance.from_row(dict(zip(COLUMNS, c.to_row())))
    assert back == c, (back, c)
    assert "2500.00" in c.to_row()            # divmod, not float division
    assert Clearance.from_row(dict(zip(COLUMNS, Clearance(
        date=date(2026, 3, 1), payer="A", under="B", amount_minor=1).to_row()
    ))).amount_minor == 1

    for absent in REQUIRED:
        row = dict(zip(COLUMNS, c.to_row()))
        row[absent] = ""
        try:
            Clearance.from_row(row)
        except EntryError as exc:
            assert absent in str(exc), (absent, exc)
        else:
            raise AssertionError(f"{absent} should be required")

    # Grouped by the name it was given under, alphabetically, both payers kept.
    rows = [
        c,
        Clearance(date=date(2026, 4, 1), payer="AMMA", under="VIHAR",
                  amount_minor=150_000),
        Clearance(date=date(2026, 4, 2), payer="RAVI", under="CHAITU",
                  amount_minor=50_000),
        Clearance(date=date(2026, 4, 3), payer="RAVI", under="VIHAR",
                  amount_minor=90_000, currency=Currency.USD),
    ]
    grouped = by_under(rows, Currency.INR)
    assert [b["under"] for b in grouped] == ["CHAITU", "VIHAR"], grouped
    vihar = grouped[1]
    assert vihar["total_minor"] == 400_000        # the USD row is not mixed in
    assert vihar["count"] == 2
    assert vihar["payers"] == ["AMMA", "RAVI"]
    assert recorded_total(rows, Currency.INR) == 450_000
    assert recorded_total(rows, Currency.USD) == 90_000
    assert years(rows) == [2026]

    # A stale row number must not be trusted: the guard compares the amount
    # numerically, because Sheets hands back "250" for what we wrote as "250.00".
    assert _matches(["2026-03-01", "RAVI", "VIHAR", "2500", "INR"], c)
    assert not _matches(["2026-03-01", "RAVI", "CHAITU", "2500.00", "INR"], c)
    assert not _matches(["2026-03-01", "RAVI", "VIHAR", "25000.00", "INR"], c)
    assert not _matches([], c)

    print("ledger.clearance: all checks passed")


if __name__ == "__main__":
    demo()
