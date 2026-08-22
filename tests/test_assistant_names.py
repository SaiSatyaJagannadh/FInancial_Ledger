"""Snapping a proposed name onto the one already in the ledger."""

from __future__ import annotations

import pytest

from ledger.assistant import _to_reply, canonical

PEOPLE = ["VIHAR DVM", "Nanna"]
LEDGERS = ["UK LEDGER", "Home"]


@pytest.mark.parametrize("given,expected", [
    ("VIHAR DVM", "VIHAR DVM"),
    ("vihar dvm", "VIHAR DVM"),          # case only
    ("  VIHAR DVM  ", "VIHAR DVM"),      # padding
    ("VIHAR", "VIHAR DVM"),              # the real failure: abbreviated
    ("vihar", "VIHAR DVM"),
    ("VIHAR DVM SIR", "VIHAR DVM"),      # extra words on the end
    ("nanna", "Nanna"),
])
def test_snaps_to_the_existing_person(given, expected):
    assert canonical(given, PEOPLE) == expected


def test_an_ambiguous_abbreviation_is_left_alone():
    """Guessing between two people is worse than leaving it for the human."""
    assert canonical("Ravi", ["Ravi Kumar", "Ravi Shankar"]) == "Ravi"


def test_a_genuinely_new_name_is_kept():
    assert canonical("Bunty", PEOPLE) == "Bunty"


def test_no_known_names_means_no_change():
    assert canonical("Someone", []) == "Someone"


def test_blank_stays_blank():
    assert canonical("   ", PEOPLE) == ""


def test_a_prefix_without_a_word_break_does_not_snap():
    """"Nan" must not become "Nanna" — that is a different word, not a
    shortened form of the same one."""
    assert canonical("Nan", ["Nanna"]) == "Nan"


def base_row(**kw):
    row = {"date": "2026-01-05", "person": "VIHAR", "ledger": "VIHAR",
           "direction": "given", "amount": "750", "currency": "INR", "note": "Ramesh"}
    row.update(kw)
    return {"entries": [row]}


def test_the_reply_canonicalises_both_names():
    reply = _to_reply(base_row(), PEOPLE, LEDGERS, {"VIHAR DVM": ["UK LEDGER"]})
    entry = reply.drafts[0].entry
    assert entry.person == "VIHAR DVM"
    assert entry.ledger == "UK LEDGER"
    assert entry.note == "Ramesh"


def test_an_invented_ledger_falls_back_to_the_persons_only_one():
    """The model naming the ledger after the person is the common mistake."""
    reply = _to_reply(base_row(ledger="Vihar's account"), PEOPLE, LEDGERS,
                      {"VIHAR DVM": ["UK LEDGER"]})
    assert reply.drafts[0].entry.ledger == "UK LEDGER"


def test_a_person_with_two_ledgers_is_not_guessed_for():
    reply = _to_reply(base_row(ledger="Something new"), PEOPLE, LEDGERS,
                      {"VIHAR DVM": ["UK LEDGER", "Home"]})
    assert reply.drafts[0].entry.ledger == "Something new"


def test_a_known_ledger_is_never_overridden():
    reply = _to_reply(base_row(ledger="Home"), PEOPLE, LEDGERS,
                      {"VIHAR DVM": ["UK LEDGER"]})
    assert reply.drafts[0].entry.ledger == "Home"


def test_canonicalising_without_any_context_changes_nothing():
    reply = _to_reply(base_row())
    assert reply.drafts[0].entry.person == "VIHAR"
