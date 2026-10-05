"""Switch between companies, and (admins) create, rename and assign them."""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, session, url_for
from sqlalchemy import func

from .. import db
from ..models import Company, Mortgage, User, audit
from ..tenancy import all_companies

bp = Blueprint("companies", __name__, url_prefix="/companies")


def _require_admin():
    if not g.user or not g.user.is_admin:
        abort(403)


def _safe_next(default):
    target = request.form.get("next") or request.args.get("next") or ""
    return target if target.startswith("/") and not target.startswith("//") else default


def _end(name):
    """Finish a sentence with a company name without doubling its full stop ("Inc.")."""
    return name if name.endswith(".") else name + "."

@bp.route("/switch", methods=["POST"])
def switch():
    company = db.get_or_404(Company, request.form.get("company_id", type=int))
    if company not in g.user.accessible_companies():
        abort(403)
    session["company_id"] = company.id
    flash(f"Now working in {_end(company.name)}", "info")
    # Stay on the same kind of page, but never on a record that belongs to the old company.
    target = _safe_next(url_for("dashboard.index"))
    for section in ("/mortgages/", "/statements/", "/reports/"):
        if target.startswith(section) and any(ch.isdigit() for ch in target[len(section):].split("?")[0]):
            target = url_for("dashboard.index")
    return redirect(target)


@bp.route("/")
def index():
    _require_admin()
    companies = Company.query.order_by(Company.name).all()
    counts = dict(db.session.execute(all_companies(
        db.select(Mortgage.company_id, func.count(Mortgage.id)).group_by(Mortgage.company_id))).all())
    users = User.query.order_by(User.email).all()
    return render_template("companies/index.html", companies=companies, counts=counts, users=users)


@bp.route("/new", methods=["POST"])
def create():
    _require_admin()
    name = (request.form.get("name") or "").strip()[:200]
    if not name:
        flash("Enter a company name.", "warning")
    elif Company.query.filter(func.lower(Company.name) == name.lower()).first():
        flash(f"{name} already exists.", "warning")
    else:
        company = Company(name=name)
        db.session.add(company)
        audit("company_created", name)
        db.session.commit()
        if request.form.get("switch"):
            session["company_id"] = company.id
        flash(f"Created {_end(name)}" + (" You're now working in it." if request.form.get("switch") else ""), "success")
    return redirect(url_for("companies.index"))


@bp.route("/<int:company_id>/rename", methods=["POST"])
def rename(company_id):
    _require_admin()
    company = db.get_or_404(Company, company_id)
    name = (request.form.get("name") or "").strip()[:200]
    clash = Company.query.filter(func.lower(Company.name) == name.lower(), Company.id != company.id).first()
    if not name or clash:
        flash("That name is empty or already used.", "warning")
    else:
        audit("company_renamed", f"{company.name} → {name}")
        company.name = name
        db.session.commit()
        flash(f"Renamed to {_end(name)}", "success")
    return redirect(url_for("companies.index"))


@bp.route("/<int:company_id>/delete", methods=["POST"])
def delete(company_id):
    """Only an empty company can be deleted — mortgages are never removed this way."""
    _require_admin()
    from ..models import StatementImport

    company = db.get_or_404(Company, company_id)
    used = db.session.execute(all_companies(db.select(Mortgage.id).where(Mortgage.company_id == company.id))).first() \
        or db.session.execute(all_companies(db.select(StatementImport.id).where(
            StatementImport.company_id == company.id))).first()
    if used:
        flash(f"{company.name} still has mortgages or statements, so it can't be deleted.", "warning")
    elif Company.query.count() == 1:
        flash("You need at least one company.", "warning")
    else:
        audit("company_deleted", company.name)
        db.session.delete(company)
        db.session.commit()
        if session.get("company_id") == company_id:
            session.pop("company_id", None)
        flash(f"Deleted {_end(company.name)}", "success")
    return redirect(url_for("companies.index"))


@bp.route("/access", methods=["POST"])
def access():
    """Which companies each non-admin user may work in (admins always see all)."""
    _require_admin()
    companies = Company.query.all()
    for user in User.query.filter(User.role != "admin"):
        chosen = {int(v) for v in request.form.getlist(f"user_{user.id}")}
        user.companies = [c for c in companies if c.id in chosen]
    audit("company_access_changed", "")
    db.session.commit()
    flash("Access updated.", "success")
    return redirect(url_for("companies.index"))


@bp.route("/choose")
def choose():
    """Company picker (used on phones, where the sidebar switcher is hidden)."""
    return render_template("companies/choose.html", companies=g.user.accessible_companies())
