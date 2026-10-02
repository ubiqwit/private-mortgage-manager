"""Activity log and follow-up reminders per mortgage."""
from datetime import datetime

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .. import db
from ..models import ACTIVITY_KINDS, OPEN_STATUSES, Mortgage, MortgageActivity, audit
from ..timeutil import today as local_today

bp = Blueprint("activity", __name__, url_prefix="/mortgages")


def _back(m):
    target = request.form.get("next") or ""
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for("mortgages.detail", mortgage_id=m.id, _anchor="activity"))


@bp.route("/<int:mortgage_id>/activity", methods=["POST"])
def add(mortgage_id):
    m = db.get_or_404(Mortgage, mortgage_id)
    body = (request.form.get("body") or "").strip()
    if not body:
        flash("Write something for the activity entry.", "warning")
        return _back(m)
    kind = request.form.get("kind", "note")
    follow_up = None
    if request.form.get("follow_up_on"):
        try:
            follow_up = datetime.strptime(request.form["follow_up_on"], "%Y-%m-%d").date()
        except ValueError:
            flash("Follow-up date must be YYYY-MM-DD.", "danger")
            return _back(m)
    db.session.add(MortgageActivity(mortgage=m, kind=kind if kind in dict(ACTIVITY_KINDS) else "note",
                                    body=body[:5000], follow_up_on=follow_up, user=g.user))
    audit("activity_added", f"{m.reference}: {kind}" + (f", follow up {follow_up}" if follow_up else ""))
    db.session.commit()
    flash("Added to the activity log." + (f" Follow-up set for {follow_up:%b %d}." if follow_up else ""), "success")
    return _back(m)


@bp.route("/activity/<int:activity_id>/done", methods=["POST"])
def toggle_done(activity_id):
    a = db.get_or_404(MortgageActivity, activity_id)
    a.done = not a.done
    db.session.commit()
    return _back(a.mortgage)


@bp.route("/activity/<int:activity_id>/delete", methods=["POST"])
def delete(activity_id):
    a = db.get_or_404(MortgageActivity, activity_id)
    m = a.mortgage
    audit("activity_deleted", f"{m.reference}: {a.kind} from {a.created_at:%Y-%m-%d}")
    db.session.delete(a)
    db.session.commit()
    return _back(m)


@bp.route("/follow-ups")
def follow_ups():
    """Every open follow-up across the book, soonest first."""
    items = (MortgageActivity.query.join(Mortgage)
             .filter(MortgageActivity.follow_up_on.isnot(None), MortgageActivity.done.is_(False))
             .order_by(MortgageActivity.follow_up_on).all())
    return render_template("mortgages/follow_ups.html", items=items, today=local_today(), open_statuses=OPEN_STATUSES)
