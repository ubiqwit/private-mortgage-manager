from flask import Blueprint, render_template

from ..services.demo import has_demo
from ..services.market import PRIME_SERIES, latest, latest_prime, recent_policy_change
from ..services.portfolio import dashboard_stats

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    prime = latest_prime()
    stats = dashboard_stats(prime=prime)
    return render_template("dashboard.html", s=stats, prime_obs=latest(PRIME_SERIES),
                           policy_change=recent_policy_change(days=30), demo_loaded=has_demo())
