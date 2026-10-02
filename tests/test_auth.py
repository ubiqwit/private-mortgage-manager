from app.models import User

from .conftest import PASSWORD, make_user


def test_login_required(anon_client):
    resp = anon_client.get("/")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert anon_client.get("/healthz").status_code == 200


def test_login_and_logout(client):
    assert client.get("/").status_code == 200
    client.post("/logout")
    assert client.get("/").status_code == 302


def test_bad_password_and_lockout(app, anon_client):
    make_user()
    for _ in range(5):
        r = anon_client.post("/login", data={"email": "owner@example.com", "password": "wrong"})
        assert r.status_code == 200
    r = anon_client.post("/login", data={"email": "owner@example.com", "password": PASSWORD})
    assert r.status_code == 429


def test_open_redirect_blocked(app, anon_client):
    make_user()
    r = anon_client.post("/login?next=//evil.com", data={"email": "owner@example.com", "password": PASSWORD})
    assert r.headers["Location"] == "/"


def test_viewer_is_read_only(app):
    make_user("acct@example.com", role="viewer")
    c = app.test_client()
    c.post("/login", data={"email": "acct@example.com", "password": PASSWORD})
    assert c.get("/").status_code == 200
    assert c.post("/users/new", data={"email": "x@example.com", "password": "x" * 12}).status_code == 403


def test_password_change_signs_out_other_sessions(app, client):
    other = app.test_client()
    other.post("/login", data={"email": "owner@example.com", "password": PASSWORD})
    assert other.get("/").status_code == 200
    r = client.post("/account/password", data={"current": PASSWORD, "new": "another-long-pass", "confirm": "another-long-pass"})
    assert r.status_code == 302
    assert client.get("/").status_code == 200
    assert other.get("/").status_code == 302


def test_admin_creates_user(app, client):
    client.post("/users/new", data={"email": "partner@example.com", "role": "editor", "password": "x" * 12})
    assert User.query.filter_by(email="partner@example.com").one().role == "editor"


def test_csrf_enforced(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/users/new", data={"email": "a@b.co", "password": "x" * 12}).status_code == 400
