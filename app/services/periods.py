"""Closing the books: once a month has gone to the accountant, lock it against changes."""
from __future__ import annotations

from datetime import date

from ..models import Setting

KEY = "books_closed_through"


class PeriodClosed(ValueError):
    pass


def closed_through() -> date | None:
    raw = Setting.get(KEY)
    return date.fromisoformat(raw) if raw else None


def is_closed(day: date | None) -> bool:
    end = closed_through()
    return bool(day and end and day <= end)


def ensure_open(*days, action="change"):
    """Raise :class:`PeriodClosed` if any of ``days`` falls in a closed month."""
    end = closed_through()
    if not end:
        return
    for day in days:
        if day and day <= end:
            raise PeriodClosed(
                f"Can't {action}: the books are closed through {end:%B %Y} ({day:%b %d, %Y} is in a closed month). "
                "An admin can reopen the month on the month-end report page."
            )


def set_closed_through(day: date | None):
    Setting.set(KEY, day.isoformat() if day else None)
