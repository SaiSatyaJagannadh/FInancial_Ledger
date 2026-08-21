"""Money helpers. Everything internal is signed integer minor units (cents)."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

#: Currencies whose minor unit is not 1/100. Extend as needed.
_EXPONENTS = {"JPY": 0, "KRW": 0, "VND": 0, "CLP": 0, "ISK": 0, "BHD": 3, "KWD": 3, "TND": 3}

_CLEAN = re.compile(r"[^\d.\-+]")


def exponent(currency: str) -> int:
    return _EXPONENTS.get(currency.upper(), 2)


def to_minor(amount: str | int | float | Decimal, currency: str = "USD") -> int:
    """Parse a human amount into signed minor units.

    Accepts "1,234.56", "$1234.56", "(50.00)" (accounting negative), "-12".
    Rounds half-up at the currency's minor unit, so 0.005 -> 1 cent, never 0.
    """
    if isinstance(amount, int) and not isinstance(amount, bool):
        dec = Decimal(amount)
    elif isinstance(amount, Decimal):
        dec = amount
    elif isinstance(amount, float):
        # str() first: Decimal(0.1) would carry the binary float error.
        dec = Decimal(str(amount))
    else:
        text = str(amount).strip()
        if not text:
            raise ValueError("empty amount")
        negative = text.startswith("(") and text.endswith(")")
        cleaned = _CLEAN.sub("", text)
        if cleaned in ("", "-", "+", "."):
            raise ValueError(f"not an amount: {amount!r}")
        try:
            dec = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"not an amount: {amount!r}") from exc
        if negative:
            dec = -abs(dec)

    scale = Decimal(10) ** exponent(currency)
    return int((dec * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_major(minor: int, currency: str = "USD") -> Decimal:
    """Minor units back to a Decimal major amount, for display and JSON."""
    return (Decimal(minor) / (Decimal(10) ** exponent(currency))).quantize(
        Decimal(1).scaleb(-exponent(currency))
    )


def format_money(minor: int, currency: str = "USD") -> str:
    return f"{to_major(minor, currency):,.{exponent(currency)}f} {currency.upper()}"
