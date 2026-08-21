from decimal import Decimal

import pytest

from ledger.money import format_compact, format_inr, to_paise, to_rupees


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,234.56", 123456),
        ("₹1234.56", 123456),
        ("Rs 500", 50000),
        ("  7.10  ", 710),
        ("0.005", 1),      # half-up: never silently lost
        ("-250", -25000),
        (0.1, 10),         # float goes via str(), no binary drift
        (Decimal("3.333"), 333),
        (500, 50000),
    ],
)
def test_to_paise(raw, expected):
    assert to_paise(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", "abc", "₹", "-", "."])
def test_to_paise_rejects_garbage(bad):
    with pytest.raises(ValueError):
        to_paise(bad)


def test_to_paise_rejects_bool():
    # bool is an int subclass; True would quietly become ₹0.01.
    with pytest.raises(ValueError):
        to_paise(True)


def test_round_trip_is_lossless():
    for paise in (-123456, -1, 0, 1, 99, 100, 67_680_000):
        assert to_paise(to_rupees(paise)) == paise


@pytest.mark.parametrize(
    "paise,text",
    [
        (67_680_000, "₹6,76,800.00"),   # Indian grouping: lakh, not thousand
        (30_400_000, "₹3,04,000.00"),
        (37_280_000, "₹3,72,800.00"),
        (100_000, "₹1,000.00"),
        (99_999, "₹999.99"),
        (99, "₹0.99"),
        (0, "₹0.00"),
        (-5_000, "-₹50.00"),
        (10_000_000_00, "₹1,00,00,000.00"),   # 1 crore
        (1_000_000_00, "₹10,00,000.00"),      # 10 lakh
    ],
)
def test_format_inr(paise, text):
    assert format_inr(paise) == text


def test_format_inr_crore_grouping():
    assert format_inr(to_paise(12_345_678)) == "₹1,23,45,678.00"


@pytest.mark.parametrize(
    "paise,text",
    [(12_000_000, "₹1.2L"), (4_500_000, "₹45k"), (50_000, "₹500"), (-4_500_000, "-₹45k")],
)
def test_format_compact(paise, text):
    assert format_compact(paise) == text
