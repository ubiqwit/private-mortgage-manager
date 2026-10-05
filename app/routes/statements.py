"""Bank statement upload, column mapping and reconciliation against mortgages."""
import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func

from .. import db
from ..models import TXN_TYPES, BankTransaction, Mortgage, MortgageTransaction, PendingUpload, StatementImport, audit
from ..services import matching
from ..services import statements as st
from ..services.market import latest_prime
from ..services.periods import PeriodClosed, closed_through, ensure_open, is_closed
from ..tenancy import get_owned_or_404
from ..timeutil import utcnow

bp = Blueprint("statements", __name__, url_prefix="/statements")


@bp.route("/")
def index():
    imports = StatementImport.query.order_by(StatementImport.imported_at.desc()).all()
    counts = dict(
        db.session.query(BankTransaction.statement_id, func.count(BankTransaction.id))
        .filter(BankTransaction.status == "matched").group_by(BankTransaction.statement_id).all()
    )
    unmatched_deposits = BankTransaction.query.filter(BankTransaction.status == "unmatched",
                                                      BankTransaction.amount > 0).count()
    accounts = [a for (a,) in db.session.query(StatementImport.account_name).distinct() if a]
    return render_template("statements/index.html", imports=imports, matched_counts=counts,
                           unmatched_deposits=unmatched_deposits, accounts=accounts)


def _import_lines(lines, filename, account, auto=True):
    account = (account or "").strip() or "Main account"
    locked = [ln for ln in lines if is_closed(ln.date)]
    if locked:
        lines = [ln for ln in lines if not is_closed(ln.date)]
        flash(f"Skipped {len(locked)} line(s) dated in months that are closed (through "
              f"{closed_through():%B %Y}). Reopen the month first if they belong in the books.", "warning")
        if not lines:
            raise st.StatementError("Every line in this file is in a closed month — nothing was imported.")
    fps = st.fingerprints(lines, account)
    existing = {fp for (fp,) in db.session.query(BankTransaction.fingerprint).filter(BankTransaction.fingerprint.in_(fps))}
    imp = StatementImport(filename=filename[:255], account_name=account,
                          period_start=min(ln.date for ln in lines), period_end=max(ln.date for ln in lines))
    db.session.add(imp)
    new = []
    for ln, fp in zip(lines, fps, strict=False):
        if fp in existing:
            continue
        existing.add(fp)
        bt = BankTransaction(statement=imp, account_name=account, date=ln.date, description=ln.description[:500] or "(no description)",
                             amount=ln.amount, fingerprint=fp)
        db.session.add(bt)
        new.append(bt)
    imp.row_count = len(new)
    imp.duplicate_count = len(lines) - len(new)
    db.session.flush()
    matched = matching.auto_match(new, latest_prime()) if auto else 0
    audit("statement_imported", f"{filename} ({account}): {len(new)} new, {imp.duplicate_count} duplicates, {matched} auto-matched")
    db.session.commit()
    msg = f"Imported {len(new)} transaction(s) from {filename}."
    if imp.duplicate_count:
        msg += f" Skipped {imp.duplicate_count} already-imported line(s)."
    if matched:
        msg += f" Auto-matched {matched} high-confidence deposit(s) — review them under Matched."
    flash(msg, "success")
    return imp


@bp.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a statement file to upload.", "warning")
        return redirect(url_for("statements.index"))
    content = f.read()
    account = request.form.get("account_name", "")
    try:
        kind = st.file_kind(f.filename, content)
        if kind == "ofx":
            lines, acct_id = st.parse_ofx(content)
            imp = _import_lines(lines, f.filename, account or (f"Account …{acct_id[-4:]}" if acct_id else ""),
                                auto=bool(request.form.get("auto_match")))
            return redirect(url_for("statements.reconcile", statement=imp.id))
        st.read_rows(f.filename, content)  # validate early (PDF, empty, unreadable)
    except st.StatementError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("statements.index"))

    # Keep the raw file in the database until the column mapping is confirmed.
    PendingUpload.query.filter(PendingUpload.created_at < utcnow() - timedelta(days=1)).delete()
    token = secrets.token_urlsafe(24)
    db.session.add(PendingUpload(token=token, user_id=g.user.id, filename=f.filename[:255], content=content))
    db.session.commit()
    return redirect(url_for("statements.map_columns", token=token, account=account,
                            auto=1 if request.form.get("auto_match") else 0))


def _mapping_from_form(form, base: st.Mapping) -> st.Mapping:
    def col(name):
        v = form.get(name, "")
        return int(v) if v.strip().lstrip("-").isdigit() and int(v) >= 0 else None

    m = st.Mapping()
    m.date = col("date")
    m.description = [c for c in (col("description"), col("description2")) if c is not None]
    m.amount = col("amount")
    m.debit = col("debit")
    m.credit = col("credit")
    m.date_format = form.get("date_format") or None
    m.has_header = bool(form.get("has_header"))
    m.header_row = col("header_row") or 0 if m.has_header else 0
    m.flip_sign = bool(form.get("flip_sign"))
    if m.amount is not None:
        m.debit = m.credit = None
    return m


@bp.route("/map/<token>", methods=["GET", "POST"])
def map_columns(token):
    pending = PendingUpload.query.filter_by(token=token).first()
    if not pending or pending.user_id != g.user.id:
        flash("That upload has expired — please upload the file again.", "warning")
        return redirect(url_for("statements.index"))
    rows = st.read_rows(pending.filename, pending.content)
    mapping = st.guess_mapping(rows)
    account = request.values.get("account", "")
    auto = request.values.get("auto", "1") == "1"
    if request.method == "POST":
        mapping = _mapping_from_form(request.form, mapping)
        account = request.form.get("account_name", "")
        auto = bool(request.form.get("auto_match"))
    lines, warnings, error = [], [], None
    try:
        lines, warnings = st.apply_mapping(rows, mapping)
    except st.StatementError as exc:
        error = str(exc)
    if request.method == "POST" and request.form.get("action") == "import" and not error:
        try:
            imp = _import_lines(lines, pending.filename, account, auto=auto)
        except st.StatementError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return redirect(url_for("statements.index"))
        db.session.delete(pending)
        db.session.commit()
        return redirect(url_for("statements.reconcile", statement=imp.id))
    width = max(len(r) for r in rows)
    header = rows[mapping.header_row] if mapping.has_header else None
    columns = [(i, (header[i] if header and i < len(header) and header[i] else f"Column {i + 1}")) for i in range(width)]
    deposits = sum((ln.amount for ln in lines if ln.amount > 0), Decimal(0))
    withdrawals = sum((ln.amount for ln in lines if ln.amount < 0), Decimal(0))
    return render_template("statements/map.html", pending=pending, rows=rows[:8], columns=columns, m=mapping,
                           lines=lines, warnings=warnings, error=error, account=account, auto=auto,
                           date_formats=st.DATE_FORMATS, deposits=deposits, withdrawals=withdrawals)


@bp.route("/<int:statement_id>/delete", methods=["POST"])
def delete_statement(statement_id):
    imp = get_owned_or_404(StatementImport, statement_id)
    try:
        ensure_open(*(bt.date for bt in imp.transactions), action="delete a statement with lines in a closed month")
    except PeriodClosed as exc:
        flash(str(exc), "danger")
        return redirect(url_for("statements.index"))
    removed = 0
    for bt in imp.transactions:
        removed += len(bt.mortgage_transactions)
        matching.unmatch(bt)
    audit("statement_deleted", f"{imp.filename}: {len(imp.transactions)} lines, {removed} mortgage transactions removed")
    db.session.delete(imp)
    db.session.commit()
    flash(f"Deleted {imp.filename}" + (f" and the {removed} mortgage transaction(s) recorded from it." if removed else "."), "success")
    return redirect(url_for("statements.index"))


@bp.route("/reconcile")
def reconcile():
    status = request.args.get("status", "unmatched")
    direction = request.args.get("direction", "in")
    statement_id = request.args.get("statement", type=int)
    month = request.args.get("month", "")
    q = BankTransaction.query
    if status != "all":
        q = q.filter(BankTransaction.status == status)
    if direction == "in":
        q = q.filter(BankTransaction.amount > 0)
    elif direction == "out":
        q = q.filter(BankTransaction.amount < 0)
    if statement_id:
        q = q.filter(BankTransaction.statement_id == statement_id)
    if month:
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError:
            abort(400)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        q = q.filter(BankTransaction.date.between(start, end))
    lines = q.order_by(BankTransaction.date.desc(), BankTransaction.id.desc()).limit(500).all()
    prime = latest_prime()
    mortgages = matching.candidate_mortgages()
    rows = []
    for bt in lines:
        sugg = matching.suggestions(bt, mortgages, prime) if bt.status == "unmatched" else []
        rows.append(dict(bt=bt, suggestions=sugg, remaining=matching.remaining(bt)))
    high = sum(1 for r in rows if r["suggestions"] and r["suggestions"][0].confidence == "high")
    counts = dict(db.session.query(BankTransaction.status, func.count(BankTransaction.id))
                  .filter(BankTransaction.amount > 0).group_by(BankTransaction.status).all())
    all_mortgages = Mortgage.query.order_by(Mortgage.reference).all()
    statements_list = StatementImport.query.order_by(StatementImport.imported_at.desc()).all()
    return render_template("statements/reconcile.html", rows=rows, status=status, direction=direction,
                           statement_id=statement_id, month=month, high=high, counts=counts,
                           mortgages=all_mortgages, txn_types=TXN_TYPES, statements=statements_list)


def _back():
    target = request.form.get("next") or ""
    if not target.startswith("/") or target.startswith("//"):
        target = url_for("statements.reconcile")
    return redirect(target)


@bp.route("/lines/<int:line_id>/allocate", methods=["POST"])
def allocate(line_id):
    bt = get_owned_or_404(BankTransaction, line_id)
    m = get_owned_or_404(Mortgage, request.form.get("mortgage_id", type=int))
    txn_type = request.form.get("type", "payment")
    if txn_type not in dict(TXN_TYPES):
        abort(400)
    try:
        ensure_open(bt.date, action="match this line")
    except PeriodClosed as exc:
        flash(str(exc), "danger")
        return _back()
    amount = request.form.get("amount")
    existing = None
    if request.form.get("existing_id"):
        existing = get_owned_or_404(MortgageTransaction, request.form.get("existing_id", type=int))
    try:
        amount = Decimal(amount.replace(",", "").replace("$", "")) if amount else None
        txn = matching.allocate(bt, m, txn_type, amount=amount, remember=bool(request.form.get("remember", "1")),
                                prime=latest_prime(), existing=existing)
    except (ValueError, ArithmeticError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return _back()
    audit("bank_line_matched", f"{bt.date} {bt.amount} → {m.reference} {txn_type} {txn.amount}")
    db.session.commit()
    left = matching.remaining(bt)
    prefix = f"Linked to the transaction you recorded on {txn.date:%b %d} (no duplicate created). " if existing else ""
    flash(prefix + f"Matched {txn.amount:,.2f} to {m.reference} as {dict(TXN_TYPES)[txn.type].lower()} "
          f"(interest {txn.interest:,.2f}, principal {txn.principal:,.2f}, fees {txn.fees:,.2f})."
          + (f" {left:,.2f} is still unallocated on this line." if left > 0 else ""), "success")
    return _back()


@bp.route("/auto-match", methods=["POST"])
def auto_match():
    lines = [bt for bt in BankTransaction.query.filter_by(status="unmatched") if not is_closed(bt.date)]
    n = matching.auto_match(lines, latest_prime())
    audit("auto_match", f"{n} lines")
    db.session.commit()
    flash(f"Matched {n} high-confidence line(s)." if n else "No high-confidence matches to accept.", "success" if n else "info")
    return _back()


@bp.route("/lines/<int:line_id>/ignore", methods=["POST"])
def ignore(line_id):
    bt = get_owned_or_404(BankTransaction, line_id)
    if bt.mortgage_transactions:
        flash("Unmatch this line before ignoring it.", "warning")
        return _back()
    try:
        ensure_open(bt.date, action="change this line")
    except PeriodClosed as exc:
        flash(str(exc), "danger")
        return _back()
    bt.status = "ignored"
    db.session.commit()
    return _back()


@bp.route("/lines/<int:line_id>/restore", methods=["POST"])
def restore(line_id):
    bt = get_owned_or_404(BankTransaction, line_id)
    try:
        ensure_open(bt.date, *(t.date for t in bt.mortgage_transactions), action="unmatch this line")
    except PeriodClosed as exc:
        flash(str(exc), "danger")
        return _back()
    if bt.mortgage_transactions:
        audit("bank_line_unmatched", f"{bt.date} {bt.amount} {bt.description[:80]}")
    matching.unmatch(bt)
    db.session.commit()
    flash("Line is back in the unmatched queue.", "info")
    return _back()
