"""Private Mortgage Manager — Flask application factory."""
import os
import secrets
import warnings
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Flask, abort, g, redirect, request, session, url_for
from markupsafe import Markup
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import exc as sa_exc
from werkzeug.middleware.proxy_fix import ProxyFix

# SQLite has no native DECIMAL; SQLAlchemy round-trips Numeric(…, 2) safely but warns.
warnings.filterwarnings("ignore", category=sa_exc.SAWarning, message=".*Decimal objects natively.*")

db = SQLAlchemy()

# Endpoints reachable without signing in.
PUBLIC_ENDPOINTS = {"auth.login", "static", "health"}
# POST endpoints a read-only user (e.g. your accountant) may still use.
VIEWER_POST_ENDPOINTS = {"auth.logout", "auth.change_password"}


def _database_url(instance_path):
    url = os.environ.get("DATABASE_URL") or os.environ.get("PMM_DATABASE_URL")
    if not url:
        return "sqlite:///" + os.path.join(instance_path, "mortgages.db")
    # Heroku/Render style URLs → SQLAlchemy + psycopg 3 driver.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    production = os.environ.get("PMM_ENV", "development") == "production"
    app.config.update(
        PRODUCTION=production,
        SECRET_KEY=os.environ.get("PMM_SECRET_KEY") or os.environ.get("SECRET_KEY"),
        SQLALCHEMY_DATABASE_URI=_database_url(app.instance_path),
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        # Disable outbound calls for live market data (tests, offline use).
        MARKET_FETCH_ENABLED=os.environ.get("PMM_MARKET_FETCH", "1") == "1",
        WTF_CSRF_ENABLED=True,
    )
    if test_config:
        app.config.update(test_config)

    if not app.config["SECRET_KEY"]:
        if production:
            raise RuntimeError("PMM_SECRET_KEY must be set when PMM_ENV=production")
        app.config["SECRET_KEY"] = _dev_secret(app.instance_path)

    if production:
        # Trust X-Forwarded-* from the hosting platform's load balancer (HTTPS detection).
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_app(app)

    from . import models  # noqa: F401  (register models)

    with app.app_context():
        db.create_all()
        models.bootstrap_admin_from_env()
        # Don't share pooled connections with forked gunicorn workers (--preload).
        db.engine.dispose()

    _register_filters(app)
    _register_security(app)

    from .routes import register_blueprints

    register_blueprints(app)

    from .cli import register_cli

    register_cli(app)

    @app.route("/healthz")
    def health():
        return {"status": "ok"}

    return app


def _dev_secret(instance_path):
    """Stable per-install secret for local development."""
    path = os.path.join(instance_path, ".secret_key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


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
        return {
            "today": date.today(),
            "blueprints_loaded": set(app.blueprints),
            "current_user": g.get("user"),
            "csrf_token": csrf_token,
            "csrf_field": csrf_field,
        }


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def csrf_field():
    return Markup(f'<input type="hidden" name="_csrf" value="{csrf_token()}">')


def _register_security(app):
    from .models import User

    @app.before_request
    def load_user_and_check():
        g.user = None
        uid = session.get("user_id")
        if uid is not None:
            user = db.session.get(User, uid)
            # Invalidate sessions when the password changes or the user is disabled.
            if user and user.active and session.get("pw_stamp") == user.password_stamp:
                g.user = user
            else:
                session.clear()

        if request.endpoint not in PUBLIC_ENDPOINTS and g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))

        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if app.config.get("WTF_CSRF_ENABLED", True):
                sent = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
                if not sent or not secrets.compare_digest(sent, session.get("_csrf", "")):
                    abort(400, "Your session expired or the form was tampered with. Go back, refresh and try again.")
            if g.user is not None and g.user.role == "viewer" and request.endpoint not in VIEWER_POST_ENDPOINTS:
                abort(403, "Your account is read-only.")

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if app.config.get("PRODUCTION"):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if g.get("user") is not None:
            # Financial data: never cache authenticated pages in shared caches.
            resp.headers.setdefault("Cache-Control", "private, no-store")
        return resp
