from datetime import date

from app.models import Mortgage, MortgageActivity, MortgageTransaction
from app.services import demo
from app.services.portfolio import dashboard_stats

from .conftest import PASSWORD, make_user


def test_demo_book_is_varied_and_consistent(app):
    today = date(2026, 10, 3)
    assert demo.load_demo(today) == 20
    ms = Mortgage.query.all()
    assert all(m.reference.startswith("DEMO-") for m in ms)
    assert min(m.funded_date for m in ms) <= date(2021, 12, 31)  # ~5 years of history
    assert {m.position for m in ms} == {1, 2, 3}
    assert {m.rate_type for m in ms} == {"fixed", "variable"}
    assert {m.payment_type for m in ms} == {"interest_only", "amortizing"}
    assert {"monthly", "biweekly"} <= {m.payment_frequency for m in ms}
    assert {"active", "paid_out", "in_arrears", "default"} <= {m.status for m in ms}
    assert any(m.term_history for m in ms)
    assert all(m.balance() == 0 for m in ms if m.status == "paid_out")
    assert all(m.balance() >= 0 for m in ms)
    # No duplicate regular payments on the same date for a mortgage.
    for m in ms:
        days = [t.date for t in m.transactions if t.type == "payment"]
        assert len(days) == len(set(days)), m.reference
    stats = dashboard_stats(today=today)
    assert stats["arrears_count"] >= 2 and stats["count"] >= 15
    assert MortgageActivity.query.count() >= 3


def test_load_and_remove_buttons(app, client):
    assert client.post("/demo/load").status_code == 302
    assert Mortgage.query.count() == 20
    assert b"demo data" in client.get("/").data
    client.post("/demo/load")  # second click does nothing
    assert Mortgage.query.count() == 20
    assert client.post("/demo/remove").status_code == 302
    assert Mortgage.query.count() == 0 and MortgageTransaction.query.count() == 0


def test_demo_buttons_admin_only(app):
    make_user("e@example.com", role="editor")
    c = app.test_client()
    c.post("/login", data={"email": "e@example.com", "password": PASSWORD})
    assert c.post("/demo/load").status_code == 403
