"""Money helpers. Everything internal is integer paise; ₹ appears only on screen."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE = 100
_STRIP = re.compile(r"[^\d.\-]")


def to_paise(amount: str | int | float | Decimal) -> int:
    """Parse a human amount into integer paise.

    Accepts "1,234.56", "₹1234.56", "Rs 500", "1.5". Rounds half-up at the
    paisa, so 0.005 becomes 1 paisa rather than silently vanishing.
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

    return int((dec * PAISE).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_rupees(paise: int) -> Decimal:
    return (Decimal(paise) / PAISE).quantize(Decimal("0.01"))


def format_inr(paise: int, decimals: bool = True) -> str:
    """Format as ₹ with Indian digit grouping (lakh/crore), e.g. ₹6,76,800.00.

    Python's own thousands separator groups in threes all the way up, which is
    not how rupee amounts are written.
    """
    negative = paise < 0
    whole, frac = divmod(abs(paise), PAISE)

    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        # After the first three digits, Indian grouping is in twos.
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        grouped = ",".join(groups) + "," + tail
    else:
        grouped = digits

    text = f"₹{grouped}.{frac:02d}" if decimals else f"₹{grouped}"
    return f"-{text}" if negative else text


def format_compact(paise: int) -> str:
    """Short form for chart axes: ₹1.2L, ₹45k."""
    rupees = abs(paise) // PAISE
    sign = "-" if paise < 0 else ""
    if rupees >= 10_000_000:
        return f"{sign}₹{rupees / 10_000_000:.1f}Cr"
    if rupees >= 100_000:
        return f"{sign}₹{rupees / 100_000:.1f}L"
    if rupees >= 1_000:
        return f"{sign}₹{rupees / 1_000:.0f}k"
    return f"{sign}₹{rupees}"
