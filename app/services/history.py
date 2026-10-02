"""Per-due-date payment history: was each scheduled payment paid on time, late, or missed?"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from ..models import ARREARS_GRACE_DAYS, REGULAR_PAYMENT_TYPES


@dataclass
class DueStatus:
    due: date
    amount: Decimal
    status: str  # on_time | late | missed | pending | upcoming
    paid_on: date | None = None

    @property
    def days_late(self):
        return (self.paid_on - self.due).days if self.paid_on else None


def payment_history(m, as_of: date, prime=None, upcoming: int = 1) -> list[DueStatus]:
    """Regular payments are applied oldest-due-first (an NSF reverses its amount on its date)."""
    dues = [d for d in m.due_dates(end=as_of) if m.balance(as_of=d) > 0]
    future = [d for d in m.due_dates(start=as_of + timedelta(days=1)) if m.balance() > 0][:upcoming]
    amounts = [m.scheduled_payment(d, prime) for d in dues]

    events = sorted(
        ((t.date, Decimal(str(t.amount)) * (-1 if t.type == "nsf" else 1))
         for t in m.transactions if t.type in REGULAR_PAYMENT_TYPES and t.date <= as_of),
        key=lambda e: e[0],
    )
    thresholds, running = [], Decimal(0)
    for a in amounts:
        running += a
        thresholds.append(running)

    covered_on: list[date | None] = [None] * len(dues)
    total = Decimal(0)
    for when, amount in events:
        total += amount
        for i, need in enumerate(thresholds):
            if total >= need and covered_on[i] is None:
                covered_on[i] = when
            elif total < need:
                covered_on[i] = None  # an NSF took it back

    out = []
    for i, d in enumerate(dues):
        paid = covered_on[i]
        if paid is not None:
            status = "on_time" if paid <= d + timedelta(days=ARREARS_GRACE_DAYS) else "late"
        else:
            status = "missed" if as_of > d + timedelta(days=ARREARS_GRACE_DAYS) else "pending"
        out.append(DueStatus(d, amounts[i], status, paid))
    out += [DueStatus(d, m.scheduled_payment(d, prime), "upcoming") for d in future]
    return out


def summary(rows: list[DueStatus]) -> dict:
    counts = {k: 0 for k in ("on_time", "late", "missed", "pending", "upcoming")}
    for r in rows:
        counts[r.status] += 1
    settled = counts["on_time"] + counts["late"] + counts["missed"]
    counts["on_time_pct"] = round(counts["on_time"] * 100 / settled) if settled else None
    return counts
