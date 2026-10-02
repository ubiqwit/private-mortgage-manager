from datetime import datetime, timezone

from app import timeutil


def test_local_today_differs_from_utc_late_evening(monkeypatch):
    # 9:30pm in Toronto on Oct 31 is already Nov 1 in UTC.
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 1, 1, 30, tzinfo=timezone.utc).astimezone(tz)

    monkeypatch.setattr(timeutil, "datetime", FakeDatetime)
    monkeypatch.setenv("PMM_TIMEZONE", "America/Toronto")
    assert timeutil.today().isoformat() == "2026-10-31"
    monkeypatch.setenv("PMM_TIMEZONE", "UTC")
    assert timeutil.today().isoformat() == "2026-11-01"


def test_utc_to_local(monkeypatch):
    monkeypatch.setenv("PMM_TIMEZONE", "America/Toronto")
    assert timeutil.utc_to_local(datetime(2026, 7, 1, 16, 0)) == datetime(2026, 7, 1, 12, 0)  # EDT
    assert timeutil.utc_to_local(datetime(2026, 1, 1, 16, 0)) == datetime(2026, 1, 1, 11, 0)  # EST
