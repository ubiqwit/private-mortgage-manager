from flask import Blueprint, render_template

from ..services.market import PRIME_SERIES, latest, latest_prime
from ..services.portfolio import dashboard_stats

bp = Blueprint("dashboard", __name__)


@bp.route("/")
def index():
    prime = latest_prime()
    stats = dashboard_stats(prime=prime)
    return render_template("dashboard.html", s=stats, prime_obs=latest(PRIME_SERIES))
