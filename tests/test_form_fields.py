from decimal import Decimal

from app import db
from app.models import Mortgage, User
from app.services import form_fields as ff

from .conftest import PASSWORD, make_user
from .test_mortgages import BASE, create


def test_default_shows_everything_and_presets_hide_fields(client):
    page = client.get("/mortgages/new").data
    assert b'name="guarantors"' in page and b'name="pin"' in page
    client.post("/account/fields", data={"preset": "simple"})
    page = client.get("/mortgages/new").data
    assert b'name="guarantors"' not in page and b'name="pin"' not in page and b"Fees &amp; parties" in page
    for required in ff.REQUIRED:
        assert f'name="{required}"'.encode() in page
    assert b"Simple form" in page
    assert b"Security (property)" in page  # section stays because it has visible fields


def test_hidden_fields_keep_their_values_on_edit(client):
    m = create(client, guarantors="Pat Smith", pin="12345-0001")
    client.post("/account/fields", data={"preset": "simple"})
    simple = {k: v for k, v in BASE.items() if k in ff.SIMPLE}
    resp = client.post(f"/mortgages/{m.id}/edit", data={**simple, "interest_rate": "11"})
    assert resp.status_code == 302
    m = db.session.get(Mortgage, m.id)
    assert m.interest_rate == Decimal("11") and m.guarantors == "Pat Smith" and m.pin == "12345-0001"


def test_new_mortgage_with_simple_form_uses_defaults(client):
    client.post("/account/fields", data={"preset": "simple"})
    data = {k: v for k, v in BASE.items() if k in ff.SIMPLE}
    assert client.post("/mortgages/new", data=data).status_code == 302
    m = Mortgage.query.one()
    assert (m.rate_type, m.payment_type, m.payment_frequency, m.status) == ("fixed", "interest_only", "monthly", "active")


def test_custom_choice_and_required_always_kept(client):
    client.post("/account/fields", data={"fields": ["guarantors"]})
    user = User.query.filter_by(email="owner@example.com").one()
    assert ff.visible_fields(user) == ff.REQUIRED | {"guarantors"}
    assert ff.preset_name(user) is None


def test_admin_sets_another_users_fields(app, client):
    other = make_user("assistant@example.com", role="editor")
    client.post(f"/users/{other.id}/fields", data={"preset": "standard"})
    assert ff.preset_name(db.session.get(User, other.id)) == "standard"
    c = app.test_client()
    c.post("/login", data={"email": "assistant@example.com", "password": PASSWORD})
    assert c.post(f"/users/{other.id}/fields", data={"preset": "simple"}).status_code == 403
    assert c.get("/account/fields").status_code == 200
