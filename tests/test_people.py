"""Grouping, and moving money inside a group.

The rule that matters: a transfer between people in one group must not change
what the group owes overall. The money left the house once.
"""

from __future__ import annotations

from datetime import date

import pytest

from ledger import people
from ledger.compute import by_group, by_person, totals
from ledger.models import Direction, Entry, EntryError
from ledger.money import Currency

PARENTS = {"Chaitu": "Vihar", "Sirisha": "Vihar"}


def entry(minor, *, person, ledger="Family", direction=Direction.given,
          currency=Currency.INR, when=date(2026, 1, 1)) -> Entry:
    return Entry(date=when, person=person, ledger=ledger, direction=direction,
                 amount_minor=minor, currency=currency, note="")


# ------------------------------------------------------------ walking chains

def test_a_person_with_no_parent_is_their_own_group():
    assert people.group_of("Ravi", PARENTS) == "Ravi"
    assert people.group_of("Vihar", PARENTS) == "Vihar"


def test_a_member_resolves_to_the_head():
    assert people.group_of("Chaitu", PARENTS) == "Vihar"


def test_a_chain_resolves_all_the_way_up():
    assert people.group_of("Chaitu", {"Chaitu": "Vihar", "Vihar": "Amma"}) == "Amma"


def test_a_cycle_stops_instead_of_hanging():
    """The sheet is hand-editable, so a loop is a question of when, not if."""
    assert people.group_of("A", {"A": "B", "B": "A"}) in ("A", "B")


def test_a_cycle_is_refused_before_it_is_saved():
    assert people.would_cycle("Vihar", "Chaitu", PARENTS)
    assert people.would_cycle("A", "A", {})
    assert not people.would_cycle("Chaitu", "Vihar", {})


def test_ungrouping_is_never_a_cycle():
    assert not people.would_cycle("Chaitu", "", PARENTS)


def test_a_person_under_themselves_is_refused():
    with pytest.raises(EntryError):
        people.Member(person="Vihar", under="Vihar")


def test_members_and_groups_agree():
    assert people.members_of("Vihar", PARENTS) == ["Chaitu", "Sirisha"]
    grouped = people.groups(["Chaitu", "Sirisha", "Vihar", "Ravi"], PARENTS)
    assert grouped["Vihar"] == ["Chaitu", "Sirisha", "Vihar"]
    assert grouped["Ravi"] == ["Ravi"]


def test_a_member_row_survives_the_sheet_round_trip():
    member = people.Member(person="Chaitu", under="Vihar", note="brother")
    rebuilt = people.Member.from_row(dict(zip(people.COLUMNS, member.to_row())))
    assert (rebuilt.person, rebuilt.under, rebuilt.note) == ("Chaitu", "Vihar", "brother")


# ------------------------------------------------------------- rolling up

def test_a_group_totals_its_people_together():
    book = [entry(1_00_000_00, person="Vihar"), entry(20_000_00, person="Chaitu")]
    group = next(g for g in by_group(book, Currency.INR, PARENTS) if g.head == "Vihar")
    assert group.net_minor == 1_20_000_00
    assert group.people == ["Chaitu", "Vihar"]
    assert group.grouped is True


def test_an_ungrouped_person_is_a_group_of_one():
    book = [entry(5_000_00, person="Ravi")]
    group = by_group(book, Currency.INR, PARENTS)[0]
    assert group.head == "Ravi" and group.grouped is False


def test_with_no_groupings_it_matches_by_person():
    book = [entry(1_00_000_00, person="Vihar"), entry(20_000_00, person="Chaitu")]
    assert (
        sorted(g.net_minor for g in by_group(book, Currency.INR, {}))
        == sorted(s.net_minor for s in by_person(book, Currency.INR))
    )


def test_grouping_never_changes_the_overall_total():
    """Rolling people up is a way of reading the book, not of changing it."""
    book = [entry(1_00_000_00, person="Vihar"), entry(20_000_00, person="Chaitu")]
    assert (
        sum(g.net_minor for g in by_group(book, Currency.INR, PARENTS))
        == totals(book, Currency.INR).net_minor
    )


# --------------------------------------------------- moving money in a group

def test_a_transfer_leaves_the_group_owing_the_same():
    """The whole point. Chaitu taking Vihar's money is not new lending."""
    before = [entry(1_00_000_00, person="Vihar")]
    after = before + people.transfer_entries(
        "Vihar", "Chaitu", 10_000_00, Currency.INR, ledger="Family",
        today=date(2026, 2, 1),
    )
    assert totals(after, Currency.INR).net_minor == totals(before, Currency.INR).net_minor


def test_a_transfer_moves_the_debt_onto_the_person_who_holds_it():
    book = [entry(1_00_000_00, person="Vihar")] + people.transfer_entries(
        "Vihar", "Chaitu", 10_000_00, Currency.INR, ledger="Family",
        today=date(2026, 2, 1),
    )
    balances = {s.person: s.net_minor for s in by_person(book, Currency.INR)}
    assert balances["Vihar"] == 90_000_00
    assert balances["Chaitu"] == 10_000_00


def test_a_transfer_is_two_entries_in_opposite_directions():
    given, received = None, None
    for row in people.transfer_entries("Vihar", "Chaitu", 500_00, Currency.INR,
                                       ledger="Family"):
        if row.direction is Direction.given:
            given = row
        else:
            received = row
    assert given.person == "Chaitu" and received.person == "Vihar"
    assert given.amount_minor == received.amount_minor == 500_00


def test_a_transfer_to_the_same_person_is_refused():
    with pytest.raises(EntryError):
        people.transfer_entries("Vihar", "Vihar", 100_00, Currency.INR, ledger="F")


@pytest.mark.parametrize("amount", [0, -100])
def test_a_transfer_must_be_a_positive_amount(amount):
    with pytest.raises(EntryError):
        people.transfer_entries("Vihar", "Chaitu", amount, Currency.INR, ledger="F")


def test_a_transfer_carries_the_currency_it_was_given():
    rows = people.transfer_entries("Vihar", "Chaitu", 100_00, Currency.USD, ledger="F")
    assert all(r.currency is Currency.USD for r in rows)
