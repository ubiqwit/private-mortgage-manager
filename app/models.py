"""Database models.

Sign conventions for :class:`MortgageTransaction`
-------------------------------------------------
``amount`` is always the cash amount (positive). ``principal`` is signed from the
borrower-balance point of view: a repayment *reduces* the balance and is stored
positive; an additional advance *increases* the balance and is stored negative.
So ``balance = original principal - sum(principal)``.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func

from . import db
from .services import calc

ZERO = Decimal("0.00")

PROPERTY_TYPES = [
    ("detached", "Detached"),
    ("semi", "Semi-detached"),
    ("townhouse", "Townhouse"),
    ("condo", "Condo"),
    ("multi_unit", "Multi-unit residential"),
    ("commercial", "Commercial"),
    ("mixed_use", "Mixed use"),
    ("land", "Vacant land"),
    ("other", "Other"),
]
STATUSES = [
    ("active", "Active"),
    ("in_arrears", "In arrears"),
    ("default", "Default / enforcement"),
    ("matured", "Matured (awaiting payout/renewal)"),
    ("paid_out", "Paid out"),
]
OPEN_STATUSES = ("active", "in_arrears", "default", "matured")
TXN_TYPES = [
    ("payment", "Regular payment"),
    ("prepayment", "Principal prepayment"),
    ("fee", "Fee (lender/renewal/NSF/etc.)"),
    ("payout", "Payout / discharge"),
    ("advance", "Advance to borrower"),
    ("nsf", "NSF / reversed payment"),
    ("adjustment", "Adjustment"),
]
INCOME_TYPES = ("payment", "prepayment", "fee", "payout", "nsf", "adjustment")
REGULAR_PAYMENT_TYPES = ("payment", "nsf")


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Mortgage(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(40), unique=True, nullable=False)

    # Borrower
    borrower_name = db.Column(db.String(200), nullable=False)
    borrower_email = db.Column(db.String(200))
    borrower_phone = db.Column(db.String(50))
    guarantors = db.Column(db.String(300))

    # Security
    property_address = db.Column(db.String(300), nullable=False)
    property_city = db.Column(db.String(100))
    property_province = db.Column(db.String(50), default="ON")
    property_type = db.Column(db.String(30), default="detached")
    property_value = db.Column(db.Numeric(14, 2))
    appraisal_date = db.Column(db.Date)
    position = db.Column(db.Integer, default=1)  # 1st, 2nd, 3rd charge
    prior_charges = db.Column(db.Numeric(14, 2), default=0)  # balance of charges ranking ahead
    pin = db.Column(db.String(50))  # land registry PIN / legal description reference

    # Loan terms
    principal_amount = db.Column(db.Numeric(14, 2), nullable=False)
    interest_rate = db.Column(db.Numeric(7, 4), nullable=False)  # annual %, also fallback for variable
    rate_type = db.Column(db.String(20), default="fixed")  # fixed | variable
    prime_spread = db.Column(db.Numeric(7, 4))  # variable: prime + spread
    rate_floor = db.Column(db.Numeric(7, 4))
    compounding = db.Column(db.String(20), default="monthly")
    payment_type = db.Column(db.String(20), default="interest_only")  # interest_only | amortizing
    payment_frequency = db.Column(db.String(20), default="monthly")
    payment_amount = db.Column(db.Numeric(14, 2))  # override; computed if blank
    amortization_months = db.Column(db.Integer)
    funded_date = db.Column(db.Date, nullable=False)
    first_payment_date = db.Column(db.Date, nullable=False)
    term_months = db.Column(db.Integer)
    maturity_date = db.Column(db.Date, nullable=False)
    ownership_pct = db.Column(db.Numeric(6, 2), default=100)  # your share if syndicated

    # Fees & parties
    lender_fee = db.Column(db.Numeric(12, 2), default=0)
    broker_name = db.Column(db.String(200))
    broker_fee = db.Column(db.Numeric(12, 2), default=0)
    lawyer_name = db.Column(db.String(200))
    renewal_fee = db.Column(db.Numeric(12, 2), default=0)
    nsf_fee = db.Column(db.Numeric(12, 2), default=0)
    prepayment_terms = db.Column(db.String(300))
    insurance_expiry = db.Column(db.Date)
    property_tax_status = db.Column(db.String(100))

    status = db.Column(db.String(20), default="active", nullable=False)
    match_keywords = db.Column(db.Text)  # one per line; used to match bank deposits
    notes = db.Column(db.Text)

    transactions = db.relationship(
        "MortgageTransaction",
        back_populates="mortgage",
        cascade="all, delete-orphan",
        order_by="MortgageTransaction.date",
    )

    # ----- labels -------------------------------------------------------
    @property
    def label(self):
        return f"{self.reference} — {self.borrower_name}"

    @property
    def position_label(self):
        return {1: "1st", 2: "2nd", 3: "3rd"}.get(self.position or 1, f"{self.position}th")

    @property
    def status_label(self):
        return dict(STATUSES).get(self.status, self.status)

    @property
    def property_type_label(self):
        return dict(PROPERTY_TYPES).get(self.property_type, self.property_type)

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    @property
    def keywords(self):
        return [k.strip() for k in (self.match_keywords or "").splitlines() if k.strip()]

    # ----- rates & payments --------------------------------------------
    def effective_rate(self, prime=None) -> Decimal:
        """Current annual rate. Variable loans follow prime + spread (subject to floor)."""
        if self.rate_type == "variable" and prime is not None and self.prime_spread is not None:
            rate = Decimal(str(prime)) + Decimal(str(self.prime_spread))
            if self.rate_floor is not None:
                rate = max(rate, Decimal(str(self.rate_floor)))
            return rate
        return Decimal(str(self.interest_rate))

    def regular_payment(self, prime=None) -> Decimal:
        if self.payment_amount:
            return calc.money(self.payment_amount)
        rate = self.effective_rate(prime)
        if self.payment_type == "amortizing" and self.amortization_months:
            return calc.blended_payment(
                self.principal_amount, rate, self.compounding, self.payment_frequency, self.amortization_months
            )
        return calc.interest_only_payment(self.balance(), rate, self.compounding, self.payment_frequency)

    def monthly_equivalent_payment(self, prime=None) -> Decimal:
        return calc.money(self.regular_payment(prime) * calc.periods_per_year(self.payment_frequency) / 12)

    def schedule(self, prime=None):
        return calc.amortization_schedule(
            self.principal_amount,
            self.effective_rate(prime),
            self.compounding,
            self.payment_frequency,
            self.first_payment_date,
            self.maturity_date,
            payment_type=self.payment_type,
            payment=self.payment_amount or None,
            amortization_months=self.amortization_months,
        )

    # ----- balances -------------------------------------------------------
    def balance(self, as_of: date | None = None) -> Decimal:
        repaid = sum(
            (Decimal(str(t.principal or 0)) for t in self.transactions if as_of is None or t.date <= as_of),
            ZERO,
        )
        return calc.money(Decimal(str(self.principal_amount)) - repaid)

    def ltv(self):
        if not self.property_value:
            return None
        return calc.money(self.balance() / Decimal(str(self.property_value)) * 100)

    def combined_ltv(self):
        if not self.property_value:
            return None
        total = self.balance() + Decimal(str(self.prior_charges or 0))
        return calc.money(total / Decimal(str(self.property_value)) * 100)

    # ----- payment tracking ----------------------------------------------
    def due_dates(self, start=None, end=None):
        end = min(end or self.maturity_date, self.maturity_date)
        return list(calc.due_dates(self.first_payment_date, self.payment_frequency, start=start, end=end))

    def next_due_date(self, after: date | None = None):
        after = after or date.today()
        for d in calc.due_dates(self.first_payment_date, self.payment_frequency, start=after, end=self.maturity_date):
            return d
        return None

    def last_payment(self):
        pays = [t for t in self.transactions if t.type in ("payment", "payout")]
        return pays[-1] if pays else None

    def regular_received(self, through: date) -> Decimal:
        return sum(
            (Decimal(str(t.amount)) * (-1 if t.type == "nsf" else 1)
             for t in self.transactions if t.type in REGULAR_PAYMENT_TYPES and t.date <= through),
            ZERO,
        )

    def arrears(self, as_of: date | None = None, prime=None) -> Decimal:
        """Scheduled payments due through ``as_of`` minus regular payments received."""
        as_of = as_of or date.today()
        if self.status == "paid_out":
            return ZERO
        due_count = len(self.due_dates(end=as_of))
        expected = self.regular_payment(prime) * due_count
        return max(calc.money(expected - self.regular_received(as_of)), ZERO)

    def days_to_maturity(self, as_of: date | None = None) -> int:
        return (self.maturity_date - (as_of or date.today())).days

    def income_between(self, start: date, end: date):
        """Cash received split into interest/principal/fees for a date range."""
        out = {"interest": ZERO, "principal": ZERO, "fees": ZERO, "total": ZERO}
        for t in self.transactions:
            if start <= t.date <= end and t.type in INCOME_TYPES:
                out["interest"] += Decimal(str(t.interest or 0))
                out["principal"] += Decimal(str(t.principal or 0))
                out["fees"] += Decimal(str(t.fees or 0))
                out["total"] += Decimal(str(t.signed_amount))
        return out


class MortgageTransaction(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mortgage_id = db.Column(db.Integer, db.ForeignKey("mortgage.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, index=True)
    type = db.Column(db.String(20), nullable=False, default="payment")
    amount = db.Column(db.Numeric(14, 2), nullable=False)
    interest = db.Column(db.Numeric(14, 2), default=0)
    principal = db.Column(db.Numeric(14, 2), default=0)
    fees = db.Column(db.Numeric(14, 2), default=0)
    bank_transaction_id = db.Column(db.Integer, db.ForeignKey("bank_transaction.id"), index=True)
    notes = db.Column(db.String(500))

    mortgage = db.relationship("Mortgage", back_populates="transactions")
    bank_transaction = db.relationship("BankTransaction", back_populates="mortgage_transactions")

    @property
    def type_label(self):
        return dict(TXN_TYPES).get(self.type, self.type)

    @property
    def signed_amount(self) -> Decimal:
        """Cash in (+) / out (-) from the lender's point of view."""
        amt = Decimal(str(self.amount))
        return -amt if self.type in ("advance", "nsf") else amt


class StatementImport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    account_name = db.Column(db.String(120))
    imported_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    row_count = db.Column(db.Integer, default=0)
    duplicate_count = db.Column(db.Integer, default=0)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)

    transactions = db.relationship("BankTransaction", back_populates="statement", cascade="all, delete-orphan")


class BankTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    statement_id = db.Column(db.Integer, db.ForeignKey("statement_import.id"), nullable=False, index=True)
    account_name = db.Column(db.String(120))
    date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False, default="")
    amount = db.Column(db.Numeric(14, 2), nullable=False)  # + deposit / - withdrawal
    fingerprint = db.Column(db.String(64), unique=True, nullable=False)
    status = db.Column(db.String(20), default="unmatched", nullable=False, index=True)  # unmatched|matched|ignored
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    statement = db.relationship("StatementImport", back_populates="transactions")
    mortgage_transactions = db.relationship("MortgageTransaction", back_populates="bank_transaction")

    @property
    def is_deposit(self):
        return Decimal(str(self.amount)) > 0

    @property
    def allocated(self) -> Decimal:
        return sum((Decimal(str(m.amount)) for m in self.mortgage_transactions), ZERO)


class MarketObservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    series = db.Column(db.String(60), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False)
    value = db.Column(db.Numeric(10, 4), nullable=False)
    source = db.Column(db.String(30), default="boc")  # boc | manual
    __table_args__ = (db.UniqueConstraint("series", "date", name="uq_series_date"),)


class NewsItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(100))
    title = db.Column(db.String(500), nullable=False)
    link = db.Column(db.String(1000), unique=True, nullable=False)
    published = db.Column(db.DateTime)
    summary = db.Column(db.Text)


class Setting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=None):
        row = db.session.get(Setting, key)
        return row.value if row else default

    @staticmethod
    def set(key, value):
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            db.session.add(row)
        row.value = value


def next_reference():
    count = db.session.query(func.count(Mortgage.id)).scalar() or 0
    n = count + 1
    while db.session.query(Mortgage.id).filter_by(reference=f"M-{n:03d}").first():
        n += 1
    return f"M-{n:03d}"


# ----------------------------------------------------------------------------
# Users & audit trail (the app is designed to be hosted and reached from anywhere)
# ----------------------------------------------------------------------------
ROLES = [
    ("admin", "Admin — full access, manages users"),
    ("editor", "Editor — can add and change data"),
    ("viewer", "Viewer — read-only (e.g. your accountant)"),
]


class User(TimestampMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    name = db.Column(db.String(200))
    password_hash = db.Column(db.String(300), nullable=False)
    password_stamp = db.Column(db.String(32), nullable=False, default="")
    role = db.Column(db.String(20), nullable=False, default="admin")
    active = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime)

    def set_password(self, password: str):
        import secrets

        from werkzeug.security import generate_password_hash

        if len(password or "") < 10:
            raise ValueError("Password must be at least 10 characters.")
        self.password_hash = generate_password_hash(password)
        self.password_stamp = secrets.token_hex(8)

    def check_password(self, password: str) -> bool:
        from werkzeug.security import check_password_hash

        return check_password_hash(self.password_hash, password or "")

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def can_edit(self):
        return self.role in ("admin", "editor")

    @property
    def display_name(self):
        return self.name or self.email


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    action = db.Column(db.String(80), nullable=False)
    detail = db.Column(db.String(1000))
    ip = db.Column(db.String(64))

    user = db.relationship("User")


def audit(action: str, detail: str = ""):
    """Record who did what. Call before ``db.session.commit()``."""
    from flask import g, has_request_context, request

    user = g.get("user") if has_request_context() else None
    ip = request.remote_addr if has_request_context() else None
    db.session.add(AuditLog(user_id=user.id if user else None, action=action, detail=detail[:1000], ip=ip))


class PendingUpload(db.Model):
    """Raw uploaded statement kept in the database between the upload and the
    column-mapping confirmation step (cloud hosts have ephemeral disks)."""

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    filename = db.Column(db.String(255), nullable=False)
    content = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


def bootstrap_admin_from_env():
    """Create the first admin from PMM_ADMIN_EMAIL / PMM_ADMIN_PASSWORD if no users exist.

    Handy on hosting platforms where you cannot run a shell command before first use.
    """
    import os

    email = os.environ.get("PMM_ADMIN_EMAIL")
    password = os.environ.get("PMM_ADMIN_PASSWORD")
    if not email or not password:
        return
    if db.session.query(User.id).first():
        return
    user = User(email=email.strip().lower(), name="Owner", role="admin")
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
