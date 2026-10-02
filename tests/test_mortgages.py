from datetime import date
from decimal import Decimal

from app import db
from app.models import Mortgage, MortgageTransaction

BASE = {
    "borrower_name": "Jane Smith",
    "property_address": "12 Maple Ave",
    "property_city": "Toronto",
    "property_value": "800000",
    "position": "2",
    "prior_charges": "400000",
    "principal_amount": "200000",
    "interest_rate": "10",
    "rate_type": "fixed",
    "compounding": "monthly",
    "payment_type": "interest_only",
    "payment_frequency": "monthly",
    "funded_date": "2026-01-01",
    "term_months": "12",
    "lender_fee": "4000",
    "record_lender_fee": "1",
    "status": "active",
}


def create(client, **overrides):
    data = {**BASE, **overrides}
    resp = client.post("/mortgages/new", data=data)
    assert resp.status_code == 302, resp.data
    return Mortgage.query.order_by(Mortgage.id.desc()).first()


def test_create_mortgage_derives_dates_and_fee(client):
    m = create(client)
    assert m.reference == "M-001"
    assert m.first_payment_date == date(2026, 2, 1)
    assert m.maturity_date == date(2027, 1, 1)
    assert m.regular_payment() == Decimal("1666.67")
    assert m.combined_ltv() == Decimal("75.00")
    assert len(m.transactions) == 1 and m.transactions[0].fees == Decimal("4000.00")
    assert client.get(f"/mortgages/{m.id}").status_code == 200
    assert client.get(f"/mortgages/{m.id}/schedule").status_code == 200
    assert b"Jane Smith" in client.get("/mortgages/").data


def test_validation_errors(client):
    resp = client.post("/mortgages/new", data={**BASE, "borrower_name": ""})
    assert resp.status_code == 400
    resp = client.post("/mortgages/new", data={**BASE, "rate_type": "variable"})
    assert resp.status_code == 400 and b"spread" in resp.data


def test_payment_split_and_arrears(client):
    m = create(client)
    # Three payments due by Apr 15; two received → one in arrears
    for d in ("2026-02-01", "2026-03-01"):
        client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": d, "amount": "1666.67"})
    m = db.session.get(Mortgage, m.id)
    pay = [t for t in m.transactions if t.type == "payment"][0]
    assert pay.interest == Decimal("1666.67") and pay.principal == 0
    assert m.arrears(as_of=date(2026, 4, 15)) == Decimal("1666.67")
    assert m.arrears(as_of=date(2026, 3, 15)) == 0


def test_prepayment_and_payout(client):
    m = create(client, record_lender_fee="")
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "prepayment", "date": "2026-03-10", "amount": "50000"})
    m = db.session.get(Mortgage, m.id)
    assert m.balance() == Decimal("150000.00")
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payout", "date": "2026-06-01", "amount": "151250"})
    m = db.session.get(Mortgage, m.id)
    assert m.balance() == 0
    assert m.status == "paid_out"
    payout = [t for t in m.transactions if t.type == "payout"][0]
    assert payout.interest == Decimal("1250.00")


def test_split_must_balance(client):
    m = create(client, record_lender_fee="")
    client.post(f"/mortgages/{m.id}/transactions",
                data={"type": "payment", "date": "2026-02-01", "amount": "1000", "interest": "900", "principal": "0", "fees": "0"})
    assert MortgageTransaction.query.count() == 0


def test_variable_rate_follows_prime(client):
    from app.models import MarketObservation
    db.session.add(MarketObservation(series="V80691311", date=date(2026, 9, 1), value=Decimal("4.95")))
    db.session.commit()
    m = create(client, rate_type="variable", prime_spread="5", rate_floor="9")
    assert m.effective_rate(Decimal("4.95")) == Decimal("9.95")
    assert m.effective_rate(Decimal("3.00")) == Decimal("9")
    assert client.get(f"/mortgages/{m.id}").status_code == 200


def test_delete_requires_reference(client):
    m = create(client)
    client.post(f"/mortgages/{m.id}/delete", data={"confirm": "nope"})
    assert db.session.get(Mortgage, m.id) is not None
    client.post(f"/mortgages/{m.id}/delete", data={"confirm": m.reference})
    assert Mortgage.query.count() == 0


def test_edit(client):
    m = create(client)
    resp = client.post(f"/mortgages/{m.id}/edit", data={**BASE, "reference": m.reference, "interest_rate": "11"})
    assert resp.status_code == 302
    assert db.session.get(Mortgage, m.id).interest_rate == Decimal("11")
    assert client.get(f"/mortgages/{m.id}/edit").status_code == 200
