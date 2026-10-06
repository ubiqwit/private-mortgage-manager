"""A realistic sample book for trying the app: ~5 years of history across varied loans.

Every demo mortgage has a reference starting with ``DEMO-`` so it can be removed in one
go (``remove_demo``) before real data is entered.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta

from .. import db
from ..models import Mortgage, MortgageActivity, MortgageTransaction, TermHistory
from . import calc, ledger

PREFIX = "DEMO-"

FIRST = ["Aarav", "Olivia", "Liam", "Priya", "Noah", "Sofia", "Ethan", "Mei", "Lucas", "Fatima", "Daniel", "Chloe",
         "Omar", "Grace", "Mateo", "Hannah", "Arjun", "Isabella", "Kwame", "Elena", "Ravi", "Zara"]
LAST = ["Sandhu", "Bennett", "Tremblay", "Kapoor", "Nguyen", "Rossi", "Okafor", "Chen", "Silva", "Haddad", "Walsh",
        "Gill", "Moreau", "Patel", "Kowalski", "Ibrahim", "Fraser", "Lam", "Dhillon", "Costa", "Murphy", "Singh"]
COMPANIES = ["Lakeview Infill Developments Inc.", "Northbridge Holdings Ltd.", "Maple Crest Properties Corp.",
             "Credit River Homes Inc.", "Highland Builders Group"]
STREETS = ["Maple Ave", "Lakeshore Rd W", "Queen St E", "Birchwood Crt", "Elm Grove", "Dundas St W", "Bayview Ave",
           "Kingston Rd", "Main St N", "Cedar Lane", "Hurontario St", "Yonge St", "Church St", "Highway 7 W"]
CITIES = ["Toronto", "Mississauga", "Brampton", "Oakville", "Markham", "Vaughan", "Hamilton", "Burlington",
          "Richmond Hill", "Pickering", "Kitchener", "Barrie"]
BROKERS = ["Mortgage Alliance", "Dominion Lending Centres", "Mortgage Architects", "Private referral", "Verico"]
LAWYERS = ["Chen & Associates LLP", "Rossi Law Professional Corp.", "Singh Real Estate Law", "Walsh Legal"]

# Each profile: property type, position, rate type, payment type, frequency, term, how it behaves.
PROFILES = [
    ("detached", 1, "fixed", "interest_only", "monthly", 12, "good"),
    ("semi", 2, "fixed", "interest_only", "monthly", 12, "late"),
    ("condo", 1, "fixed", "amortizing", "monthly", 24, "good"),
    ("townhouse", 2, "fixed", "interest_only", "monthly", 12, "arrears"),
    ("commercial", 1, "variable", "interest_only", "monthly", 12, "good"),
    ("land", 1, "fixed", "interest_only", "monthly", 12, "payout"),
    ("multi_unit", 1, "fixed", "amortizing", "monthly", 36, "good"),
    ("detached", 3, "fixed", "interest_only", "monthly", 6, "default"),
    ("detached", 1, "variable", "interest_only", "monthly", 18, "good"),
    ("mixed_use", 1, "fixed", "interest_only", "monthly", 24, "payout"),
    ("condo", 2, "fixed", "interest_only", "biweekly", 12, "good"),
    ("detached", 2, "fixed", "interest_only", "monthly", 12, "nsf"),
    ("semi", 1, "fixed", "amortizing", "monthly", 24, "prepay"),
    ("townhouse", 1, "variable", "interest_only", "monthly", 12, "late"),
    ("detached", 1, "fixed", "interest_only", "monthly", 12, "matured"),
    ("commercial", 2, "fixed", "interest_only", "monthly", 12, "good"),
    ("land", 1, "fixed", "interest_only", "monthly", 6, "payout"),
    ("multi_unit", 2, "fixed", "interest_only", "biweekly", 12, "good"),
    ("detached", 1, "fixed", "amortizing", "monthly", 12, "good"),
    ("condo", 1, "fixed", "interest_only", "monthly", 12, "arrears"),
]


def has_demo() -> bool:
    return db.session.query(Mortgage.id).filter(Mortgage.reference.like(f"{PREFIX}%")).first() is not None


def remove_demo() -> int:
    from .deletion import delete_mortgage

    mortgages = Mortgage.query.filter(Mortgage.reference.like(f"{PREFIX}%")).all()
    for m in mortgages:
        delete_mortgage(m)
    return len(mortgages)


def _money(x) -> Decimal:
    return calc.money(x)


def _pay(m, d, amount, kind="payment", notes=None, for_due=None):
    # Split as at the due date the payment is for, so a late payment keeps that period's rate.
    split = ledger.suggest_split(m, amount, for_due or d, kind)
    t = MortgageTransaction(date=d, type=kind, amount=_money(amount), notes=notes, source="manual", **split)
    t.mortgage = m
    db.session.add(t)
    return t


def _fee(m, d, amount, notes):
    t = MortgageTransaction(date=d, type="fee", amount=_money(amount), interest=0, principal=0, fees=_money(amount),
                            notes=notes, source="manual")
    t.mortgage = m
    db.session.add(t)
    return t


def load_demo(today: date, count: int = 20, user=None, seed: int = 7) -> int:
    """Create ``count`` demo mortgages funded over the last five years, with their history."""
    rnd = random.Random(seed)
    created = 0
    span_months = 60
    for i in range(count):
        ptype, position, rate_type, pay_type, freq, term, behaviour = PROFILES[i % len(PROFILES)]
        # Spread funding dates evenly across the five years (oldest first), on the 1st or 15th.
        months_ago = span_months - int(i * span_months / count) - rnd.randint(0, 1)
        funded = (today - relativedelta(months=months_ago)).replace(day=rnd.choice([1, 15]))
        if behaviour in ("arrears", "default", "late", "nsf", "matured") and months_ago > 30:
            funded = (today - relativedelta(months=rnd.randint(14, 30))).replace(day=1)
        value = Decimal(rnd.randrange(650, 2600) * 1000)
        if ptype in ("commercial", "multi_unit", "mixed_use", "land"):
            value = Decimal(rnd.randrange(1400, 4200) * 1000)
        prior = Decimal(0) if position == 1 else _money(value * Decimal(rnd.uniform(0.45, 0.6)))
        ltv_room = value * Decimal("0.80") - prior
        principal = Decimal(max(100, min(int(ltv_room * Decimal(rnd.uniform(0.35, 0.9)) / 5000) * 5, 2400)) * 1000)
        base_rate = {1: Decimal("8.49"), 2: Decimal("10.99"), 3: Decimal("13.49")}[position]
        rate = base_rate + Decimal(rnd.choice(["-0.50", "0", "0.25", "0.50", "1.00"]))
        is_company = ptype in ("commercial", "land", "mixed_use") or i % 7 == 0
        borrower = (COMPANIES[i % len(COMPANIES)] if is_company
                    else f"{FIRST[i % len(FIRST)]} {LAST[(i * 3) % len(LAST)]}")
        city = CITIES[(i * 5) % len(CITIES)]
        m = Mortgage(
            reference=f"{PREFIX}{i + 1:02d}", borrower_name=borrower, created_by=user,
            borrower_email=f"{borrower.split()[0].lower()}@example.com", borrower_phone=f"416-555-{1000 + i * 37:04d}",
            property_address=f"{rnd.randint(2, 980)} {STREETS[(i * 3 + i // 5) % len(STREETS)]}", property_city=city,
            property_province="ON", property_type=ptype, property_value=value, appraisal_date=funded - timedelta(days=20),
            position=position, prior_charges=prior, principal_amount=principal, interest_rate=rate,
            rate_type=rate_type, prime_spread=Decimal("5.00") if rate_type == "variable" else None,
            rate_floor=Decimal("9.50") if rate_type == "variable" else None,
            compounding="semi_annual" if pay_type == "amortizing" else "monthly", payment_type=pay_type,
            payment_frequency=freq, amortization_months=300 if pay_type == "amortizing" else None,
            funded_date=funded,
            first_payment_date=funded + (relativedelta(months=1) if freq == "monthly" else timedelta(days=14)),
            term_months=term, maturity_date=funded + relativedelta(months=term),
            ownership_pct=Decimal(50) if i in (4, 15) else Decimal(100),
            lender_fee=_money(principal * Decimal("0.02")), broker_name=BROKERS[i % len(BROKERS)],
            broker_fee=_money(principal * Decimal("0.01")), lawyer_name=LAWYERS[i % len(LAWYERS)],
            renewal_fee=_money(principal * Decimal("0.0075")), nsf_fee=Decimal("300.00"),
            prepayment_terms=rnd.choice(["Open after 3 months", "Closed; 3 months' interest penalty", "Fully open"]),
            insurance_expiry=today + timedelta(days=rnd.choice([-10, 12, 45, 120, 200, 290, 340])),
            property_tax_status=rnd.choice(["Paid to date", "Paid to date", "Instalments current", "Arrears noted"]),
            status="active", match_keywords=f"E-TRANSFER {borrower.split()[0].upper()}",
            notes="Sample mortgage for trying the app. Remove with Users & data → Remove demo data.",
        )
        db.session.add(m)
        _fee(m, funded, m.lender_fee, "Lender fee at funding")

        # The last few scheduled dates (across the whole life of the loan) go unpaid.
        missed_tail = {"arrears": 2, "default": 5}.get(behaviour, 0)
        missed = set(list(calc.due_dates(m.first_payment_date, freq, end=today - timedelta(days=6)))[-missed_tail:]) \
            if missed_tail else set()
        nsf_done = prepay_done = False
        finished = False
        start = None
        while not finished:
            term_end = min(m.maturity_date, today)
            dues = [d for d in m.due_dates(start=start, end=term_end) if d < today]
            for n, d in enumerate(dues):
                if behaviour == "payout" and m.term_history and n == len(dues) // 2 and n >= 3:
                    # Borrower sells / refinances part-way through a renewal term.
                    payout_day = d
                    owed = m.balance(as_of=payout_day) + ledger.period_interest(m, payout_day)
                    _pay(m, payout_day, owed, "payout", "Paid out on sale of property")
                    m.status = "paid_out"
                    finished = True
                    break
                pay = m.scheduled_payment(d)
                paid_on = d
                if behaviour == "late" and rnd.random() < 0.35:
                    paid_on = d + timedelta(days=rnd.randint(6, 21))
                elif rnd.random() < 0.2:
                    paid_on = d + timedelta(days=rnd.randint(0, 3))
                if d in missed:
                    continue  # missed payment (arrears / default)
                if paid_on >= today:
                    continue
                _pay(m, paid_on, pay, for_due=d)
                if behaviour == "nsf" and not nsf_done and n >= 4:
                    nsf_done = True
                    bounce = paid_on + timedelta(days=3)
                    _pay(m, bounce, pay, "nsf", "Payment returned NSF")
                    catch_up = bounce + timedelta(days=9)
                    if catch_up < today:
                        _pay(m, catch_up, pay, notes="Replacement for NSF payment")
                        _fee(m, catch_up, m.nsf_fee, "NSF fee")
                if behaviour == "prepay" and not prepay_done and n >= 6:
                    prepay_done = True
                    extra = _money(m.balance(as_of=paid_on) * Decimal("0.15"))
                    _pay(m, paid_on + timedelta(days=10), extra, "prepayment", "Annual 15% prepayment privilege")
            if finished:
                break
            if m.maturity_date >= today:
                break
            # The term has matured: renew, pay out, or leave it awaiting a decision.
            if behaviour == "matured" and m.maturity_date >= today - timedelta(days=90):
                m.status = "matured"
                break
            if behaviour == "payout" and not m.term_history and rnd.random() < 0.5 or \
                    behaviour == "payout" and m.maturity_date < today - relativedelta(months=30):
                owed = m.balance(as_of=m.maturity_date) + ledger.period_interest(m, m.maturity_date)
                _pay(m, m.maturity_date, owed, "payout", "Paid out at maturity (refinanced with a bank)")
                m.status = "paid_out"
                break
            effective = m.maturity_date
            db.session.add(TermHistory.snapshot(m, effective - timedelta(days=1), note="Renewed"))
            if m.rate_type == "fixed":
                m.interest_rate = Decimal(m.interest_rate) + Decimal(rnd.choice(["-0.50", "-0.25", "0", "0.25", "0.50"]))
            m.maturity_date = effective + relativedelta(months=m.term_months)
            _fee(m, effective, m.renewal_fee, "Renewal fee")
            start = effective + timedelta(days=1)  # the renewal-date payment belongs to the old term
        # Status and a paper trail for loans in trouble.
        if behaviour == "arrears" and m.status == "active":
            m.status = "in_arrears"
            db.session.add(MortgageActivity(mortgage=m, kind="call", user=user,
                                            body="Called borrower about missed payments — promised to catch up by month end.",
                                            follow_up_on=today + timedelta(days=5)))
        if behaviour == "default":
            m.status = "default"
            for kind, body, days in [
                ("letter", "Sent demand letter for arrears.", 75),
                ("legal", "Notice of Sale under Mortgage issued by lawyer (35-day redemption period).", 40),
                ("call", "Borrower's lawyer says refinancing is in progress — awaiting commitment.", 10),
            ]:
                a = MortgageActivity(mortgage=m, kind=kind, body=body, user=user,
                                     follow_up_on=(today + timedelta(days=7)) if kind == "call" else None)
                db.session.add(a)
                db.session.flush()
                a.created_at = a.created_at - timedelta(days=days)
        if behaviour == "late":
            db.session.add(MortgageActivity(mortgage=m, kind="email", user=user,
                                            body="Reminded borrower that payments are due on the 1st (5-day grace)."))
        db.session.flush()
        created += 1
    return created
