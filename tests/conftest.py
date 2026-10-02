import pytest

from app import create_app, db
from app.models import User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
        "MARKET_FETCH_ENABLED": False,
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        yield app
        db.session.remove()


def make_user(email="owner@example.com", role="admin"):
    user = User(email=email, role=role)
    user.set_password(PASSWORD)
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
