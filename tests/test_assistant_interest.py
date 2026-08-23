"""The assistant proposing interest and groupings.

The model now has three things it can propose instead of one, and the rule
that matters is that they cannot be confused for each other. An interest
charge that arrives as a ledger entry would inflate "who owes me what" with
money nobody was handed — and once saved, the two are indistinguishable.

Everything here goes through `_to_reply`, the same function the live response
is parsed by. Nothing calls the network.
"""

from __future__ import annotations

import pytest

from ledger.assistant import _to_reply
from ledger.models import BY_CHAT
from ledger.money import Currency

PEOPLE = ["Chaitu Annaya Friend", "Vihar", "Nanna"]
LEDGERS = ["Family", "Bike loan"]


def interest_payload(**over) -> dict:
    row = {"date": "2026-08-01", "person": "Chaitu", "amount": "2000",
           "currency": "INR", "rate_percent": "2", "note": "Aug"}
    row.update(over)
    return {"interest": [row]}


# --------------------------------------------- interest is never an entry

def test_interest_becomes_a_charge_not_a_ledger_draft():
    reply = _to_reply(interest_payload(), people=PEOPLE)
    assert len(reply.charges) == 1
    assert reply.drafts == [], "an interest charge must never arrive as a ledger entry"


def test_a_charge_carries_what_the_model_said():
    charge = _to_reply(interest_payload(), people=PEOPLE).charges[0]
    assert charge.amount_minor == 2000_00
    assert charge.month_label == "Aug 2026"
    assert charge.rate_percent == 2.0
    assert charge.note == "Aug"


def test_a_charge_is_marked_as_coming_from_the_chat():
    assert _to_reply(interest_payload(), people=PEOPLE).charges[0].source == BY_CHAT


def test_the_person_is_snapped_onto_the_recorded_name():
    """The model writes "Chaitu"; the sheet holds "Chaitu Annaya Friend"."""
    charge = _to_reply(interest_payload(), people=PEOPLE).charges[0]
    assert charge.person == "Chaitu Annaya Friend"


@pytest.mark.parametrize("amount", ["0", "-500", "", "abc"])
def test_an_unusable_amount_is_rejected_rather_than_saved(amount):
    reply = _to_reply(interest_payload(amount=amount), people=PEOPLE)
    assert reply.charges == []
    assert reply.rejected, "the reason must be shown, not swallowed"


def test_a_charge_that_is_not_an_object_is_rejected():
    reply = _to_reply({"interest": ["two thousand"]}, people=PEOPLE)
    assert reply.charges == [] and reply.rejected


def test_the_currency_the_user_typed_beats_the_model():
    """Every model tested read "$250" as rupees. The user's words win."""
    reply = _to_reply(
        interest_payload(currency="INR"), people=PEOPLE,
        said="add $250 interest for Chaitu",
    )
    assert reply.charges[0].currency is Currency.USD


# ----------------------------------------------------------- groupings

def test_a_grouping_is_proposed_as_a_pair():
    reply = _to_reply(
        {"grouping": [{"person": "Chaitu", "under": "Vihar"}]}, people=PEOPLE
    )
    assert reply.groupings == [("Chaitu Annaya Friend", "Vihar")]
    assert reply.drafts == [] and reply.charges == []


def test_a_grouping_onto_an_unknown_person_is_refused():
    """It would make a group of one that never matches anybody."""
    reply = _to_reply(
        {"grouping": [{"person": "Chaitu", "under": "Someone Else"}]}, people=PEOPLE
    )
    assert reply.groupings == []
    assert any("not somebody in the ledger" in r for r in reply.rejected)


def test_grouping_somebody_under_themselves_is_refused():
    reply = _to_reply(
        {"grouping": [{"person": "Vihar", "under": "Vihar"}]}, people=PEOPLE
    )
    assert reply.groupings == [] and reply.rejected


def test_an_empty_parent_means_back_on_their_own():
    reply = _to_reply(
        {"grouping": [{"person": "Chaitu", "under": ""}]}, people=PEOPLE
    )
    assert reply.groupings == [("Chaitu Annaya Friend", "")]


# ------------------------------------------------- the old paths still work

def test_a_plain_entry_is_unaffected():
    reply = _to_reply(
        {"entries": [{"date": "2026-08-01", "person": "Vihar", "ledger": "Family",
                      "direction": "given", "amount": "500", "currency": "INR",
                      "note": ""}]},
        people=PEOPLE, ledgers=LEDGERS,
    )
    assert len(reply.drafts) == 1
    assert reply.charges == [] and reply.groupings == []
    assert reply.drafts[0].entry.amount_minor == 500_00


def test_a_question_still_comes_back_as_a_question():
    reply = _to_reply({"question": "Who did you give it to?"}, people=PEOPLE)
    assert reply.question and not reply.anything


def test_an_answer_still_comes_back_as_an_answer():
    reply = _to_reply({"answer": "Vihar owes you ₹50,000."}, people=PEOPLE)
    assert reply.answer and not reply.anything


def test_anything_reports_what_needs_a_decision():
    assert _to_reply(interest_payload(), people=PEOPLE).anything
    assert not _to_reply({"answer": "hello"}, people=PEOPLE).anything


# ------------------------------------------------------- what the model sees

def test_the_summary_marks_interest_as_separate():
    """If the summary let interest look like a balance, the model would add it
    into a total when asked "how much am I owed"."""
    from datetime import date

    from ledger.assistant import summarise
    from ledger.interest import Charge
    from ledger.models import Direction, Entry

    entries = [Entry(date=date(2026, 1, 1), person="Vihar", ledger="Family",
                     direction=Direction.given, amount_minor=50_000_00,
                     currency=Currency.INR, note="")]
    charges = [Charge(date=date(2026, 8, 1), person="Vihar", amount_minor=1_000_00)]
    text = summarise(entries, charges, {"Chaitu Annaya Friend": "Vihar"})

    assert "NOT part of any figure above" in text
    assert "GROUPS" in text and "comes under Vihar" in text


def test_the_summary_without_interest_reads_as_before():
    from datetime import date

    from ledger.assistant import summarise
    from ledger.models import Direction, Entry

    entries = [Entry(date=date(2026, 1, 1), person="Vihar", ledger="Family",
                     direction=Direction.given, amount_minor=50_000_00,
                     currency=Currency.INR, note="")]
    assert "INTEREST" not in summarise(entries)
    assert "GROUPS" not in summarise(entries)


# ------------------------------------------- a rate is applied in code

class TestARateWithNoAmount:
    """"charge Chaitu 2% this month" is the natural way to say it, and the
    model answers with a rate and no figure. That used to be rejected as a
    missing amount. The percentage is now applied to what the person still
    owes, in code — the model is never asked to do the arithmetic.
    """

    from datetime import date as _date

    from ledger.models import Direction, Entry

    OWES = [
        Entry(date=_date(2026, 1, 1), person="Chaitu", ledger="Loan",
              direction=Direction.given, amount_minor=50_000_00,
              currency=Currency.INR, note=""),
    ]

    def payload(self, **over) -> dict:
        row = {"date": "2026-08-01", "person": "Chaitu",
               "rate_percent": "2", "currency": "INR"}
        row.update(over)
        return {"interest": [row]}

    def test_the_amount_is_worked_out_from_the_rate(self):
        charge = _to_reply(self.payload(), people=["Chaitu"],
                           entries=self.OWES).charges[0]
        assert charge.amount_minor == 1_000_00, "2% of the ₹50,000 outstanding"
        assert charge.rate_percent == 2.0

    def test_an_explicit_amount_still_wins_over_the_rate(self):
        """If the user said a figure, that figure is what gets proposed."""
        charge = _to_reply(self.payload(amount="333"), people=["Chaitu"],
                           entries=self.OWES).charges[0]
        assert charge.amount_minor == 333_00

    def test_a_rate_against_someone_who_owes_nothing_says_so(self):
        reply = _to_reply(self.payload(), people=["Chaitu"], entries=[])
        assert reply.charges == []
        assert any("owe anything" in r for r in reply.rejected), reply.rejected

    def test_without_the_entries_it_cannot_guess(self):
        """No ledger to work from means no charge, not a made-up one."""
        reply = _to_reply(self.payload(), people=["Chaitu"])
        assert reply.charges == [] and reply.rejected

    def test_the_rate_is_charged_on_the_balance_at_that_date(self):
        """Charging for August must not know about a September repayment."""
        from datetime import date

        from ledger.models import Direction, Entry

        later = self.OWES + [
            Entry(date=date(2026, 9, 1), person="Chaitu", ledger="Loan",
                  direction=Direction.received, amount_minor=50_000_00,
                  currency=Currency.INR, note=""),
        ]
        charge = _to_reply(self.payload(), people=["Chaitu"],
                           entries=later).charges[0]
        assert charge.amount_minor == 1_000_00
