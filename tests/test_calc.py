from datetime import date
from decimal import Decimal

from app.services import calc


def test_interest_only_monthly():
    # $500k at 9% compounded monthly → $3,750.00 per month
    assert calc.interest_only_payment(500_000, 9, "monthly", "monthly") == Decimal("3750.00")


def test_semi_annual_compounding_is_lower_than_monthly_nominal():
    monthly = calc.periodic_rate(6, "monthly", "monthly")
    semi = calc.periodic_rate(6, "semi_annual", "monthly")
    assert semi < monthly
    # Canadian 6% compounded semi-annually → ~0.4939% per month
    assert abs(float(semi) - 0.004938622) < 1e-8


def test_blended_payment_canadian_standard():
    # $100,000, 5% semi-annual, 25 years, monthly → $581.60 (standard Canadian table value)
    assert calc.blended_payment(100_000, 5, "semi_annual", "monthly", 300) == Decimal("581.60")


def test_zero_rate_blended():
    assert calc.blended_payment(12_000, 0, "monthly", "monthly", 12) == Decimal("1000.00")


def test_due_dates_monthly_handles_month_end():
    dates = list(calc.due_dates(date(2026, 1, 31), "monthly", end=date(2026, 4, 30)))
    assert dates == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)]


def test_due_dates_window():
    dates = list(calc.due_dates(date(2026, 1, 1), "biweekly", start=date(2026, 2, 1), end=date(2026, 3, 1)))
    assert dates == [date(2026, 2, 12), date(2026, 2, 26)]


def test_interest_only_schedule_has_balloon():
    rows = calc.amortization_schedule(
        100_000, 12, "monthly", "monthly", date(2026, 2, 1), date(2027, 1, 1)
    )
    assert len(rows) == 12
    assert all(r.interest == Decimal("1000.00") for r in rows)
    assert rows[-1].balloon and rows[-1].payment == Decimal("101000.00")
    assert rows[-1].balance == 0


def test_amortizing_schedule_reduces_balance():
    rows = calc.amortization_schedule(
        100_000, 5, "semi_annual", "monthly", date(2026, 2, 1), date(2027, 1, 1),
        payment_type="amortizing", amortization_months=300,
    )
    assert rows[0].payment == Decimal("581.60")
    assert rows[0].principal > 0
    assert rows[5].balance < rows[0].balance
    assert rows[-1].balloon
