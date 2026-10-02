"""The business runs on the owner's local calendar, not the cloud server's (UTC) clock.

Set PMM_TIMEZONE (default America/Toronto) so "today", due dates and arrears roll over
at local midnight. Stored timestamps (created_at, audit log) stay in UTC.
"""
import os
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def tz() -> ZoneInfo:
    return ZoneInfo(os.environ.get("PMM_TIMEZONE", "America/Toronto"))


def today() -> date:
    return datetime.now(tz()).date()


def now() -> datetime:
    """Local wall-clock time (naive) for display."""
    return datetime.now(tz()).replace(tzinfo=None)


def utc_to_local(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone(tz()).replace(tzinfo=None)


def utcnow() -> datetime:
    """Naive UTC timestamp for storage (datetime.utcnow() is deprecated from Python 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
