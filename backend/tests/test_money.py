from decimal import Decimal

import pytest

from app.money import format_money, to_major, to_minor


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.56", 123456),
        ("$1234.56", 123456),
        ("(50.00)", -5000),   # accounting-style negative
        ("-12", -1200),
        ("0.005", 1),         # half-up, never silently truncated to 0
        ("  7.10  ", 710),
        (0.1, 10),            # float goes through str(), no binary drift
        (Decimal("3.333"), 333),
        (5, 500),
    ],
)
def test_to_minor_parses(raw, expected):
    assert to_minor(raw) == expected


def test_zero_decimal_currency():
    assert to_minor("1234", "JPY") == 1234
    assert to_major(1234, "JPY") == Decimal("1234")


@pytest.mark.parametrize("bad", ["", "   ", "abc", "$", "-"])
def test_to_minor_rejects_garbage(bad):
    with pytest.raises(ValueError):
        to_minor(bad)


def test_round_trip_is_lossless():
    for cents in (-123456, -1, 0, 1, 99, 100, 999999):
        assert to_minor(to_major(cents)) == cents


def test_format():
    assert format_money(-5000) == "-50.00 USD"
    assert format_money(123456) == "1,234.56 USD"
