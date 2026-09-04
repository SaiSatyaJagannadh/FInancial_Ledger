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

import enum
from dataclasses import dataclass, field
from datetime import date, timedelta

from ledger.models import EntryError, parse_date
from ledger.money import Currency, format_money, parse_currency, to_minor

#: The tab this lives in. Alongside the ledger's, never inside it.
WORKSHEET = "interest"

#: New columns go on the END, never in the middle. A tab that already exists
#: keeps its old header until it is widened, and every row in it is positional
#: — inserting a column mid-list would shift every value in every existing row
#: one place to the right and silently rewrite people's history.
COLUMNS = [
    "date", "person", "amount", "currency", "rate_percent", "note", "source",
    "kind", "attachment", "moved_to",
]

REQUIRED = ("date", "person", "amount")


class Kind(str, enum.Enum):
    """Whether this month's interest is still owed or has been handed over."""

    due = "due"        # they owe it
    given = "given"    # they have paid it

    @property
    def label(self) -> str:
        return {"due": "Still due", "given": "Given to me"}[self.value]


def parse_kind(value) -> Kind:
    """Read the word, whatever form it arrives in.

    Rows written before this column existed have nothing here, and the honest
    reading of an interest row with no status is that it is still owed.
    """
    text = str(value or "").strip().lower()
    if not text:
        return Kind.due
    if text in ("due", "owed", "pending", "unpaid", "outstanding"):
        return Kind.due
    if text in ("given", "paid", "received", "settled", "cleared", "got"):
        return Kind.given
    raise EntryError(f"kind must be 'due' or 'given', got {value!r}")

#: What a month's interest is charged on: the outstanding balance at the time.
DEFAULT_RATE = 2.0


@dataclass(frozen=True)
class Charge:
    """One month's interest against one person."""

    date: date
    person: str
    amount_minor: int
    currency: Currency = Currency.INR
    kind: Kind = Kind.due
    rate_percent: float = 0.0
    note: str = ""
    #: A photo or receipt, kept in the attachments tab like everything else.
    attachment: str = ""
    #: Who this interest was handed on to, when it was. Non-empty means a
    #: ledger entry exists for it, so it is no longer money owed to you as
    #: interest — it is a loan to them, and counting it twice would be wrong.
    moved_to: str = ""
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
            kind=parse_kind(row.get("kind")),
            rate_percent=rate,
            note=str(row.get("note") or "").strip(),
            attachment=str(row.get("attachment") or "").strip(),
            moved_to=str(row.get("moved_to") or "").strip(),
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
            self.kind.value,
            self.attachment,
            self.moved_to,
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


def clone_month(charges: list[Charge], source: date, target: date,
                currency: Currency = Currency.INR, *,
                kind: Kind = Kind.due) -> tuple[list[Charge], list[str]]:
    """The charges that would carry one month's interest into another.

    Interest is usually the same arrangement month after month — the same
    people, the same figures, the same reasons — so the alternative is retyping
    every name every month, which is both dull and the sort of thing a person
    eventually does wrong.

    Returns `(to_write, already_there)`. Writing is the caller's decision, the
    same shape as `settle.balancing_entries`, so the page can show what is
    about to happen before anything is saved.

    Three fields are deliberately **not** carried across:

    - `moved_to`, because a copy has not been handed to anybody. Carrying it
      would take the new charge out of the interest total *and* claim a ledger
      entry that does not exist — the money would be counted nowhere.
    - `attachment`, because August's receipt does not evidence September.
    - `kind`, which starts as "still due": a month that has only just begun has
      not been paid yet, whatever last month ended up as. `parse_kind` reads a
      blank status the same way, for the same reason.

    Anybody who already has a charge in the target month is skipped and named,
    never overwritten. `set_for_month` is an upsert, so cloning on top of a
    figure somebody had already corrected would silently undo that correction.
    """
    if month_start(source) == month_start(target):
        return [], []

    taken = set(for_month(charges, target, currency))
    to_write, already_there = [], []
    for charge in sorted(for_month(charges, source, currency).values(),
                         key=lambda c: c.person):
        if charge.person in taken:
            already_there.append(charge.person)
            continue
        to_write.append(Charge(
            date=month_start(target),
            person=charge.person,
            amount_minor=charge.amount_minor,
            currency=charge.currency,
            kind=kind,
            rate_percent=charge.rate_percent,
            note=charge.note,
            source=charge.source or "manual",
        ))
    return to_write, already_there


def set_for_month(person: str, when: date, amount_minor: int, *,
                  currency: Currency = Currency.INR, rate_percent: float = 0.0,
                  note: str = "", source: str = "manual",
                  kind: Kind = Kind.due, attachment: str = "",
                  moved_to: str = "", secrets: dict | None = None) -> str:
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
        currency=currency, kind=kind, rate_percent=rate_percent, note=note,
        attachment=attachment, moved_to=moved_to, source=source,
    )
    if existing is None:
        add(wanted, secrets)
        return "added"
    if (existing.amount_minor == amount_minor and existing.note == wanted.note
            and existing.kind is kind and existing.attachment == attachment
            and existing.moved_to == moved_to):
        return "unchanged"
    replace_row(existing, wanted, secrets)
    return "updated"


def ledger_entry(charge: Charge, person: str, *, ledger: str,
                 note: str = "") -> "object":
    """The ledger row for interest that somebody else actually took.

    **The one and only way anything on the Interest page reaches the lending
    ledger, and it happens because a person picked it, never by default.**
    Interest normally stays out of the ledger entirely: the ledger says how
    much of your money is out there, and interest says what it earned. But
    when the interest money is handed on to somebody rather than kept, it has
    stopped being interest and become a loan to them, and the ledger is where
    loans live.

    Returns the Entry. Writing it is the caller's decision.
    """
    from ledger.models import BY_HAND, Direction, Entry

    if not person.strip():
        raise EntryError("who took it?")
    return Entry(
        date=charge.date,
        person=person.strip(),
        ledger=ledger.strip() or "Interest",
        direction=Direction.given,
        amount_minor=charge.amount_minor,
        currency=charge.currency,
        note=trail_note(charge, person, note),
        source=BY_HAND,
    )


def trail_note(charge: Charge, person: str, note: str = "") -> str:
    """Why this row is in the ledger, in words that survive being read cold.

    The provenance is written first and **always**, with whatever reason was
    typed added after it. It used to be the other way round — a typed purpose
    replaced the trail entirely — and the rows that produced said only "given
    to vihar but used to pay proxy service": no mention of Narayana, of
    interest, or of which month. Six months on that is a mystery, and the
    interest tab is the only place holding the other half of the story.
    """
    trail = (
        f"{charge.person} interest for {charge.month_label}, "
        f"taken by {person.strip()}"
    )
    reason = str(note or "").strip()
    return f"{trail} — {reason}" if reason else trail


def find_ledger_entry(entries: list, charge: Charge, person: str,
                      ledger: str):
    """The ledger row already standing for this charge, if there is one.

    Without this, saving the same interest twice appended a second identical
    ledger entry while the interest row itself was merely updated — one charge,
    two loans, and the person shown owing fifteen thousand more than they do.
    It happened on the real sheet.

    Matched on the trail this bridge writes into the note, because that is the
    only mark that says "this row came from that charge". Matching on shape
    alone — person, ledger, currency, date, direction — matched *any* loan
    handed over on the first of that month, and the next save overwrote it with
    the interest figure. A real entry disappeared that way: the row still read
    "given", but for the interest amount, and the money actually lent was gone.

    The reason typed after the trail is ignored: a person is free to reword it
    between saves, and `trail_note` always writes the trail first.
    """
    from ledger.models import Direction

    trail = trail_note(charge, person)
    return next(
        (
            e for e in entries
            if e.note.startswith(trail)
            and e.person == person.strip()
            and e.ledger == (ledger.strip() or "Interest")
            and e.currency is charge.currency
            and e.direction is Direction.given
        ),
        None,
    )


def sync_ledger_entry(entries: list, charge: Charge, person: str, ledger: str,
                      note: str = "") -> str:
    """Write, or correct, the single ledger row for interest handed on.

    Both places that cross from interest to the ledger come through here, so
    the "is one already standing?" question is asked once and answered the same
    way. Returns what it did.
    """
    from ledger import store

    fresh = ledger_entry(charge, person, ledger=ledger, note=note)
    standing = find_ledger_entry(entries, charge, person, ledger)
    if standing is None:
        store.append(fresh)
        return "added"
    if standing.amount_minor != fresh.amount_minor:
        store.update(standing, fresh)
        return "updated"
    return "unchanged"


def recorded_total(charges: list[Charge], currency: Currency) -> int:
    """Interest still counted as interest — what has moved to the ledger is not.

    Once a charge has been handed on to somebody it is a loan to them and the
    ledger is counting it. Leaving it in this total as well would count the
    same money twice.
    """
    return sum(
        c.amount_minor for c in charges
        if c.currency is currency and not c.moved_to
    )


def moved_total(charges: list[Charge], currency: Currency) -> int:
    """How much interest has been handed on and now lives in the ledger."""
    return sum(
        c.amount_minor for c in charges
        if c.currency is currency and c.moved_to
    )


def split_by_kind(charges: list[Charge], currency: Currency) -> dict[Kind, int]:
    """How much is still due and how much has been handed over."""
    out = {kind: 0 for kind in Kind}
    for charge in charges:
        # A charge that has moved to the ledger is counted there, not here.
        if charge.currency is currency and not charge.moved_to:
            out[charge.kind] += charge.amount_minor
    return out


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
        # Named explicitly: a tab widened by a previous write has trailing
        # blank headings, and gspread counts two blanks as duplicate headers
        # and refuses to read the tab at all.
        records = sheet.get_all_records(expected_headers=COLUMNS)
    except Exception:  # noqa: BLE001 — fall back before giving up on the tab
        try:
            records = _records_by_position(sheet)
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


def _records_by_position(sheet) -> list[dict]:
    """Read the tab by column position when its header cannot be trusted.

    The header is only a label; the values are positional. If the headings are
    stale or duplicated, reading by position still gets the data out, which
    beats showing somebody an empty interest page.
    """
    values = sheet.get_all_values()
    return [
        dict(zip(COLUMNS, list(row) + [""] * (len(COLUMNS) - len(row))))
        for row in values[1:]
    ]


def _sheet(secrets: dict):
    """The interest tab, with its header brought up to date if need be.

    A tab created before a column existed keeps the header it was made with,
    and `get_all_records` then reads the new values under blank headings — or
    refuses outright, because a row of two blank headings counts as duplicates.
    Widening the header here is what makes adding a column safe. It is only
    ever an append, so no existing cell moves.
    """
    from ledger import store

    if not store.is_configured(secrets):
        raise RuntimeError("Demo mode: there is no sheet to write to.")
    sheet = store._open_worksheet(secrets, WORKSHEET)
    try:
        first = [str(v).strip() for v in sheet.row_values(1)]
    except Exception:  # noqa: BLE001 — a brand new tab has no rows at all
        first = []

    if not any(first):
        sheet.update(values=[COLUMNS], range_name="A1")
        return sheet

    named = [v for v in first if v]
    if named != COLUMNS:
        last = store._column_letter(len(COLUMNS))
        sheet.update(values=[COLUMNS], range_name=f"A1:{last}1")
    return sheet


def add(charge: Charge, secrets: dict | None = None) -> None:
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    store.append_rows(_sheet(secrets), [charge.to_row()])
    _announce("Interest charge", "added", after=charge, secrets=secrets)


def _announce(kind: str, action: str, *, before=None, after=None,
              secrets: dict | None = None) -> None:
    """Same best-effort notice the ledger sends, with this tab's columns.

    Note that `set_for_month` on the hand-it-on path writes twice — once for the
    figure, once to record who took it — so two notices arrive for that one
    action. Both are true: they are two writes to the sheet, and the second
    reads "moved_to: — → Vihar", which is the fact worth knowing.
    """
    try:
        from ledger import notify

        notify.changed(kind, action, before=before, after=after,
                       columns=COLUMNS, secrets=secrets)
    except Exception:  # noqa: BLE001 — never let a notice undo a save
        pass


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
    from ledger import archive

    archive.record(archive.INTEREST, charge, secrets)   # before, for the same reason
    sheet.delete_rows(charge.row)
    _announce("Interest charge", "deleted", before=charge, secrets=secrets)


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
    _announce("Interest charge", "edited", before=original, after=edited, secrets=secrets)


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

    # ------------------------------------------------ carrying a month forward
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    book = [
        Charge(date=aug, person="Chaitu", amount_minor=15_000_00, note="monthly",
               kind=Kind.given, moved_to="Vihar", attachment="sheet:abc"),
        Charge(date=aug, person="Sirisha", amount_minor=5_000_00, rate_percent=2.0),
        Charge(date=aug, person="Vihar", amount_minor=7_000_00),
        Charge(date=sep, person="Vihar", amount_minor=9_999_00),   # already set
    ]
    todo, already = clone_month(book, aug, sep, Currency.INR)

    assert [c.person for c in todo] == ["Chaitu", "Sirisha"], [c.person for c in todo]
    assert already == ["Vihar"], "somebody with September already must be left alone"
    assert all(c.date == sep for c in todo)
    assert [c.amount_minor for c in todo] == [15_000_00, 5_000_00], "figures carry"
    assert todo[0].note == "monthly", "the reason carries"
    assert todo[1].rate_percent == 2.0, "the rate carries"

    # The three that must not carry, each for its own reason.
    assert all(c.moved_to == "" for c in todo), "a copy has been handed to nobody"
    assert all(c.attachment == "" for c in todo), "August's receipt is not September's"
    assert all(c.kind is Kind.due for c in todo), "a month just begun is not paid"

    # Cloning a month onto itself is a no-op, not a duplicate of everything.
    assert clone_month(book, aug, aug, Currency.INR) == ([], [])

    # Currencies never mix, here as everywhere.
    dollars = [Charge(date=aug, person="Sam", amount_minor=4_000, currency=Currency.USD)]
    assert clone_month(dollars, aug, sep, Currency.INR) == ([], [])
    assert len(clone_month(dollars, aug, sep, Currency.USD)[0]) == 1

    # Cloning twice adds nothing the second time — everyone is already there.
    assert clone_month(book + todo, aug, sep, Currency.INR)[0] == []

    print("ledger.interest: all checks passed")


if __name__ == "__main__":
    demo()
