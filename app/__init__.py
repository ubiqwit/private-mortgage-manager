"""Private Mortgage Manager — Flask application factory."""
import os
import warnings
from datetime import date, datetime
from decimal import Decimal

from flask import Flask, redirect, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import exc as sa_exc

# SQLite has no native DECIMAL; SQLAlchemy round-trips Numeric(…, 2) safely but warns.
warnings.filterwarnings("ignore", category=sa_exc.SAWarning, message=".*Decimal objects natively.*")

db = SQLAlchemy()


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(
        SECRET_KEY=os.environ.get("PMM_SECRET_KEY", "change-me-in-production"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "PMM_DATABASE_URL", "sqlite:///" + os.path.join(app.instance_path, "mortgages.db")
        ),
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        # Optional single-user password. Leave unset when running only on localhost.
        APP_PASSWORD=os.environ.get("PMM_PASSWORD"),
        # Disable outbound calls for live market data (tests, offline use).
        MARKET_FETCH_ENABLED=os.environ.get("PMM_MARKET_FETCH", "1") == "1",
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)

    from . import models  # noqa: F401  (register models)

    with app.app_context():
        db.create_all()

    _register_filters(app)
    _register_auth(app)

    from .routes import register_blueprints

    register_blueprints(app)
    return app


def _register_filters(app):
    @app.template_filter("money")
    def money(value, cents=True):
        if value is None or value == "":
            return "—"
        value = Decimal(str(value))
        sign = "-" if value < 0 else ""
        fmt = "{:,.2f}" if cents else "{:,.0f}"
        return f"{sign}${fmt.format(abs(value))}"

    @app.template_filter("pct")
    def pct(value, places=2):
        if value is None or value == "":
            return "—"
        return f"{Decimal(str(value)):.{places}f}%"

    @app.template_filter("d")
    def fmt_date(value):
        if not value:
            return "—"
        if isinstance(value, (date, datetime)):
            return value.strftime("%b %d, %Y")
        return str(value)

    @app.context_processor
    def inject_globals():
        return {"today": date.today(), "blueprints_loaded": set(app.blueprints)}


def _register_auth(app):
    @app.before_request
    def require_login():
        if not app.config.get("APP_PASSWORD"):
            return None
        if request.endpoint in ("auth.login", "static") or session.get("authed"):
            return None
        return redirect(url_for("auth.login", next=request.path))
