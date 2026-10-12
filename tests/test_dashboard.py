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
    assert stats["monthly_interest"] > 0 and stats["ytd"]["interest"] > 0
    page = client.get("/").get_data(as_text=True)
    for shown in ("Capital deployed", "Monthly interest", "Collected", "Income year to date", "Arrears",
                  "Needs attention"):
        assert shown in page
    for gone in ("Weighted avg", "Maturity ladder", "Concentration", "combined LTV", "Payments due", "Recent activity"):
        assert gone not in page
    assert "chart" not in page.lower()
    assert client.get("/mortgages/").status_code == 200


def test_attention_list_is_arrears_and_maturities(client):
    from .test_mortgages import create

    m = create(client, record_lender_fee="")  # funded Jan 1 2026, matures Jan 1 2027, $1,666.67 due monthly
    alerts = dashboard_stats(today=date(2026, 3, 10))["alerts"]
    assert [(level, text.split(" (")[0]) for level, _, text in alerts] == [("critical", "In arrears $3,333.34")]
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": "2026-03-10", "amount": "3333.34"})
    alerts = dashboard_stats(today=date(2026, 11, 20))["alerts"]
    assert [level for level, _, _ in alerts] == ["critical", "warning"]  # missed Apr–Nov; matures in 42 days
    assert "Matures in 42 days" in alerts[1][2]
