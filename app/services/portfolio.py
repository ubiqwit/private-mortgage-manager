"""Portfolio-level figures for the dashboard."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from ..models import OPEN_STATUSES, Mortgage
from ..timeutil import today as local_today
from . import calc

ZERO = Decimal("0.00")


def month_bounds(d: date):
    start = d.replace(day=1)
    return start, start + relativedelta(months=1) - timedelta(days=1)


def dashboard_stats(today: date | None = None, prime=None) -> dict:
    today = today or local_today()
    all_mortgages = Mortgage.query.all()
    book = [m for m in all_mortgages if m.status in OPEN_STATUSES]

    rows = []
    for m in book:
        bal = m.balance()
        rows.append(dict(
            m=m, balance=bal, arrears=m.arrears(today, prime), payment=m.regular_payment(prime),
            monthly_interest=calc.monthly_interest(bal, m.effective_rate(prime), m.compounding),
        ))

    # This month: regular payments scheduled vs received.
    m_start, m_end = month_bounds(today)
    expected_month = sum((r["m"].scheduled_total(start=m_start, end=m_end, prime=prime) for r in rows), ZERO)
    collected_month = sum((m.regular_received(m_end) - m.regular_received(m_start - timedelta(days=1))
                           for m in all_mortgages), ZERO)

    # Income received so far this year (cash basis).
    ytd = {"interest": ZERO, "fees": ZERO}
    for m in all_mortgages:
        inc = m.income_between(date(today.year, 1, 1), today)
        ytd["interest"] += inc["interest"]
        ytd["fees"] += inc["fees"]

    # What needs attention: missed payments and maturities (renew or collect the payout).
    alerts = []
    for r in rows:
        m = r["m"]
        if r["arrears"] > 0:
            behind = int(r["arrears"] / r["payment"]) if r["payment"] else 0
            alerts.append(("critical", m, f"In arrears ${calc.money(r['arrears']):,.2f} (about {behind} payment(s))"))
        dtm = m.days_to_maturity(today)
        if dtm < 0:
            alerts.append(("critical", m, f"Matured {-dtm} days ago — renew or collect payout"))
        elif dtm <= 90:
            alerts.append(("warning", m, f"Matures in {dtm} days ({m.maturity_date:%b %d, %Y}) — start renewal discussion"))
    alerts.sort(key=lambda a: (a[0] != "critical", a[1].maturity_date))

    return dict(
        count=len(rows),
        total_balance=sum((r["balance"] for r in rows), ZERO),
        monthly_interest=calc.money(sum((r["monthly_interest"] for r in rows), ZERO)),
        expected_month=calc.money(expected_month),
        collected_month=calc.money(collected_month),
        collection_pct=(calc.money(collected_month / expected_month * 100) if expected_month else None),
        ytd={k: calc.money(v) for k, v in ytd.items()},
        arrears_total=calc.money(sum((r["arrears"] for r in rows), ZERO)),
        arrears_count=sum(1 for r in rows if r["arrears"] > 0),
        alerts=alerts,
        month_label=today.strftime("%B %Y"),
    )
