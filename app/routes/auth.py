"""Optional single-password login (enabled when PMM_PASSWORD is set)."""
import hmac

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        expected = current_app.config.get("APP_PASSWORD") or ""
        if hmac.compare_digest(request.form.get("password", ""), expected):
            session["authed"] = True
            target = request.args.get("next") or ""
            # Only allow local redirects.
            if not target.startswith("/") or target.startswith("//"):
                target = url_for("dashboard.index")
            return redirect(target)
        flash("Incorrect password.", "danger")
    return render_template("login.html")


@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
