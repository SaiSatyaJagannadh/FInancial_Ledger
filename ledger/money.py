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


#: Indian numbering: a lakh is 10^5 and a crore is 10^7. "25,00,000" is read as
#: "25 lakh", never as "2.5 million" — so the rupee scale is the Indian one and
#: the dollar scale is the western one.
_INR_SCALE = ((10_000_000, "crore"), (100_000, "lakh"), (1_000, "thousand"))
_USD_SCALE = ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand"))

#: Below this there is nothing to simplify — "₹900" is already the clearest way
#: to say ₹900.
COMPACT_FLOOR = {Currency.INR: 100_000, Currency.USD: 10_000}


def _trim(value: Decimal) -> str:
    """Two decimals at most, and no trailing zeroes: 25.00 -> 25, 9.50 -> 9.5."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def compact(minor: int, currency: Currency | str = DEFAULT_CURRENCY) -> str:
    """A short reading of a large amount: 2,50,00,000 paise -> "2.5 lakh".

    Returns "" when the figure is small enough to read as it stands, so callers
    can drop the hint entirely rather than printing something redundant.
    """
    currency = currency if isinstance(currency, Currency) else Currency(currency)
    units = abs(minor) / MINOR
    if units < COMPACT_FLOOR[currency]:
        return ""

    scale = _INR_SCALE if currency is Currency.INR else _USD_SCALE
    for size, name in scale:
        if units >= size:
            amount = _trim(Decimal(str(units)) / Decimal(size))
            sign = "-" if minor < 0 else ""
            return f"{sign}{amount} {name}"
    return ""


def spoken(minor: int, currency: Currency | str = DEFAULT_CURRENCY) -> str:
    """`compact` with the symbol in front, for showing beside an input."""
    short = compact(minor, currency)
    if not short:
        return ""
    currency = currency if isinstance(currency, Currency) else Currency(currency)
    return f"{currency.symbol}{short}"
