"""Market & rates: live Bank of Canada rates, rate decisions (hikes and cuts) and news."""
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from dateutil.relativedelta import relativedelta
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for

from .. import db
from ..models import NewsItem, Setting, audit
from ..services import market
from ..timeutil import today as local_today

bp = Blueprint("market", __name__, url_prefix="/market")


@bp.route("/")
def index():
    today = local_today()
    cards = [dict(s=s, obs=market.latest(s.id), d30=market.change_over(s.id, 30), d365=market.change_over(s.id, 365))
             for s in market.SERIES]
    news = NewsItem.query.order_by(NewsItem.published.desc().nullslast()).limit(40).all()
    errors = [e for e in (Setting.get("market_last_errors") or "").splitlines() if e]
    return render_template(
        "market/index.html",
        cards=cards,
        changes=market.rate_changes(market.POLICY_SERIES, since=today - relativedelta(years=4))[:12],
        recent=market.recent_policy_change(),
        news=news,
        last_fetch=market.last_fetch(),
        stale=market.is_stale() and current_app.config.get("MARKET_FETCH_ENABLED", True),
        errors=errors,
        fetch_enabled=current_app.config.get("MARKET_FETCH_ENABLED", True),
    )


@bp.route("/refresh", methods=["POST"])
def refresh():
    if not current_app.config.get("MARKET_FETCH_ENABLED", True):
        abort(404)
    result = market.refresh(force=bool(request.form.get("force") or request.args.get("force")))
    if request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With"):
        added = sum(result["series"].values()) + result["news"]
        return jsonify(added=added, errors=result["errors"])
    added = sum(result["series"].values())
    msg = f"Updated market data: {added} new rate observation(s), {result['news']} news item(s)."
    flash(msg, "success" if not result["errors"] else "warning")
    if result["errors"]:
        flash("Some sources could not be reached: " + "; ".join(result["errors"][:4]), "warning")
    return redirect(url_for("market.index"))


@bp.route("/manual", methods=["POST"])
def manual():
    """Enter prime (or another series) by hand, e.g. when the server can't reach the Bank of Canada."""
    series = request.form.get("series", market.PRIME_SERIES)
    if series not in market.SERIES_BY_ID:
        abort(400)
    try:
        value = Decimal(request.form.get("value", "").replace("%", "").strip())
        when = datetime.strptime(request.form.get("date", ""), "%Y-%m-%d").date()
    except (InvalidOperation, ValueError):
        flash("Enter a rate and a date.", "danger")
        return redirect(url_for("market.index"))
    if not Decimal("-5") < value < Decimal("50") or when > local_today() + timedelta(days=1):
        flash("That rate or date doesn't look right.", "danger")
        return redirect(url_for("market.index"))
    market.store_observations(series, [(when, value)], source="manual")
    audit("market_manual", f"{series} {value} on {when}")
    db.session.commit()
    flash(f"Saved {market.SERIES_BY_ID[series].short} = {value}% from {when:%b %d, %Y}.", "success")
    return redirect(url_for("market.index"))
