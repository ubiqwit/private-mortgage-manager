"""Splitting received money into interest / principal / fees."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from . import calc

ZERO = Decimal("0.00")


def period_interest(mortgage, on: date, prime=None) -> Decimal:
    """Interest for one payment period on the balance outstanding just before ``on``."""
    balance = mortgage.balance(as_of=on - timedelta(days=1))
    rate = calc.periodic_rate(mortgage.effective_rate(prime), mortgage.compounding, mortgage.payment_frequency)
    return calc.money(balance * rate)


def suggest_split(mortgage, amount, on: date, txn_type: str = "payment", prime=None) -> dict:
    """Best-guess allocation of ``amount`` received on ``on``. Always sums to ``amount``
    (with ``principal`` negated for advances), except funding, which is all zero."""
    amount = calc.money(amount)
    interest = principal = fees = ZERO
    if txn_type == "payment":
        due_interest = period_interest(mortgage, on, prime)
        interest = min(amount, due_interest)
        principal = min(amount - interest, mortgage.balance(as_of=on))
        fees = amount - interest - principal  # overpayment beyond the balance → treat as fee/other
    elif txn_type == "nsf":
        split = suggest_split(mortgage, amount, on, "payment", prime)
        interest, principal, fees = -split["interest"], -split["principal"], -split["fees"]
    elif txn_type == "prepayment":
        principal = amount
    elif txn_type == "payout":
        principal = min(amount, mortgage.balance(as_of=on))
        interest = amount - principal
    elif txn_type == "advance":
        principal = -amount
    elif txn_type == "funding":
        pass  # cash out for the original principal: no balance or income effect
    elif txn_type == "fee":
        fees = amount
    else:  # adjustment — leave for the user to allocate
        interest = amount
    return {"interest": calc.money(interest), "principal": calc.money(principal), "fees": calc.money(fees)}
