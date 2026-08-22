"""Reading the currency from the user's own words.

Every model tested got "gave amma $250" wrong and called it rupees. The text
says which currency it is, so this does not need a model at all.
"""

from __future__ import annotations

import pytest

from ledger.assistant import _to_reply, currency_hint
from ledger.money import Currency


@pytest.mark.parametrize("text", [
    "gave amma $250 today",
    "gave amma 250 dollars",
    "gave 1 dollar",
    "gave amma USD 250",
    "paid $1,200.50 for the flight",
])
def test_dollars_are_recognised(text):
    assert currency_hint(text) is Currency.USD


@pytest.mark.parametrize("text", [
    "gave ravi 2500 rupees",
    "gave ravi ₹2500",
    "rs. 500 to amma",
    "Rs 500",
    "gave 2 lakh",
    "gave 3 crore",
    "INR 900",
])
def test_rupees_are_recognised(text):
    assert currency_hint(text) is Currency.INR


def test_rs_does_not_match_inside_dollars():
    """The bug this guards: "rs" is a substring of "dollars", so a plain
    containment test found both currencies and gave up."""
    assert currency_hint("gave amma 250 dollars") is Currency.USD


@pytest.mark.parametrize("text", ["gave 500 today", "", None, "gave amma some money"])
def test_silence_is_not_a_guess(text):
    assert currency_hint(text) is None


def test_a_mixed_message_is_left_to_the_model():
    """Correcting a message that names both would be a coin flip."""
    assert currency_hint("$250 and ₹300 on the same day") is None


def test_the_hint_overrides_what_the_model_said():
    payload = {"entries": [{
        "date": "2026-01-05", "person": "Amma", "ledger": "Home",
        "direction": "given", "amount": "250", "currency": "INR",
    }]}
    reply = _to_reply(payload, ["Amma"], ["Home"], said="gave amma $250 today")
    assert reply.drafts[0].entry.currency is Currency.USD


def test_no_hint_leaves_the_models_choice_alone():
    payload = {"entries": [{
        "date": "2026-01-05", "person": "Amma", "ledger": "Home",
        "direction": "given", "amount": "250", "currency": "USD",
    }]}
    reply = _to_reply(payload, ["Amma"], ["Home"], said="gave amma 250 today")
    assert reply.drafts[0].entry.currency is Currency.USD
