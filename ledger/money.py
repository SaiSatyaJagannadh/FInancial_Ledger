"""Money helpers. Everything internal is integer minor units (paise / cents).

Currencies are never summed together. A rupee total and a dollar total are
different quantities, and adding them would need an exchange rate that changes
daily — so the app keeps them apart rather than inventing one.
"""

from __future__ import annotations

import enum
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MINOR = 100
_STRIP = re.compile(r"[^\d.\-]")


class Currency(str, enum.Enum):
    INR = "INR"
    USD = "USD"

    @property
    def symbol(self) -> str:
        return {"INR": "₹", "USD": "$"}[self.value]

    @property
    def label(self) -> str:
        return {"INR": "Indian Rupees", "USD": "US Dollars"}[self.value]

    @property
    def flag(self) -> str:
        return {"INR": "🇮🇳", "USD": "🇺🇸"}[self.value]

    @property
    def minor_name(self) -> str:
        return {"INR": "paise", "USD": "cents"}[self.value]


#: Sheets written before currency existed are rupee ledgers.
DEFAULT_CURRENCY = Currency.INR

_SYMBOL_HINTS = {"₹": Currency.INR, "rs": Currency.INR, "inr": Currency.INR,
                 "$": Currency.USD, "usd": Currency.USD, "us$": Currency.USD}


def parse_currency(value: str | Currency | None) -> Currency:
    """Read a currency from a sheet cell. Blank means the pre-currency default."""
    if isinstance(value, Currency):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return DEFAULT_CURRENCY
    if text in _SYMBOL_HINTS:
        return _SYMBOL_HINTS[text]
    try:
        return Currency(text.upper())
    except ValueError as exc:
        raise ValueError(f"unknown currency: {value!r}") from exc


def to_minor(amount: str | int | float | Decimal) -> int:
    """Parse a human amount into integer minor units.

    Accepts "1,234.56", "₹1234.56", "$500", "Rs 500". Rounds half-up at the
    minor unit, so 0.005 becomes 1 rather than silently vanishing.
    """
    if isinstance(amount, bool):
        raise ValueError("not an amount: bool")
    if isinstance(amount, int):
        dec = Decimal(amount)
    elif isinstance(amount, Decimal):
        dec = amount
    elif isinstance(amount, float):
        # str() first: Decimal(0.1) would carry the binary float error.
        dec = Decimal(str(amount))
    else:
        cleaned = _STRIP.sub("", str(amount).strip())
        if cleaned in ("", "-", ".", "-."):
            raise ValueError(f"not an amount: {amount!r}")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"not an amount: {amount!r}") from exc

    return int((dec * MINOR).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_major(minor: int) -> Decimal:
    return (Decimal(minor) / MINOR).quantize(Decimal("0.01"))


def group_indian(digits: str) -> str:
    """Last three digits, then twos: 676800 -> 6,76,800."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def group_western(digits: str) -> str:
    return f"{int(digits):,}"


def format_money(minor: int, currency: Currency | str = DEFAULT_CURRENCY,
                 decimals: bool = True) -> str:
    """₹6,76,800.00 for rupees (lakh grouping), $676,800.00 for dollars."""
    currency = parse_currency(currency)
    negative = minor < 0
    whole, frac = divmod(abs(minor), MINOR)

    digits = str(whole)
    grouped = group_indian(digits) if currency is Currency.INR else group_western(digits)

    text = f"{currency.symbol}{grouped}.{frac:02d}" if decimals else f"{currency.symbol}{grouped}"
    return f"-{text}" if negative else text


def format_compact(minor: int, currency: Currency | str = DEFAULT_CURRENCY) -> str:
    """Short form for axes. Rupees read in lakh/crore; dollars in K/M."""
    currency = parse_currency(currency)
    major = abs(minor) // MINOR
    sign = "-" if minor < 0 else ""
    symbol = currency.symbol

    if currency is Currency.INR:
        if major >= 10_000_000:
            return f"{sign}{symbol}{major / 10_000_000:.1f}Cr"
        if major >= 100_000:
            return f"{sign}{symbol}{major / 100_000:.1f}L"
    else:
        if major >= 1_000_000:
            return f"{sign}{symbol}{major / 1_000_000:.1f}M"
    if major >= 1_000:
        return f"{sign}{symbol}{major / 1_000:.0f}k"
    return f"{sign}{symbol}{major}"
