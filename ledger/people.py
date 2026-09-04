"""Who rolls up under whom.

Chaitu and Sirisha borrow, but the arrangement is really with Vihar — the
money went out once, and what happens inside that family is their business.
The ledger still needs each person's own entries kept apart, so grouping is a
layer over the top rather than a change to the rows.

**Stored per person, not per entry.** A grouping is a fact about somebody, not
about one transfer, so it lives in its own tab: one row, one person, one
parent. Putting it on every entry would mean editing twenty rows to move one
person, and would let the same person sit in two groups at once — which is not
a state that means anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ledger.models import EntryError

#: The tab this lives in, alongside entries, transactions and attachments.
WORKSHEET = "people"

COLUMNS = ["person", "under", "note"]

#: How deep a chain may go before we call it a mistake. Chaitu under Vihar is
#: the real case; Chaitu under Vihar under Ravi under Amma is somebody having
#: an off day, and a cycle would hang the page.
MAX_DEPTH = 10


@dataclass(frozen=True)
class Member:
    person: str
    under: str = ""
    note: str = ""
    row: int | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not self.person.strip():
            raise EntryError("person is required")
        if self.under.strip() and self.under.strip() == self.person.strip():
            raise EntryError(f"{self.person} cannot be grouped under themselves")

    @classmethod
    def from_row(cls, row: dict, row_number: int | None = None) -> Member:
        person = str(row.get("person") or "").strip()
        if not person:
            raise EntryError("person is required")
        return cls(
            person=person,
            under=str(row.get("under") or "").strip(),
            note=str(row.get("note") or "").strip(),
            row=row_number,
        )

    def to_row(self) -> list[str]:
        return [self.person, self.under, self.note]


def mapping(members: list[Member]) -> dict[str, str]:
    """person -> their immediate parent, skipping anyone ungrouped."""
    return {m.person: m.under for m in members if m.under}


def group_of(person: str, parents: dict[str, str]) -> str:
    """The top of this person's chain — the name their money rolls up to.

    Somebody with no parent is their own group, so this is safe to call on
    everyone. A chain that loops back on itself stops rather than spinning:
    the sheet is hand-editable, so a cycle is a question of when, not if.
    """
    seen = {person}
    current = person
    for _ in range(MAX_DEPTH):
        parent = str(parents.get(current) or "").strip()
        if not parent or parent in seen:
            return current
        seen.add(parent)
        current = parent
    return current


def members_of(parent: str, parents: dict[str, str]) -> list[str]:
    """Everyone whose chain ends at `parent`, not counting the parent."""
    return sorted(
        person for person in parents
        if person != parent and group_of(person, parents) == parent
    )


def groups(people: list[str], parents: dict[str, str]) -> dict[str, list[str]]:
    """Every group as head -> its people, the head included.

    Keyed by the head so the dashboard can show one row per arrangement, with
    the individuals still available underneath.
    """
    out: dict[str, list[str]] = {}
    for person in people:
        out.setdefault(group_of(person, parents), []).append(person)
    return {head: sorted(names) for head, names in sorted(out.items())}


def would_cycle(person: str, parent: str, parents: dict[str, str]) -> bool:
    """Would grouping `person` under `parent` make a loop?

    Checked before saving rather than survived afterwards: "Vihar under Chaitu"
    when Chaitu is already under Vihar is an easy click to make and an
    unpleasant one to debug.
    """
    person, parent = person.strip(), parent.strip()
    if not parent:
        return False
    if parent == person:
        return True
    proposed = dict(parents)
    proposed[person] = parent
    seen = {parent}
    current = parent
    for _ in range(MAX_DEPTH + 1):
        nxt = str(proposed.get(current) or "").strip()
        if not nxt:
            return False
        if nxt == person or nxt in seen:
            return True
        seen.add(nxt)
        current = nxt
    return True


# ---------------------------------------------------------------- persistence
# Same workbook, its own tab, the same read-then-write guards as everywhere
# else: a row is confirmed before it is changed, because rows shift.

def load(secrets: dict | None = None) -> tuple[list[Member], list[str]]:
    """Every grouping, plus a message for each row that could not be read."""
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    if not store.is_configured(secrets):
        return [], []
    try:
        sheet = store._open_worksheet(secrets, WORKSHEET)
        records = store.records(sheet, COLUMNS)
    except Exception as exc:  # noqa: BLE001 — an unreachable tab is not a crash
        return [], [f"Could not reach the people tab. {store._why(exc)}"]

    rows: list[Member] = []
    problems: list[str] = []
    for offset, raw in enumerate(records):
        number = offset + 2
        cleaned = {str(k).strip().lower(): v for k, v in raw.items()}
        if not any(str(v).strip() for v in cleaned.values()):
            continue
        try:
            rows.append(Member.from_row(cleaned, row_number=number))
        except EntryError as exc:
            problems.append(f"row {number}: {exc}")
    rows.sort(key=lambda m: m.person)
    return rows, problems


def parents_of(secrets: dict | None = None) -> dict[str, str]:
    """Just the map, for callers that do not care about row numbers."""
    return mapping(load(secrets)[0])


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


def set_parent(person: str, parent: str, secrets: dict | None = None) -> None:
    """Group one person under another, or ungroup them with an empty parent.

    Upserts: one person has one grouping, so setting it twice must not leave
    two rows disagreeing about where they belong.
    """
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    person, parent = person.strip(), parent.strip()
    if not person:
        raise RuntimeError("Which person?")

    existing, _ = load(secrets)
    current = {m.person: m for m in existing}
    if would_cycle(person, parent, mapping(existing)):
        raise RuntimeError(
            f"Grouping {person} under {parent} would make a loop — "
            f"{parent} already rolls up to {person}."
        )

    member = Member(person=person, under=parent)
    sheet = _sheet(secrets)
    was = current.get(person)
    if was is None:
        store.append_rows(sheet, [member.to_row()])
        return

    if not _matches(sheet.row_values(was.row), was):
        raise RuntimeError(
            f"Row {was.row} no longer matches — the sheet changed since it was "
            "loaded. Reload and try again."
        )
    row = member.to_row()
    last = store._column_letter(len(row))
    sheet.update(
        values=[row], range_name=f"A{was.row}:{last}{was.row}",
        value_input_option="USER_ENTERED",
    )


def _matches(cells: list[str], member: Member) -> bool:
    """Does this raw row still name the same person?"""
    return bool(cells) and str(cells[0]).strip() == member.person


def remove(person: str, secrets: dict | None = None) -> None:
    """Drop somebody's grouping row entirely."""
    from ledger import store

    secrets = store._secrets() if secrets is None else secrets
    existing, _ = load(secrets)
    was = next((m for m in existing if m.person == person.strip()), None)
    if was is None or was.row is None:
        return
    sheet = _sheet(secrets)
    if not _matches(sheet.row_values(was.row), was):
        raise RuntimeError(
            f"Row {was.row} no longer matches — reload and try again."
        )
    sheet.delete_rows(was.row)


# ------------------------------------------------------- moving money across

def transfer_entries(
    parent: str,
    member: str,
    amount_minor: int,
    currency,
    *,
    ledger: str,
    today: date | None = None,
    note: str = "",
) -> list:
    """Two entries recording that `member` took `amount_minor` of `parent`'s money.

    Two, not one. The money left the house once — when it went to Vihar — so
    the group's total must not move. A single "given" row under Chaitu would
    say more money went out than actually did. Instead Vihar is shown as
    having returned it and Chaitu as having taken it, which nets to nothing
    across the group and puts the debt on the person who now holds it.
    """
    from ledger.models import BY_HAND, Direction, Entry

    when = today or date.today()
    if amount_minor <= 0:
        raise EntryError("amount must be more than zero")
    if parent.strip() == member.strip():
        raise EntryError("that is the same person on both sides")
    reason = note.strip() or f"{member} took this from {parent}"
    return [
        Entry(date=when, person=parent.strip(), ledger=ledger,
              direction=Direction.received, amount_minor=amount_minor,
              currency=currency, note=reason, source=BY_HAND),
        Entry(date=when, person=member.strip(), ledger=ledger,
              direction=Direction.given, amount_minor=amount_minor,
              currency=currency, note=reason, source=BY_HAND),
    ]


def demo() -> None:
    """Self-check on the chain walking, which is where this can hang or lie."""
    parents = {"Chaitu": "Vihar", "Sirisha": "Vihar"}
    assert group_of("Chaitu", parents) == "Vihar"
    assert group_of("Vihar", parents) == "Vihar"
    assert group_of("Nobody", parents) == "Nobody"
    assert members_of("Vihar", parents) == ["Chaitu", "Sirisha"]

    # A chain resolves to the top, not to the next step up.
    deep = {"Chaitu": "Vihar", "Vihar": "Amma"}
    assert group_of("Chaitu", deep) == "Amma"

    # A cycle stops instead of spinning forever.
    looped = {"A": "B", "B": "A"}
    assert group_of("A", looped) in ("A", "B")

    assert would_cycle("Vihar", "Chaitu", {"Chaitu": "Vihar"})
    assert would_cycle("A", "A", {})
    assert not would_cycle("Chaitu", "Vihar", {})
    assert not would_cycle("Chaitu", "", {"Chaitu": "Vihar"})

    grouped = groups(["Chaitu", "Sirisha", "Vihar", "Ravi"], parents)
    assert grouped["Vihar"] == ["Chaitu", "Sirisha", "Vihar"]
    assert grouped["Ravi"] == ["Ravi"]

    try:
        Member(person="Vihar", under="Vihar")
    except EntryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("a person under themselves must be refused")

    # Moving money inside a group must leave the group's total alone.
    from ledger.compute import totals as _totals
    from ledger.models import Direction, Entry
    from ledger.money import Currency

    lent = [Entry(date=date(2026, 1, 1), person="Vihar", ledger="Family",
                  direction=Direction.given, amount_minor=1_00_000_00,
                  currency=Currency.INR, note="")]
    moved = lent + transfer_entries(
        "Vihar", "Chaitu", 10_000_00, Currency.INR,
        ledger="Family", today=date(2026, 2, 1),
    )
    assert _totals(lent, Currency.INR).net_minor == _totals(moved, Currency.INR).net_minor, \
        "a transfer inside a group must not change what is owed overall"

    from ledger.compute import by_person
    balances = {s.person: s.net_minor for s in by_person(moved, Currency.INR)}
    assert balances["Vihar"] == 90_000_00
    assert balances["Chaitu"] == 10_000_00

    try:
        transfer_entries("Vihar", "Vihar", 100, Currency.INR, ledger="Family")
    except EntryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("the same person on both sides must be refused")

    print("ledger.people: all checks passed")


if __name__ == "__main__":
    demo()
