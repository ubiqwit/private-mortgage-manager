from datetime import timedelta

from app import db
from app.models import MortgageActivity
from app.services.portfolio import dashboard_stats
from app.timeutil import today

from .test_mortgages import create


def test_activity_log_and_follow_ups(client):
    m = create(client)
    due = today() - timedelta(days=1)
    client.post(f"/mortgages/{m.id}/activity", data={"kind": "call", "body": "Borrower promised payment", "follow_up_on": due.isoformat()})
    client.post(f"/mortgages/{m.id}/activity", data={"kind": "note", "body": "Insurance renewed"})
    assert MortgageActivity.query.count() == 2
    page = client.get(f"/mortgages/{m.id}")
    assert b"Borrower promised payment" in page.data and b"Overdue" in page.data
    stats = dashboard_stats(today=today())
    assert any("Follow-up overdue" in text for _, _, text in stats["alerts"])
    assert b"Borrower promised payment" in client.get("/mortgages/follow-ups").data

    a = MortgageActivity.query.filter_by(kind="call").one()
    client.post(f"/mortgages/activity/{a.id}/done", data={"next": "/mortgages/follow-ups"})
    assert db.session.get(MortgageActivity, a.id).done
    assert not any("Follow-up" in text for _, _, text in dashboard_stats(today=today())["alerts"])
    client.post(f"/mortgages/activity/{a.id}/delete")
    assert MortgageActivity.query.count() == 1


def test_activity_validation(client):
    m = create(client)
    client.post(f"/mortgages/{m.id}/activity", data={"kind": "note", "body": "  "})
    client.post(f"/mortgages/{m.id}/activity", data={"kind": "note", "body": "x", "follow_up_on": "tomorrow"})
    assert MortgageActivity.query.count() == 0
