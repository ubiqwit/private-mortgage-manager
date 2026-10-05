import os

import pytest

from app import create_app, db
from app.models import User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app(tmp_path):
    # Set PMM_TEST_DATABASE_URL=postgresql+psycopg://… to run the suite against PostgreSQL.
    url = os.environ.get("PMM_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": url,
        "MARKET_FETCH_ENABLED": False,
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        yield app
        db.session.remove()
        if url.startswith("postgresql"):
            db.drop_all()


def make_user(email="owner@example.com", role="admin", companies=None):
    from app.tenancy import ensure_default_company

    user = User(email=email, role=role)
    user.set_password(PASSWORD)
    user.companies = companies if companies is not None else [ensure_default_company()]
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def anon_client(app):
    return app.test_client()


@pytest.fixture
def client(app):
    """A client signed in as an admin."""
    make_user()
    c = app.test_client()
    resp = c.post("/login", data={"email": "owner@example.com", "password": PASSWORD})
    assert resp.status_code == 302
    return c
