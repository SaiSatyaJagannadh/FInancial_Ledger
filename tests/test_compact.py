"""Reading a large amount out loud, on the Indian scale for rupees."""

from __future__ import annotations

import pytest

from ledger.money import Currency, compact, spoken


@pytest.mark.parametrize("rupees,expected", [
    (25_00_000, "25 lakh"),        # the case that prompted this
    (1_00_000, "1 lakh"),
    (2_50_000, "2.5 lakh"),
    (9_05_689, "9.06 lakh"),
    (99_99_999, "100 lakh"),
    (1_00_00_000, "1 crore"),
    (1_23_45_678, "1.23 crore"),
    (10_00_00_000, "10 crore"),
])
def test_rupees_read_on_the_indian_scale(rupees, expected):
    assert compact(rupees * 100, Currency.INR) == expected


@pytest.mark.parametrize("rupees", [0, 1, 999, 25_000, 99_999])
def test_small_rupee_amounts_are_left_alone(rupees):
    """₹900 is already the clearest way to say ₹900."""
    assert compact(rupees * 100, Currency.INR) == ""


@pytest.mark.parametrize("dollars,expected", [
    (10_000, "10 thousand"),
    (42_500, "42.5 thousand"),
    (1_250_000, "1.25 million"),
    (3_000_000_000, "3 billion"),
])
def test_dollars_read_on_the_western_scale(dollars, expected):
    """A dollar figure is never described in lakhs."""
    got = compact(dollars * 100, Currency.USD)
    assert got == expected
    assert "lakh" not in got and "crore" not in got


@pytest.mark.parametrize("dollars", [0, 999, 9_999])
def test_small_dollar_amounts_are_left_alone(dollars):
    assert compact(dollars * 100, Currency.USD) == ""


def test_the_thresholds_differ_by_currency():
    """A lakh is the natural rupee break; ten thousand is the dollar one."""
    assert compact(50_000 * 100, Currency.INR) == ""
    assert compact(50_000 * 100, Currency.USD) == "50 thousand"


def test_trailing_zeroes_are_trimmed():
    assert compact(25_00_000 * 100, Currency.INR) == "25 lakh"      # not 25.00
    assert compact(9_50_000 * 100, Currency.INR) == "9.5 lakh"      # not 9.50


def test_negative_amounts_keep_their_sign():
    assert compact(-25_00_000 * 100, Currency.INR) == "-25 lakh"


def test_paise_do_not_shift_the_reading():
    assert compact(25_00_000 * 100 + 49, Currency.INR) == "25 lakh"


def test_a_string_currency_works_the_same():
    assert compact(25_00_000 * 100, "INR") == "25 lakh"


def test_spoken_puts_the_symbol_in_front():
    assert spoken(9_05_689 * 100, Currency.INR) == "₹9.06 lakh"
    assert spoken(42_500 * 100, Currency.USD) == "$42.5 thousand"


def test_spoken_is_empty_when_there_is_nothing_to_shorten():
    assert spoken(900 * 100, Currency.INR) == ""
