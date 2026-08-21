"""A month-arithmetic helper, so the app needs no date library beyond stdlib."""

from __future__ import annotations

from datetime import date


def months_ago(today: date, months: int) -> date:
    """The same day-of-month `months` back, clamped to a real date.

    31 March minus 1 month is 28/29 February, not an error.
    """
    total = today.year * 12 + (today.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    day = min(today.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year + (month // 12), (month % 12) + 1, 1) - date(year, month, 1)).days
