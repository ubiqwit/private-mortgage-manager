"""Sign-in, password changes and user management."""
from datetime import timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for

from .. import db
from ..models import ROLES, AuditLog, User, audit
from ..timeutil import utcnow

bp = Blueprint("auth", __name__)

MAX_FAILURES = 5


class _Dummy:
    """Unknown emails still pay for a password check, so response time doesn't reveal which emails exist."""

    from werkzeug.security import generate_password_hash as _gen

    password_hash = _gen("not-a-real-password")

    def check_password(self, password):
        from werkzeug.security import check_password_hash

        check_password_hash(self.password_hash, password or "")
        return False


_DUMMY_USER = _Dummy()
LOCKOUT = timedelta(minutes=15)


def _recent_failures(email, ip):
    since = utcnow() - LOCKOUT
    q = AuditLog.query.filter(AuditLog.action == "login_failed", AuditLog.at >= since)
    by_email = q.filter(AuditLog.detail == email).count()
    by_ip = q.filter(AuditLog.ip == ip).count() if ip else 0
    return max(by_email, by_ip // 3)  # an IP gets more slack (shared offices)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user"):
        return redirect(url_for("dashboard.index"))
    no_users = db.session.query(User.id).first() is None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if _recent_failures(email, request.remote_addr) >= MAX_FAILURES:
            flash("Too many failed attempts. Wait 15 minutes and try again.", "danger")
            return render_template("login.html", no_users=no_users), 429
        user = User.query.filter_by(email=email).first()
        password_ok = (user or _DUMMY_USER).check_password(request.form.get("password", ""))  # same cost either way
        if user and user.active and password_ok:
            session.clear()
            session.permanent = True
            session["user_id"] = user.id
            session["pw_stamp"] = user.password_stamp
            user.last_login_at = utcnow()
            g.user = user
            audit("login", email)
            db.session.commit()
            target = request.args.get("next") or ""
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("dashboard.index")
            return redirect(target)
        g.user = None
        audit("login_failed", email)
        db.session.commit()
        flash("Incorrect email or password.", "danger")
    return render_template("login.html", no_users=no_users)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account/password", methods=["GET", "POST"])
def change_password():
    if request.method == "POST":
        user = g.user
        if not user.check_password(request.form.get("current", "")):
            flash("Current password is incorrect.", "danger")
        elif request.form.get("new") != request.form.get("confirm"):
            flash("New passwords do not match.", "danger")
        else:
            try:
                user.set_password(request.form.get("new", ""))
            except ValueError as exc:
                flash(str(exc), "danger")
            else:
                session["pw_stamp"] = user.password_stamp  # keep this session; others are signed out
                audit("password_changed", user.email)
                db.session.commit()
                flash("Password updated. Other devices have been signed out.", "success")
                return redirect(url_for("dashboard.index"))
    return render_template("auth/password.html")


def _require_admin():
    if not g.user or not g.user.is_admin:
        abort(403)


@bp.route("/users")
def users():
    _require_admin()
    from ..services.demo import has_demo

    recent = AuditLog.query.order_by(AuditLog.at.desc()).limit(50).all()
    return render_template("auth/users.html", users=User.query.order_by(User.email).all(), roles=ROLES, recent=recent,
                           demo_loaded=has_demo())


@bp.route("/users/new", methods=["POST"])
def create_user():
    _require_admin()
    email = request.form.get("email", "").strip().lower()
    if not email or "@" not in email:
        flash("Enter a valid email.", "danger")
    elif User.query.filter_by(email=email).first():
        flash("That user already exists.", "danger")
    else:
        user = User(email=email, name=request.form.get("name") or None, role=request.form.get("role", "viewer"))
        try:
            user.set_password(request.form.get("password", ""))
        except ValueError as exc:
            flash(str(exc), "danger")
        else:
            db.session.add(user)
            audit("user_created", f"{email} ({user.role})")
            db.session.commit()
            flash(f"Created {email}. Share the password with them securely.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/users/<int:user_id>", methods=["POST"])
def update_user(user_id):
    _require_admin()
    user = db.get_or_404(User, user_id)
    if user.id == g.user.id and (request.form.get("role") != "admin" or not request.form.get("active")):
        flash("You can't demote or disable your own account.", "warning")
        return redirect(url_for("auth.users"))
    user.role = request.form.get("role", user.role)
    user.active = bool(request.form.get("active"))
    if request.form.get("password"):
        try:
            user.set_password(request.form["password"])
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("auth.users"))
    audit("user_updated", f"{user.email} role={user.role} active={user.active}")
    db.session.commit()
    flash(f"Updated {user.email}.", "success")
    return redirect(url_for("auth.users"))


@bp.route("/export/all.xlsx")
def export_all():
    """Admin-only full backup of the data as an Excel workbook."""
    _require_admin()
    from ..services.backup import export_workbook
    from ..timeutil import today

    audit("data_exported", "full workbook")
    db.session.commit()
    return Response(export_workbook(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="mortgage-manager-export-{today():%Y-%m-%d}.xlsx"'})


@bp.route("/demo/load", methods=["POST"])
def load_demo_data():
    """Admin: fill the book with ~5 years of sample mortgages (all referenced DEMO-…)."""
    _require_admin()
    from ..services import demo
    from ..timeutil import today

    if demo.has_demo():
        flash("Demo data is already loaded.", "info")
        return redirect(url_for("auth.users"))
    n = demo.load_demo(today(), user=g.user)
    audit("demo_loaded", f"{n} demo mortgages")
    db.session.commit()
    flash(f"Loaded {n} demo mortgages with five years of history. Remove them before entering real data.", "success")
    return redirect(url_for("dashboard.index"))


@bp.route("/demo/remove", methods=["POST"])
def remove_demo_data():
    _require_admin()
    from ..services import demo

    n = demo.remove_demo()
    audit("demo_removed", f"{n} demo mortgages")
    db.session.commit()
    flash(f"Removed {n} demo mortgages and everything recorded on them.", "success")
    return redirect(url_for("auth.users"))
