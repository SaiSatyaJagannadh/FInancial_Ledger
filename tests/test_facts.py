"""The computed answers.

Two things are being tested, and the second is the more important one: that
the right question gets an exact answer, and that a question this cannot read
gets `None` so the assistant falls through to the model. Answering the wrong
question instantly is worse than answering the right one slowly.
"""

from __future__ import annotations

from datetime import date

import pytest

from ledger import facts
from ledger.compute import by_person, totals
from ledger.models import Direction, Entry
from ledger.money import Currency, format_money


def entry(minor, *, person="Ravi", ledger="Loan", direction=Direction.given,
          currency=Currency.INR, when=date(2026, 1, 5), note="", row=None) -> Entry:
    return Entry(date=when, person=person, ledger=ledger, direction=direction,
                 amount_minor=minor, currency=currency, note=note, row=row)


@pytest.fixture
def book() -> list[Entry]:
    """Ravi owes 3,000; Amma is square; Sam owes $400."""
    return [
        entry(500_000, person="Ravi", when=date(2026, 1, 5)),
        entry(200_000, person="Ravi", direction=Direction.received, when=date(2026, 3, 9)),
        entry(100_000, person="Amma", ledger="Home", when=date(2026, 2, 1)),
        entry(100_000, person="Amma", ledger="Home",
              direction=Direction.received, when=date(2026, 2, 20)),
        entry(40_000, person="Sam", ledger="Trip", currency=Currency.USD,
              when=date(2026, 4, 2)),
    ]


# ------------------------------------------------------------ the refusals

@pytest.mark.parametrize("question", [
    "hello",
    "what do you think of my spending habits",
    "should i lend him more money",
    "tell me a joke",
    "gave 5000 to ravi today",          # this is an entry, not a question
    "",
    "   ",
])
def test_it_declines_what_it_cannot_compute(question, book):
    """None is the contract for 'I am not sure' — the assistant then asks the
    model exactly as it did before this module existed."""
    assert facts.answer(question, book) is None


def test_two_people_in_one_question_is_declined(book):
    """"who owes more, Ravi or Amma" is a comparison this does not do, and
    answering about whichever name matched first would be worse than silence."""
    assert facts.answer("how much do ravi and amma owe me", book) is None


def test_an_unknown_person_is_declined(book):
    assert facts.answer("how much does Kavita owe me", book) is None


def test_owe_is_not_matched_inside_another_word(book):
    """Substring matching found "owe" in "owner" and answered a question about
    something else entirely."""
    assert not facts._has("who is the owner of this", "owe")


# ------------------------------------------------------------- the answers

def test_a_person_balance_matches_compute(book):
    reply = facts.answer("how much does Ravi owe me", book)
    mine = [e for e in book if e.currency is Currency.INR]
    expected = next(s for s in by_person(mine, Currency.INR) if s.person == "Ravi")
    assert format_money(expected.net_minor, Currency.INR) in reply
    assert "Ravi" in reply and "owes you" in reply


def test_the_arithmetic_is_shown_not_just_the_result(book):
    """A figure you cannot check is a figure you have to trust."""
    reply = facts.answer("how much does Ravi owe me", book)
    assert "given" in reply and "received" in reply
    assert format_money(500_000, Currency.INR) in reply
    assert format_money(200_000, Currency.INR) in reply


def test_a_settled_person_is_reported_as_settled(book):
    reply = facts.answer("what about Amma", book)
    assert "settled" in reply.lower()


def test_the_ranking_puts_the_largest_first(book):
    reply = facts.answer("who owes me the most", book)
    assert reply.index("Ravi") < reply.index("Sam")
    assert "Amma" not in reply  # settled, so not chased


def test_totals_match_compute(book):
    reply = facts.answer("what is the total i have lent", book)
    inr = totals([e for e in book if e.currency is Currency.INR], Currency.INR)
    assert format_money(inr.net_minor, Currency.INR) in reply


def test_counts_distinguish_entries_people_and_ledgers(book):
    """Four rupee entries across Ravi and Amma; the dollar row is counted apart."""
    assert "4 entries" in facts.answer("how many entries", book)
    assert "2 people" in facts.answer("how many people are there", book)
    assert "ledgers" in facts.answer("how many ledgers", book)


def test_last_activity_names_the_actual_entry(book):
    reply = facts.answer("when did i last give Ravi", book)
    assert "09 Mar 2026" in reply
    assert "got back" in reply          # the 09 Mar row is a repayment


def test_largest_finds_the_biggest_single_entry(book):
    reply = facts.answer("what is the biggest entry", book)
    assert format_money(500_000, Currency.INR) in reply
    assert "Ravi" in reply


def test_open_ledgers_excludes_the_settled_one(book):
    reply = facts.answer("which ledgers are still open", book)
    assert "Ravi" in reply and "Sam" in reply
    assert "Home" not in reply          # Amma's ledger nets to zero


def test_everything_settled_says_so():
    settled = [
        entry(100_000, person="Amma"),
        entry(100_000, person="Amma", direction=Direction.received),
    ]
    assert "settled" in facts.answer("which ledgers are open", settled).lower()


# ------------------------------------------------- the house rules still hold

def test_currencies_are_reported_separately_never_added(book):
    """₹3,00,000 + $400 is not a number. Both appear; no third figure does."""
    reply = facts.answer("what is the total", book)
    assert Currency.INR.label in reply and Currency.USD.label in reply
    assert format_money(40_000, Currency.USD) in reply


def test_a_dollar_entry_never_moves_a_rupee_answer(book):
    before = facts.answer("how much does Ravi owe me", book)
    with_more_dollars = book + [entry(999_00, person="Sam", currency=Currency.USD)]
    assert facts.answer("how much does Ravi owe me", with_more_dollars) == before


def test_an_empty_ledger_answers_rather_than_crashing():
    assert "empty" in facts.answer("who owes me", []).lower()


def test_a_name_snaps_onto_the_recorded_one():
    """"ravi" is what people type; "Ravi Kumar" is what the sheet holds."""
    book = [entry(500_00, person="Ravi Kumar")]
    reply = facts.answer("what about ravi", book)
    assert reply is not None and "Ravi Kumar" in reply


def test_an_ambiguous_abbreviation_is_left_to_the_model():
    """"ravi" fits two recorded people, so this must not pick one."""
    book = [entry(500_00, person="Ravi Kumar"), entry(500_00, person="Ravi Sharma")]
    assert facts.answer("what about ravi", book) is None


def test_a_person_question_is_not_answered_as_a_grand_total(book):
    """"how much does Ravi owe me" contains "how much"; it must still be read
    as a question about Ravi."""
    reply = facts.answer("how much does Ravi owe me", book)
    assert "Ravi" in reply
    assert "entries and" not in reply    # the totals shape's wording


def test_answering_needs_no_network(book, monkeypatch):
    """The whole point. If this ever reaches requests, it is not a fast path."""
    import requests

    def forbidden(*_a, **_kw):
        raise AssertionError("facts.answer must not make a network call")

    monkeypatch.setattr(requests, "post", forbidden)
    monkeypatch.setattr(requests, "get", forbidden)
    assert facts.answer("who owes me the most", book)
