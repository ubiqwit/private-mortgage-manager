from datetime import date
from decimal import Decimal

from app import db
from app.models import BankTransaction, Mortgage, MortgageTransaction
from app.services import periods

from .conftest import PASSWORD, make_user
from .test_matching import confirm_mapping, upload
from .test_mortgages import create


def close_through(client, month):
    return client.post(f"/reports/close?month={month}")


def test_edit_transaction_split(client):
    m = create(client, record_lender_fee="")
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": "2026-02-01", "amount": "1900"})
    t = MortgageTransaction.query.one()
    assert t.principal == Decimal("233.33")  # default: excess goes to principal
    resp = client.post(f"/mortgages/transactions/{t.id}/edit", data={
        "type": "payment", "date": "2026-02-01", "amount": "1900", "interest": "1666.67", "principal": "0", "fees": "233.33",
    })
    assert resp.status_code == 302
    t = db.session.get(MortgageTransaction, t.id)
    assert t.fees == Decimal("233.33") and t.principal == 0
    assert db.session.get(Mortgage, m.id).balance() == Decimal("200000.00")
    # Unbalanced split rejected.
    client.post(f"/mortgages/transactions/{t.id}/edit", data={
        "type": "payment", "date": "2026-02-01", "amount": "1900", "interest": "1", "principal": "0", "fees": "0"})
    assert db.session.get(MortgageTransaction, t.id).interest == Decimal("1666.67")
    assert client.get(f"/mortgages/transactions/{t.id}/edit").status_code == 200


def test_closed_month_is_locked(client):
    m = create(client, record_lender_fee="")
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": "2026-02-01", "amount": "1666.67"})
    t = MortgageTransaction.query.one()
    close_through(client, "2026-02")
    assert periods.closed_through() == date(2026, 2, 28)

    # Can't add, edit or delete in February…
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "fee", "date": "2026-02-15", "amount": "50"})
    client.post(f"/mortgages/transactions/{t.id}/delete")
    client.post(f"/mortgages/transactions/{t.id}/edit", data={"type": "payment", "date": "2026-03-01", "amount": "1666.67"})
    assert MortgageTransaction.query.count() == 1 and MortgageTransaction.query.one().date == date(2026, 2, 1)
    # …or renew from a date in it…
    client.post(f"/mortgages/{m.id}/renew", data={"effective_date": "2026-02-10", "interest_rate": "12", "term_months": "12"})
    assert db.session.get(Mortgage, m.id).term_history == []
    # …but March is open.
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": "2026-03-01", "amount": "1666.67"})
    assert MortgageTransaction.query.count() == 2

    # Statement lines in the closed month are skipped on import and can't be matched.
    csv = "Date,Description,Amount\n2026-02-20,E-TRANSFER JANE SMITH,100.00\n2026-03-20,E-TRANSFER JANE SMITH,200.00\n"
    confirm_mapping(client, upload(client, csv, "x.csv"), auto="")
    assert [b.date for b in BankTransaction.query.all()] == [date(2026, 3, 20)]

    # Reopen February: changes allowed again.
    client.post("/reports/reopen?month=2026-02")
    assert periods.closed_through() == date(2026, 1, 31)
    client.post(f"/mortgages/transactions/{t.id}/delete")
    assert [t.date for t in MortgageTransaction.query.all()] == [date(2026, 3, 1)]


def test_cannot_close_current_month_and_only_admins_close(app, client):
    from app.timeutil import today

    t = today()
    client.post(f"/reports/close?month={t:%Y-%m}")
    assert periods.closed_through() is None
    make_user("acct@example.com", role="editor")
    c = app.test_client()
    c.post("/login", data={"email": "acct@example.com", "password": PASSWORD})
    assert c.post("/reports/close?month=2026-01").status_code == 403
