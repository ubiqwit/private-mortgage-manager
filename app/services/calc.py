"""Pure mortgage maths — no database access, so it is easy to test.

Conventions
-----------
* Rates are annual nominal percentages (``12.0`` means 12%).
* ``compounding`` is how often the quoted rate compounds: ``monthly``,
  ``semi_annual`` (the Canadian standard for blended fixed-rate loans) or ``annual``.
* Money is ``Decimal`` rounded to cents at each scheduled payment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from dateutil.relativedelta import relativedelta

CENT = Decimal("0.01")

FREQUENCIES = {
    "monthly": 12,
    "semi_monthly": 24,
    "biweekly": 26,
    "weekly": 52,
    "quarterly": 4,
    "annually": 1,
}
COMPOUNDING = {"monthly": 12, "semi_annual": 2, "annual": 1}


def money(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def periods_per_year(frequency: str) -> int:
    return FREQUENCIES[frequency]


def periodic_rate(annual_pct, compounding: str, frequency: str) -> Decimal:
    """Effective rate per payment period for a nominal rate with the given compounding."""
    annual = Decimal(str(annual_pct or 0)) / 100
    if annual == 0:
        return Decimal(0)
    c = COMPOUNDING[compounding]
    f = FREQUENCIES[frequency]
    # (1 + r/c)^(c/f) - 1, evaluated in float then brought back to Decimal.
    return Decimal(str((1 + float(annual) / c) ** (c / f) - 1))


def monthly_interest(balance, annual_pct, compounding="monthly") -> Decimal:
    """Interest earned in one month on ``balance`` (used for month-end accruals)."""
    return money(Decimal(str(balance or 0)) * periodic_rate(annual_pct, compounding, "monthly"))


def blended_payment(principal, annual_pct, compounding, frequency, amortization_months) -> Decimal:
    """Level blended principal + interest payment."""
    principal = Decimal(str(principal or 0))
    n = round(int(amortization_months) * periods_per_year(frequency) / 12)
    if n <= 0:
        return money(principal)
    r = periodic_rate(annual_pct, compounding, frequency)
    if r == 0:
        return money(principal / n)
    return money(principal * r / (1 - (1 + r) ** -n))


def interest_only_payment(principal, annual_pct, compounding, frequency) -> Decimal:
    return money(Decimal(str(principal or 0)) * periodic_rate(annual_pct, compounding, frequency))


def nth_due_date(first: date, frequency: str, n: int) -> date:
    """The n-th (0-based) due date of a schedule that starts on ``first``."""
    if frequency == "monthly":
        return first + relativedelta(months=n)
    if frequency == "quarterly":
        return first + relativedelta(months=3 * n)
    if frequency == "annually":
        return first + relativedelta(years=n)
    if frequency == "semi_monthly":
        # 1st/15th-style: alternate between the start day and +15 days each month.
        base = first + relativedelta(months=n // 2)
        return base if n % 2 == 0 else base + timedelta(days=15)
    if frequency == "biweekly":
        return first + timedelta(days=14 * n)
    if frequency == "weekly":
        return first + timedelta(days=7 * n)
    raise ValueError(f"unknown frequency {frequency!r}")


def due_dates(first: date, frequency: str, start: date | None = None, end: date | None = None):
    """Yield due dates on/after ``start`` and on/before ``end`` (``end`` is required)."""
    if first is None or end is None:
        return
    n = 0
    while True:
        d = nth_due_date(first, frequency, n)
        if d > end:
            return
        if start is None or d >= start:
            yield d
        n += 1
        if n > 5000:  # safety valve for absurd inputs
            return


@dataclass
class ScheduleRow:
    number: int
    due_date: date
    payment: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal
    balloon: bool = False


def amortization_schedule(
    principal,
    annual_pct,
    compounding: str,
    frequency: str,
    first_payment: date,
    maturity: date,
    payment_type: str = "interest_only",
    payment=None,
    amortization_months: int | None = None,
):
    """Projected schedule from ``first_payment`` to ``maturity``.

    The last row includes the balloon (remaining balance) due at maturity.
    """
    balance = money(principal)
    r = periodic_rate(annual_pct, compounding, frequency)
    if payment is None:
        if payment_type == "amortizing" and amortization_months:
            payment = blended_payment(principal, annual_pct, compounding, frequency, amortization_months)
        else:
            payment = interest_only_payment(principal, annual_pct, compounding, frequency)
    payment = money(payment)

    rows = []
    dates = list(due_dates(first_payment, frequency, end=maturity))
    for i, d in enumerate(dates, start=1):
        interest = money(balance * r)
        if payment_type == "amortizing":
            principal_part = min(max(payment - interest, Decimal(0)), balance)
        else:
            principal_part = Decimal("0.00")
        pay = interest + principal_part
        balance = money(balance - principal_part)
        is_last = i == len(dates)
        if is_last and balance > 0:
            rows.append(ScheduleRow(i, d, money(pay + balance), interest, money(principal_part + balance), Decimal("0.00"), True))
            balance = Decimal("0.00")
        else:
            rows.append(ScheduleRow(i, d, money(pay), interest, principal_part, balance))
        if balance <= 0:
            break
    return rows


def months_between(start: date, end: date) -> int:
    rd = relativedelta(end, start)
    return rd.years * 12 + rd.months
