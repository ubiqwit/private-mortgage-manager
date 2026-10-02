"""Portfolio-level statistics for the dashboard."""
from __future__ import annotations

from collections import OrderedDict, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from ..models import OPEN_STATUSES, PROPERTY_TYPES, Mortgage, MortgageTransaction
from . import calc

ZERO = Decimal("0.00")
LTV_BUCKETS = [("< 50%", 50), ("50–65%", 65), ("65–75%", 75), ("75–80%", 80), ("80–90%", 90), ("90%+", None)]


def month_bounds(d: date):
    start = d.replace(day=1)
    return start, start + relativedelta(months=1) - timedelta(days=1)


def _weighted(pairs):
    total = sum((w for w, _ in pairs), ZERO)
    if not total:
        return None
    return calc.money(sum((w * v for w, v in pairs), ZERO) / total)


def dashboard_stats(today: date | None = None, prime=None) -> dict:
    today = today or date.today()
    all_mortgages = Mortgage.query.all()
    book = [m for m in all_mortgages if m.status in OPEN_STATUSES]

    rows = []
    for m in book:
        bal = m.balance()
        rate = m.effective_rate(prime)
        rows.append(dict(
            m=m, balance=bal, rate=rate, cltv=m.combined_ltv(),
            monthly_interest=calc.monthly_interest(bal, rate, m.compounding) * Decimal(str(m.ownership_pct or 100)) / 100,
            arrears=m.arrears(today, prime), payment=m.regular_payment(prime),
        ))

    total_balance = sum((r["balance"] for r in rows), ZERO)
    monthly_interest = calc.money(sum((r["monthly_interest"] for r in rows), ZERO))

    # This month: scheduled vs collected regular payments.
    m_start, m_end = month_bounds(today)
    expected_month = ZERO
    for r in rows:
        expected_month += r["payment"] * len(r["m"].due_dates(start=m_start, end=m_end))
    collected_month = ZERO
    income_month = ZERO
    for m in all_mortgages:
        collected_month += m.regular_received(m_end) - m.regular_received(m_start - timedelta(days=1))
        inc = m.income_between(m_start, m_end)
        income_month += inc["interest"] + inc["fees"]

    # Year to date income (cash basis).
    y_start = date(today.year, 1, 1)
    ytd = {"interest": ZERO, "fees": ZERO, "principal": ZERO}
    for m in all_mortgages:
        inc = m.income_between(y_start, today)
        for k in ytd:
            ytd[k] += inc[k]

    # Income by month, trailing 12 months.
    months = [(today.replace(day=1) - relativedelta(months=i)) for i in range(11, -1, -1)]
    by_month = OrderedDict((mo.strftime("%Y-%m"), {"interest": ZERO, "fees": ZERO, "principal": ZERO}) for mo in months)
    first = months[0]
    for t in MortgageTransaction.query.filter(MortgageTransaction.date >= first, MortgageTransaction.date <= today):
        key = t.date.strftime("%Y-%m")
        if key in by_month and t.type != "advance":
            by_month[key]["interest"] += Decimal(str(t.interest or 0))
            by_month[key]["fees"] += Decimal(str(t.fees or 0))
            by_month[key]["principal"] += Decimal(str(t.principal or 0))
    income_chart = {
        "labels": [date.fromisoformat(k + "-01").strftime("%b %Y") for k in by_month],
        "interest": [float(v["interest"]) for v in by_month.values()],
        "fees": [float(v["fees"]) for v in by_month.values()],
        "principal": [float(v["principal"]) for v in by_month.values()],
    }

    # Maturity ladder: overdue + next 8 quarters.
    q_start = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    ladder = OrderedDict()
    ladder["Overdue"] = ZERO
    for i in range(8):
        qs = q_start + relativedelta(months=3 * i)
        ladder[f"Q{(qs.month - 1) // 3 + 1} {qs.year}"] = ZERO
    ladder["Later"] = ZERO
    for r in rows:
        md = r["m"].maturity_date
        if md < today:
            ladder["Overdue"] += r["balance"]
            continue
        idx = (md.year - q_start.year) * 4 + (md.month - 1) // 3 - (q_start.month - 1) // 3
        key = list(ladder)[1 + idx] if idx < 8 else "Later"
        ladder[key] += r["balance"]
    if not ladder["Overdue"]:
        del ladder["Overdue"]
    if not ladder["Later"]:
        del ladder["Later"]

    by_position = defaultdict(lambda: ZERO)
    by_type = defaultdict(lambda: ZERO)
    by_city = defaultdict(lambda: ZERO)
    ltv = OrderedDict((label, ZERO) for label, _ in LTV_BUCKETS)
    ltv_unknown = ZERO
    for r in rows:
        m = r["m"]
        by_position[m.position_label + " mortgages"] += r["balance"]
        by_type[dict(PROPERTY_TYPES).get(m.property_type, "Other")] += r["balance"]
        by_city[(m.property_city or "Unknown").strip().title()] += r["balance"]
        if r["cltv"] is None:
            ltv_unknown += r["balance"]
        else:
            for label, upper in LTV_BUCKETS:
                if upper is None or r["cltv"] < upper:
                    ltv[label] += r["balance"]
                    break
    cities = sorted(by_city.items(), key=lambda kv: kv[1], reverse=True)
    if len(cities) > 6:
        cities = cities[:5] + [("Other", sum((v for _, v in cities[5:]), ZERO))]

    largest = max(rows, key=lambda r: r["balance"], default=None)

    # Things that need attention.
    alerts = []
    for r in rows:
        m = r["m"]
        if r["arrears"] > 0:
            alerts.append(("critical", m, f"In arrears ${calc.money(r['arrears']):,.2f} (about {int(r['arrears'] / r['payment']) if r['payment'] else 0} payment(s))"))
        dtm = m.days_to_maturity(today)
        if dtm < 0:
            alerts.append(("critical", m, f"Matured {-dtm} days ago — renew or collect payout"))
        elif dtm <= 90:
            alerts.append(("warning", m, f"Matures in {dtm} days ({m.maturity_date:%b %d, %Y}) — start renewal discussion"))
        if m.insurance_expiry and m.insurance_expiry <= today + timedelta(days=30):
            past = m.insurance_expiry < today
            alerts.append(("critical" if past else "warning", m,
                           f"Property insurance {'expired' if past else 'expires'} {m.insurance_expiry:%b %d, %Y} — request proof of renewal"))
        if r["cltv"] is not None and r["cltv"] > 85:
            alerts.append(("warning", m, f"Combined LTV {r['cltv']}% — thin equity cushion"))
        if m.appraisal_date and m.appraisal_date < today - relativedelta(years=2):
            alerts.append(("info", m, f"Appraisal is from {m.appraisal_date:%b %Y} — value may be stale"))
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (order[a[0]], a[1].reference))

    # Payments due in the next 14 days.
    upcoming = []
    for r in rows:
        for d in r["m"].due_dates(start=today, end=today + timedelta(days=30)):
            upcoming.append((d, r["m"], r["payment"]))
    upcoming.sort(key=lambda u: (u[0], u[1].reference))

    recent = (MortgageTransaction.query.order_by(MortgageTransaction.date.desc(), MortgageTransaction.id.desc())
              .limit(8).all())

    return dict(
        count=len(rows),
        total_balance=total_balance,
        avg_loan=calc.money(total_balance / len(rows)) if rows else ZERO,
        wavg_rate=_weighted([(r["balance"], r["rate"]) for r in rows]),
        wavg_cltv=_weighted([(r["balance"], r["cltv"]) for r in rows if r["cltv"] is not None]),
        monthly_interest=monthly_interest,
        annual_interest=calc.money(monthly_interest * 12),
        expected_month=calc.money(expected_month),
        collected_month=calc.money(collected_month),
        income_month=calc.money(income_month),
        collection_pct=(calc.money(collected_month / expected_month * 100) if expected_month else None),
        ytd={k: calc.money(v) for k, v in ytd.items()},
        arrears_total=calc.money(sum((r["arrears"] for r in rows), ZERO)),
        arrears_count=sum(1 for r in rows if r["arrears"] > 0),
        maturing_90=[r for r in rows if 0 <= r["m"].days_to_maturity(today) <= 90],
        largest=largest,
        largest_pct=(calc.money(largest["balance"] / total_balance * 100) if largest and total_balance else None),
        income_chart=income_chart,
        ladder={"labels": list(ladder), "values": [float(v) for v in ladder.values()]},
        by_position=sorted(by_position.items()),
        by_type=sorted(by_type.items(), key=lambda kv: kv[1], reverse=True),
        by_city=cities,
        ltv={"labels": list(ltv), "values": [float(v) for v in ltv.values()], "unknown": ltv_unknown},
        alerts=alerts,
        upcoming=upcoming,
        recent=recent,
        month_label=today.strftime("%B %Y"),
    )
