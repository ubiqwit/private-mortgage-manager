from datetime import date

from app import db
from app.models import Mortgage
from app.services.history import payment_history, summary

from .test_mortgages import create


def pay(client, m, d, amount="1666.67", kind="payment"):
    client.post(f"/mortgages/{m.id}/transactions", data={"type": kind, "date": d, "amount": amount})


def test_on_time_late_missed(client):
    m = create(client, record_lender_fee="")  # dues on the 1st from Feb 2026, $1,666.67
    pay(client, m, "2026-02-01")          # Feb on time
    pay(client, m, "2026-03-20")          # Mar late (19 days)
    pay(client, m, "2026-04-03")          # Apr within grace
    # May missed; Jun 1 due, checked on Jun 3 → pending
    m = db.session.get(Mortgage, m.id)
    rows = payment_history(m, date(2026, 6, 3))
    status = {r.due.month: r.status for r in rows}
    assert status == {2: "on_time", 3: "late", 4: "on_time", 5: "missed", 6: "pending", 7: "upcoming"}
    late = [r for r in rows if r.status == "late"][0]
    assert late.days_late == 19
    s = summary(rows)
    assert (s["on_time"], s["late"], s["missed"], s["on_time_pct"]) == (2, 1, 1, 50)
    assert b"Payment history" in client.get(f"/mortgages/{m.id}").data


def test_catch_up_and_nsf(client):
    m = create(client, record_lender_fee="")
    pay(client, m, "2026-02-01")
    pay(client, m, "2026-03-01")
    pay(client, m, "2026-03-05", kind="nsf")       # March bounced
    pay(client, m, "2026-04-10", amount="3333.34")  # catch-up covers Mar + Apr
    m = db.session.get(Mortgage, m.id)
    rows = {r.due.month: r for r in payment_history(m, date(2026, 4, 20))}
    assert rows[2].status == "on_time"
    assert rows[3].status == "late" and rows[3].paid_on == date(2026, 4, 10)
    assert rows[4].status == "late"
