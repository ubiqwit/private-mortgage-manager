"""Database models.

Sign conventions for :class:`MortgageTransaction`
-------------------------------------------------
``amount`` is always the cash amount (positive). ``principal`` is signed from the
borrower-balance point of view: a repayment *reduces* the balance and is stored
positive; an additional advance *increases* the balance and is stored negative.
So ``balance = original principal - sum(principal)``. A ``funding`` transaction
records the cash sent out for the original principal (e.g. matched to the bank
withdrawal) and has no balance effect, because ``principal_amount`` already counts it.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func

from . import db
from .services import calc
from .timeutil import today as local_today
from .timeutil import utcnow

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
    ("fee", "Fee"),
    ("payout", "Payout / discharge"),
    ("funding", "Funding (initial advance)"),
    ("advance", "Additional advance"),
    ("nsf", "NSF / reversed payment"),
    ("adjustment", "Adjustment"),
]
INCOME_TYPES = ("payment", "prepayment", "fee", "payout", "nsf", "adjustment")
REGULAR_PAYMENT_TYPES = ("payment", "nsf")
# A payment only counts as in arrears once it is this many days past due.
ARREARS_GRACE_DAYS = 5


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


def _current_company_id():
    """Default for company_id columns: the company the signed-in user is working in."""
    from .tenancy import current_company_id

    return current_company_id()


user_company = db.Table(
    "user_company",
    db.Column("user_id", db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
    db.Column("company_id", db.Integer, db.ForeignKey("company.id", ondelete="CASCADE"), primary_key=True),
)


class Company(TimestampMixin, db.Model):
    """A lending entity (e.g. a holding company). Mortgages, statements, reports and
    closed months belong to one company; users switch between the companies they manage."""

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)


class Mortgage(TimestampMixin, db.Model):
    __table_args__ = (db.UniqueConstraint("company_id", "reference", name="uq_mortgage_company_reference"),)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True,
                           default=_current_company_id)
    reference = db.Column(db.String(40), nullable=False)

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
    term_history = db.relationship(
        "TermHistory",
        back_populates="mortgage",
        cascade="all, delete-orphan",
        order_by="TermHistory.valid_until",
    )
    activities = db.relationship(
        "MortgageActivity",
        back_populates="mortgage",
        cascade="all, delete-orphan",
        order_by="MortgageActivity.created_at.desc()",
    )
    documents = db.relationship(
        "MortgageDocument",
        back_populates="mortgage",
        cascade="all, delete-orphan",
        order_by="MortgageDocument.uploaded_at.desc()",
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
    def display_status(self):
        """Stored status, upgraded to "matured" when an active loan is past its maturity date."""
        if self.status in ("active", "in_arrears") and self.maturity_date < local_today():
            return "matured"
        return self.status

    @property
    def display_status_label(self):
        return dict(STATUSES).get(self.display_status, self.display_status)

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
    def terms_on(self, day: date):
        """The terms (rate, payment…) in force on ``day``: a :class:`TermHistory` row for
        days before a renewal/rate change, otherwise the mortgage's current fields."""
        for h in self.term_history:  # ordered by valid_until
            if day <= h.valid_until:
                return h
        return self

    def effective_rate(self, prime=None, on: date | None = None) -> Decimal:
        """Annual rate on ``on`` (default today). Variable loans follow prime + spread,
        subject to the floor — using prime as it was on that day when ``on`` is given."""
        terms = self.terms_on(on or local_today())
        if terms.rate_type == "variable" and terms.prime_spread is not None:
            if on is not None:
                from .services.market import prime_on

                historical = prime_on(on)
                if historical is not None:
                    prime = historical
            if prime is not None:
                rate = Decimal(str(prime)) + Decimal(str(terms.prime_spread))
                if terms.rate_floor is not None:
                    rate = max(rate, Decimal(str(terms.rate_floor)))
                return rate
        return Decimal(str(terms.interest_rate))

    def _payment(self, terms, rate, balance) -> Decimal:
        if terms.payment_amount:
            return calc.money(terms.payment_amount)
        if terms.payment_type == "amortizing" and terms.amortization_months:
            return calc.blended_payment(
                self.principal_amount, rate, terms.compounding, self.payment_frequency, terms.amortization_months
            )
        return calc.interest_only_payment(balance, rate, terms.compounding, self.payment_frequency)

    def regular_payment(self, prime=None) -> Decimal:
        """The payment under today's terms (interest-only: on today's balance)."""
        return self._payment(self.terms_on(local_today()), self.effective_rate(prime), self.balance())

    def scheduled_payment(self, due: date, prime=None) -> Decimal:
        """Payment owed on ``due``, under the terms and balance of the period it pays for —
        so past dues stay correct after a prepayment, renewal or prime change."""
        day = due - timedelta(days=1)
        return self._payment(self.terms_on(day), self.effective_rate(prime, on=day), self.balance(as_of=day))

    def scheduled_total(self, start=None, end=None, prime=None) -> Decimal:
        """Sum of scheduled payments due in the window, skipping dates on/after a payout."""
        return sum(
            (self.scheduled_payment(d, prime) for d in self.due_dates(start=start, end=end) if self.balance(as_of=d) > 0),
            ZERO,
        )

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
        after = after or local_today()
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

    def arrears(self, as_of: date | None = None, prime=None, grace_days: int = ARREARS_GRACE_DAYS) -> Decimal:
        """Scheduled payments more than ``grace_days`` past due, minus regular payments received."""
        as_of = as_of or local_today()
        if self.status == "paid_out":
            return ZERO
        expected = self.scheduled_total(end=as_of - timedelta(days=grace_days), prime=prime)
        return max(calc.money(expected - self.regular_received(as_of)), ZERO)

    def days_to_maturity(self, as_of: date | None = None) -> int:
        return (self.maturity_date - (as_of or local_today())).days

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
    source = db.Column(db.String(10), nullable=False, default="manual")  # manual | bank (created from a bank line)
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
        return -amt if self.type in ("advance", "nsf", "funding") else amt


ACTIVITY_KINDS = [
    ("note", "Note"),
    ("call", "Phone call"),
    ("email", "Email"),
    ("meeting", "Meeting"),
    ("letter", "Letter / notice"),
    ("legal", "Legal / enforcement"),
]


class MortgageActivity(db.Model):
    """A dated entry in a mortgage's activity log, optionally with a follow-up date."""

    id = db.Column(db.Integer, primary_key=True)
    mortgage_id = db.Column(db.Integer, db.ForeignKey("mortgage.id"), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, default="note")
    body = db.Column(db.Text, nullable=False)
    follow_up_on = db.Column(db.Date, index=True)
    done = db.Column(db.Boolean, nullable=False, default=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    mortgage = db.relationship("Mortgage", back_populates="activities")
    user = db.relationship("User")

    @property
    def kind_label(self):
        return dict(ACTIVITY_KINDS).get(self.kind, self.kind)

    @property
    def open_follow_up(self):
        return self.follow_up_on is not None and not self.done


DOCUMENT_CATEGORIES = [
    ("commitment", "Commitment letter / loan agreement"),
    ("appraisal", "Appraisal"),
    ("title", "Title / registration / charge"),
    ("insurance", "Insurance"),
    ("identity", "ID / KYC"),
    ("renewal", "Renewal / amendment"),
    ("discharge", "Payout statement / discharge"),
    ("correspondence", "Correspondence"),
    ("other", "Other"),
]


class MortgageDocument(db.Model):
    """A file kept with a mortgage. Stored in the database so it survives redeploys
    and is covered by the database's backups."""

    id = db.Column(db.Integer, primary_key=True)
    mortgage_id = db.Column(db.Integer, db.ForeignKey("mortgage.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(120), nullable=False, default="application/octet-stream")
    size = db.Column(db.Integer, nullable=False, default=0)
    category = db.Column(db.String(30), nullable=False, default="other")
    note = db.Column(db.String(300))
    content = db.deferred(db.Column(db.LargeBinary, nullable=False))  # only loaded on download
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    uploaded_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    mortgage = db.relationship("Mortgage", back_populates="documents")
    uploaded_by = db.relationship("User")

    @property
    def category_label(self):
        return dict(DOCUMENT_CATEGORIES).get(self.category, self.category)


class TermHistory(db.Model):
    """Terms that applied up to and including ``valid_until``. Written when a mortgage is
    renewed or its rate changes, so earlier periods keep their original rate and payment."""

    id = db.Column(db.Integer, primary_key=True)
    mortgage_id = db.Column(db.Integer, db.ForeignKey("mortgage.id"), nullable=False, index=True)
    valid_until = db.Column(db.Date, nullable=False)
    rate_type = db.Column(db.String(20), nullable=False)
    interest_rate = db.Column(db.Numeric(7, 4), nullable=False)
    prime_spread = db.Column(db.Numeric(7, 4))
    rate_floor = db.Column(db.Numeric(7, 4))
    compounding = db.Column(db.String(20), nullable=False)
    payment_type = db.Column(db.String(20), nullable=False)
    payment_amount = db.Column(db.Numeric(14, 2))
    amortization_months = db.Column(db.Integer)
    maturity_date = db.Column(db.Date, nullable=False)  # maturity before the change
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    mortgage = db.relationship("Mortgage", back_populates="term_history")

    TERM_FIELDS = ("rate_type", "interest_rate", "prime_spread", "rate_floor", "compounding", "payment_type",
                   "payment_amount", "amortization_months", "maturity_date")

    @classmethod
    def snapshot(cls, m: "Mortgage", valid_until: date, note: str | None = None) -> "TermHistory":
        return cls(mortgage=m, valid_until=valid_until, note=note,
                   **{f: getattr(m, f) for f in cls.TERM_FIELDS})

    def restore_to(self, m: "Mortgage"):
        for f in self.TERM_FIELDS:
            setattr(m, f, getattr(self, f))


class StatementImport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True,
                           default=_current_company_id)
    filename = db.Column(db.String(255), nullable=False)
    account_name = db.Column(db.String(120))
    imported_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    row_count = db.Column(db.Integer, default=0)
    duplicate_count = db.Column(db.Integer, default=0)
    period_start = db.Column(db.Date)
    period_end = db.Column(db.Date)

    transactions = db.relationship("BankTransaction", back_populates="statement", cascade="all, delete-orphan")


class BankTransaction(db.Model):
    __table_args__ = (db.UniqueConstraint("company_id", "fingerprint", name="uq_bank_transaction_company_fingerprint"),)

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("company.id"), nullable=False, index=True,
                           default=_current_company_id)
    statement_id = db.Column(db.Integer, db.ForeignKey("statement_import.id"), nullable=False, index=True)
    account_name = db.Column(db.String(120))
    date = db.Column(db.Date, nullable=False, index=True)
    description = db.Column(db.String(500), nullable=False, default="")
    amount = db.Column(db.Numeric(14, 2), nullable=False)  # + deposit / - withdrawal
    fingerprint = db.Column(db.String(64), nullable=False)  # unique per company
    status = db.Column(db.String(20), default="unmatched", nullable=False, index=True)  # unmatched|matched|ignored
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

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
    form_fields = db.Column(db.Text)  # JSON list of mortgage-form fields shown; NULL = all
    companies = db.relationship("Company", secondary=user_company, order_by="Company.name")

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

    def accessible_companies(self):
        """Admins manage every company; others only the ones they're assigned to."""
        if self.is_admin:
            return Company.query.order_by(Company.name).all()
        return list(self.companies)

    @property
    def can_edit(self):
        return self.role in ("admin", "editor")

    @property
    def display_name(self):
        return self.name or self.email


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)
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
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)


def bootstrap_admin_from_env():
    """Create the first admin from PMM_ADMIN_EMAIL / PMM_ADMIN_PASSWORD if no users exist.

    Handy on hosting platforms without shell access (e.g. Render's free plan). Setting
    PMM_ADMIN_RESET=1 as well is the locked-out recovery path: on start-up that account's
    password is reset (or the account re-created) and it is made an active admin.
    """
    import os

    email = (os.environ.get("PMM_ADMIN_EMAIL") or "").strip().lower()
    password = os.environ.get("PMM_ADMIN_PASSWORD")
    if not email or not password:
        return
    reset = os.environ.get("PMM_ADMIN_RESET") == "1"
    if db.session.query(User.id).first() and not reset:
        return
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(email=email, name="Owner")
        db.session.add(user)
    user.set_password(password)  # also signs out every existing session for this account
    user.role, user.active = "admin", True
    if reset:
        db.session.add(AuditLog(action="admin_reset_from_env", detail=email))
    db.session.commit()
