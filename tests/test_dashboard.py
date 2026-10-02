from datetime import date
from decimal import Decimal

from app.cli import _seed_demo
from app.models import Mortgage
from app.services.portfolio import dashboard_stats


def test_empty_dashboard(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"mortgage book is empty" in resp.data


def test_dashboard_with_demo_book(app, client):
    _seed_demo(today=date(2026, 10, 2))
    stats = dashboard_stats(today=date(2026, 10, 2))
    assert stats["count"] == 8
    assert stats["total_balance"] == sum((m.balance() for m in Mortgage.query.all()), Decimal(0))
    # The demo book has exactly one borrower two payments behind.
    assert stats["arrears_count"] == 1
    assert any("arrears" in text for _, _, text in stats["alerts"])
    assert len(stats["income_chart"]["labels"]) == 12
    assert sum(stats["ladder"]["values"]) == float(stats["total_balance"])
    assert 0 < stats["wavg_rate"] < 20
    resp = client.get("/")
    assert resp.status_code == 200 and b"Capital deployed" in resp.data
    assert client.get("/mortgages/").status_code == 200


def test_upcoming_payments_skip_ones_already_received(client):
    from .test_mortgages import create

    m = create(client, record_lender_fee="")  # first payment Feb 1 2026
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": "2026-01-30", "amount": "1666.67"})
    stats = dashboard_stats(today=date(2026, 1, 25))
    assert [d for d, _, _ in stats["upcoming"]] == [date(2026, 2, 1)]  # Jan 30 payment not yet "received" on Jan 25
    stats = dashboard_stats(today=date(2026, 2, 1))
    assert [d for d, _, _ in stats["upcoming"]] == [date(2026, 3, 1)]  # Feb 1 was paid early, so only March shows
