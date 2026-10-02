"""Mortgage book: list, create/edit, detail, schedule and manual transactions."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from sqlalchemy import or_

from .. import db
from ..models import (
    PROPERTY_TYPES,
    STATUSES,
    TXN_TYPES,
    Mortgage,
    MortgageTransaction,
    audit,
    next_reference,
)
from ..services import calc, ledger, matching
from ..services.market import latest_prime

bp = Blueprint("mortgages", __name__, url_prefix="/mortgages")

DECIMAL_FIELDS = [
    "property_value", "prior_charges", "principal_amount", "interest_rate", "prime_spread", "rate_floor",
    "payment_amount", "ownership_pct", "lender_fee", "broker_fee", "renewal_fee", "nsf_fee",
]
INT_FIELDS = ["position", "amortization_months", "term_months"]
DATE_FIELDS = ["appraisal_date", "funded_date", "first_payment_date", "maturity_date", "insurance_expiry"]
TEXT_FIELDS = [
    "reference", "borrower_name", "borrower_email", "borrower_phone", "guarantors", "property_address",
    "property_city", "property_province", "property_type", "pin", "rate_type", "compounding", "payment_type",
    "payment_frequency", "broker_name", "lawyer_name", "prepayment_terms", "property_tax_status", "status",
    "match_keywords", "notes",
]
REQUIRED = {
    "borrower_name": "Borrower name",
    "property_address": "Property address",
    "principal_amount": "Principal amount",
    "interest_rate": "Interest rate",
    "funded_date": "Funded date",
}


class FormError(ValueError):
    pass


def parse_decimal(raw, label):
    raw = (raw or "").replace(",", "").replace("$", "").replace("%", "").strip()
    if raw == "":
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise FormError(f"{label}: '{raw}' is not a number")


def parse_date(raw, label):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        raise FormError(f"{label}: '{raw}' is not a valid date (YYYY-MM-DD)")


def apply_form(m: Mortgage, form):
    for f in TEXT_FIELDS:
        if f in form:
            setattr(m, f, form.get(f, "").strip() or None)
    for f in DECIMAL_FIELDS:
        if f in form:
            setattr(m, f, parse_decimal(form.get(f), f.replace("_", " ").capitalize()))
    for f in INT_FIELDS:
        if f in form:
            v = parse_decimal(form.get(f), f.replace("_", " ").capitalize())
            setattr(m, f, int(v) if v is not None else None)
    for f in DATE_FIELDS:
        if f in form:
            setattr(m, f, parse_date(form.get(f), f.replace("_", " ").capitalize()))

    missing = [label for f, label in REQUIRED.items() if getattr(m, f) in (None, "")]
    if missing:
        raise FormError("Required: " + ", ".join(missing))

    # Sensible derivations so the user only has to type the essentials.
    if not m.first_payment_date:
        m.first_payment_date = m.funded_date + relativedelta(months=1)
    if not m.maturity_date:
        if not m.term_months:
            raise FormError("Enter either a term (months) or a maturity date")
        m.maturity_date = m.funded_date + relativedelta(months=m.term_months)
    if not m.term_months:
        m.term_months = calc.months_between(m.funded_date, m.maturity_date)
    if m.maturity_date <= m.funded_date:
        raise FormError("Maturity date must be after the funded date")
    if m.rate_type == "variable" and m.prime_spread is None:
        raise FormError("Variable-rate mortgages need a spread over prime")
    if m.payment_type == "amortizing" and not m.amortization_months and not m.payment_amount:
        raise FormError("Amortizing mortgages need an amortization period or a fixed payment amount")
    if not m.reference:
        m.reference = next_reference()
    if m.ownership_pct is None:
        m.ownership_pct = Decimal(100)
    if m.position is None:
        m.position = 1


def form_context(m):
    return dict(
        m=m,
        property_types=PROPERTY_TYPES,
        statuses=STATUSES,
        frequencies=list(calc.FREQUENCIES),
        compounding=list(calc.COMPOUNDING),
    )


@bp.route("/")
def index():
    prime = latest_prime()
    q = Mortgage.query
    status = request.args.get("status", "open")
    if status == "open":
        q = q.filter(Mortgage.status.in_(("active", "in_arrears", "default", "matured")))
    elif status != "all":
        q = q.filter_by(status=status)
    search = request.args.get("q", "").strip()
    if search:
        like = f"%{search}%"
        q = q.filter(or_(
            Mortgage.borrower_name.ilike(like), Mortgage.property_address.ilike(like),
            Mortgage.reference.ilike(like), Mortgage.property_city.ilike(like), Mortgage.broker_name.ilike(like),
        ))
    sort = request.args.get("sort", "maturity")
    order = {
        "maturity": Mortgage.maturity_date,
        "reference": Mortgage.reference,
        "borrower": Mortgage.borrower_name,
        "rate": Mortgage.interest_rate.desc(),
        "principal": Mortgage.principal_amount.desc(),
    }.get(sort, Mortgage.maturity_date)
    mortgages = q.order_by(order).all()
    rows = [
        dict(m=m, balance=m.balance(), rate=m.effective_rate(prime), payment=m.regular_payment(prime),
             arrears=m.arrears(prime=prime), ltv=m.combined_ltv(), next_due=m.next_due_date())
        for m in mortgages
    ]
    totals = dict(
        balance=sum((r["balance"] for r in rows), Decimal(0)),
        payment=sum((r["m"].monthly_equivalent_payment(prime) for r in rows), Decimal(0)),
        arrears=sum((r["arrears"] for r in rows), Decimal(0)),
    )
    return render_template("mortgages/index.html", rows=rows, totals=totals, status=status, search=search,
                           sort=sort, statuses=STATUSES)


@bp.route("/new", methods=["GET", "POST"])
def new():
    m = Mortgage(status="active", rate_type="fixed", compounding="monthly", payment_type="interest_only",
                 payment_frequency="monthly", position=1, property_province="ON", property_type="detached",
                 ownership_pct=Decimal(100))
    if request.method == "POST":
        try:
            apply_form(m, request.form)
        except FormError as exc:
            flash(str(exc), "danger")
            return render_template("mortgages/form.html", **form_context(m)), 400
        db.session.add(m)
        if request.form.get("record_lender_fee") and m.lender_fee:
            # Lender fees are usually deducted from the advance, so they are income on the funding date.
            db.session.add(MortgageTransaction(
                mortgage=m, date=m.funded_date, type="fee", amount=m.lender_fee, fees=m.lender_fee,
                interest=0, principal=0, notes="Lender fee at funding",
            ))
        audit("mortgage_created", m.reference)
        db.session.commit()
        flash(f"Mortgage {m.reference} created.", "success")
        return redirect(url_for("mortgages.detail", mortgage_id=m.id))
    m.reference = next_reference()
    return render_template("mortgages/form.html", **form_context(m))


@bp.route("/<int:mortgage_id>/edit", methods=["GET", "POST"])
def edit(mortgage_id):
    m = db.get_or_404(Mortgage, mortgage_id)
    if request.method == "POST":
        try:
            with db.session.no_autoflush:
                apply_form(m, request.form)
        except FormError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            m = db.get_or_404(Mortgage, mortgage_id)
            return render_template("mortgages/form.html", **form_context(m)), 400
        audit("mortgage_updated", m.reference)
        db.session.commit()
        flash("Saved.", "success")
        return redirect(url_for("mortgages.detail", mortgage_id=m.id))
    return render_template("mortgages/form.html", **form_context(m))


@bp.route("/<int:mortgage_id>/delete", methods=["POST"])
def delete(mortgage_id):
    m = db.get_or_404(Mortgage, mortgage_id)
    if request.form.get("confirm") != m.reference:
        flash(f"Type {m.reference} to confirm deletion.", "warning")
        return redirect(url_for("mortgages.detail", mortgage_id=m.id))
    for t in m.transactions:
        if t.bank_transaction is not None:
            t.bank_transaction.status = "unmatched" if len(t.bank_transaction.mortgage_transactions) <= 1 else "matched"
    audit("mortgage_deleted", f"{m.reference} {m.borrower_name}")
    db.session.delete(m)
    db.session.commit()
    flash(f"Deleted {m.reference}.", "success")
    return redirect(url_for("mortgages.index"))


@bp.route("/<int:mortgage_id>")
def detail(mortgage_id):
    m = db.get_or_404(Mortgage, mortgage_id)
    prime = latest_prime()
    year_start = date(date.today().year, 1, 1)
    stats = dict(
        balance=m.balance(),
        rate=m.effective_rate(prime),
        payment=m.regular_payment(prime),
        arrears=m.arrears(prime=prime),
        ltv=m.ltv(),
        cltv=m.combined_ltv(),
        next_due=m.next_due_date(),
        last_payment=m.last_payment(),
        days_to_maturity=m.days_to_maturity(),
        ytd=m.income_between(year_start, date.today()),
        lifetime=m.income_between(m.funded_date, date.today()),
        annual_interest=calc.money(m.balance() * m.effective_rate(prime) / 100),
    )
    return render_template("mortgages/detail.html", m=m, s=stats, prime=prime, txn_types=TXN_TYPES,
                           transactions=list(reversed(m.transactions)))


@bp.route("/<int:mortgage_id>/schedule")
def schedule(mortgage_id):
    m = db.get_or_404(Mortgage, mortgage_id)
    prime = latest_prime()
    rows = m.schedule(prime)
    totals = dict(
        interest=sum((r.interest for r in rows), Decimal(0)),
        principal=sum((r.principal for r in rows), Decimal(0)),
        payment=sum((r.payment for r in rows), Decimal(0)),
    )
    return render_template("mortgages/schedule.html", m=m, rows=rows, totals=totals, prime=prime)


@bp.route("/<int:mortgage_id>/split")
def split(mortgage_id):
    """JSON helper used by the payment form to pre-fill the split."""
    m = db.get_or_404(Mortgage, mortgage_id)
    try:
        amount = parse_decimal(request.args.get("amount"), "Amount") or Decimal(0)
        on = parse_date(request.args.get("date"), "Date") or date.today()
    except FormError as exc:
        return jsonify(error=str(exc)), 400
    s = ledger.suggest_split(m, amount, on, request.args.get("type", "payment"), latest_prime())
    return jsonify({k: str(v) for k, v in s.items()})


def transaction_from_form(m, form, txn=None):
    prime = latest_prime()
    txn = txn or MortgageTransaction()
    txn.type = form.get("type", "payment")
    if txn.type not in dict(TXN_TYPES):
        raise FormError("Unknown transaction type")
    txn.date = parse_date(form.get("date"), "Date") or date.today()
    amount = parse_decimal(form.get("amount"), "Amount")
    if amount is None or amount <= 0:
        raise FormError("Amount must be greater than zero")
    txn.amount = calc.money(amount)
    interest = parse_decimal(form.get("interest"), "Interest")
    principal = parse_decimal(form.get("principal"), "Principal")
    fees = parse_decimal(form.get("fees"), "Fees")
    if interest is None and principal is None and fees is None:
        s = ledger.suggest_split(m, txn.amount, txn.date, txn.type, prime)
        interest, principal, fees = s["interest"], s["principal"], s["fees"]
    txn.interest = calc.money(interest)
    txn.principal = calc.money(principal)
    txn.fees = calc.money(fees)
    check = txn.interest + txn.fees + (txn.principal if txn.type != "advance" else -txn.principal)
    if txn.type == "nsf":
        check = -check
    if check != txn.amount:
        raise FormError(f"Interest + principal + fees ({check}) must equal the amount ({txn.amount})")
    txn.notes = (form.get("notes") or "").strip() or None
    txn.mortgage = m
    return txn


@bp.route("/<int:mortgage_id>/transactions", methods=["POST"])
def add_transaction(mortgage_id):
    m = db.get_or_404(Mortgage, mortgage_id)
    try:
        txn = transaction_from_form(m, request.form)
    except FormError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("mortgages.detail", mortgage_id=m.id))
    db.session.add(txn)
    if txn.type == "payout" and m.balance() <= 0:
        m.status = "paid_out"
        flash("Balance is now zero — status set to Paid out.", "info")
    audit("transaction_added", f"{m.reference} {txn.type} {txn.amount} on {txn.date}")
    db.session.commit()
    flash("Transaction recorded.", "success")
    return redirect(url_for("mortgages.detail", mortgage_id=m.id))


@bp.route("/transactions/<int:txn_id>/delete", methods=["POST"])
def delete_transaction(txn_id):
    txn = db.get_or_404(MortgageTransaction, txn_id)
    m = txn.mortgage
    bank = txn.bank_transaction
    audit("transaction_deleted", f"{m.reference} {txn.type} {txn.amount} on {txn.date}")
    if bank is not None:
        bank.mortgage_transactions.remove(txn)
    m.transactions.remove(txn)
    db.session.delete(txn)
    db.session.flush()
    matching.reopen_if_needed(m)
    if bank is not None:
        bank.status = "matched" if matching.remaining(bank) <= 0 else "unmatched"
    db.session.commit()
    flash("Transaction removed." + (" The bank line is back in the unmatched queue." if bank else ""), "success")
    return redirect(url_for("mortgages.detail", mortgage_id=m.id))
