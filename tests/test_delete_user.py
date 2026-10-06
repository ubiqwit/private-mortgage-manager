"""Admins can delete a user; the mortgages that user added go with them."""
from datetime import date, datetime

from flask_migrate import downgrade, upgrade

from app import MIGRATIONS_DIR, create_app, db
from app.models import (
    AuditLog,
    BankTransaction,
    Mortgage,
    MortgageActivity,
    MortgageTransaction,
    StatementImport,
    User,
)
from app.services.periods import set_closed_through

from .conftest import PASSWORD, make_user
from .test_import import ROW as IMPORT_ROW
from .test_import import csv_with, post
from .test_mortgages import BASE


def sign_in(app, email):
    c = app.test_client()
    assert c.post("/login", data={"email": email, "password": PASSWORD}).status_code == 302
    return c


def setup_two_editors(app, client):
    """The admin adds one mortgage, the editor two (one by hand, one by import)."""
    client.post("/mortgages/new", data={**BASE, "property_address": "1 Admin St"})
    make_user("editor@example.com", role="editor")
    editor = sign_in(app, "editor@example.com")
    assert editor.post("/mortgages/new", data={**BASE, "property_address": "2 Editor Ave"}).status_code == 302
    assert post(editor, csv_with({**IMPORT_ROW, "Property address": "3 Imported Rd"})).status_code == 302
    return editor


def test_mortgages_remember_who_added_them(app, client):
    setup_two_editors(app, client)
    by = {m.property_address: m.created_by.email for m in Mortgage.query.all()}
    assert by == {"1 Admin St": "owner@example.com", "2 Editor Ave": "editor@example.com",
                  "3 Imported Rd": "editor@example.com"}
    m = Mortgage.query.filter_by(property_address="2 Editor Ave").one()
    assert "editor@example.com" in client.get(f"/mortgages/{m.id}").get_data(as_text=True)


def test_delete_user_and_their_mortgages(app, client):
    setup_two_editors(app, client)
    editor = User.query.filter_by(email="editor@example.com").one()
    theirs = Mortgage.query.filter_by(property_address="2 Editor Ave").one()
    admins = Mortgage.query.filter_by(property_address="1 Admin St").one()

    # Their payment was matched to a bank deposit; that line goes back to the queue.
    imp = StatementImport(filename="s.csv")
    bank = BankTransaction(statement=imp, date=date(2026, 2, 1), description="E-TRANSFER", amount=1666.67,
                           fingerprint="fp1", status="matched")
    db.session.add_all([imp, bank, MortgageTransaction(mortgage=theirs, date=date(2026, 2, 1), type="payment",
                                                       amount=1666.67, interest=1666.67, principal=0, fees=0,
                                                       bank_transaction=bank),
                        MortgageActivity(mortgage=admins, body="Called borrower", user_id=editor.id)])
    db.session.commit()

    users_page = client.get("/users").get_data(as_text=True)
    assert f"/users/{editor.id}/delete" in users_page

    page = client.get(f"/users/{editor.id}/delete").get_data(as_text=True)
    assert "2 Editor Ave" in page and "3 Imported Rd" in page and "1 Admin St" not in page

    client.post(f"/users/{editor.id}/delete", data={"confirm": "wrong@example.com"})
    assert db.session.get(User, editor.id) is not None

    resp = client.post(f"/users/{editor.id}/delete", data={"confirm": "Editor@Example.com "})
    assert resp.status_code == 302
    db.session.expire_all()
    assert db.session.get(User, editor.id) is None
    assert [m.property_address for m in Mortgage.query.all()] == ["1 Admin St"]
    assert MortgageTransaction.query.filter_by(bank_transaction_id=bank.id).count() == 0
    assert db.session.get(BankTransaction, bank.id).status == "unmatched"
    # History stays, just without them.
    assert MortgageActivity.query.one().user_id is None
    assert AuditLog.query.filter_by(action="mortgage_created", detail=theirs.reference).one().user_id is None
    assert "editor@example.com and 2 mortgage(s)" in AuditLog.query.filter_by(action="user_deleted").one().detail


def test_user_without_mortgages(app, client):
    client.post("/mortgages/new", data=BASE)
    viewer = make_user("viewer@example.com", role="viewer")
    assert "no mortgages will be deleted" in client.get(f"/users/{viewer.id}/delete").get_data(as_text=True)
    client.post(f"/users/{viewer.id}/delete", data={"confirm": "viewer@example.com"})
    assert db.session.get(User, viewer.id) is None and Mortgage.query.count() == 1


def test_cannot_delete_yourself_and_only_admins_can_delete(app, client):
    admin = User.query.filter_by(email="owner@example.com").one()
    client.post(f"/users/{admin.id}/delete", data={"confirm": admin.email})
    assert db.session.get(User, admin.id) is not None
    assert f"/users/{admin.id}/delete" not in client.get("/users").get_data(as_text=True)

    make_user("editor@example.com", role="editor")
    editor = sign_in(app, "editor@example.com")
    assert editor.get(f"/users/{admin.id}/delete").status_code == 403
    assert editor.post(f"/users/{admin.id}/delete", data={"confirm": admin.email}).status_code == 403


def test_closed_months_block_deleting(app, client):
    setup_two_editors(app, client)
    editor = User.query.filter_by(email="editor@example.com").one()
    set_closed_through(date(2026, 3, 31))
    db.session.commit()
    page = client.get(f"/users/{editor.id}/delete").get_data(as_text=True)
    assert "can&#39;t be deleted yet" in page or "can't be deleted yet" in page
    client.post(f"/users/{editor.id}/delete", data={"confirm": editor.email})
    assert db.session.get(User, editor.id) is not None and Mortgage.query.count() == 3


def test_demo_data_belongs_to_whoever_loaded_it(app, client):
    client.post("/demo/load")
    assert {m.created_by.email for m in Mortgage.query.all()} == {"owner@example.com"}


def test_upgrade_backfills_who_added_mortgages(tmp_path):
    app = create_app({"SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'b.db'}",
                      "MARKET_FETCH_ENABLED": False})
    with app.app_context():
        t0 = datetime(2026, 10, 1, 12, 0, 0)
        for email in ("dad@example.com", "son@example.com"):
            make_user(email, role="editor")
        dad, son = (User.query.filter_by(email=e).one().id for e in ("dad@example.com", "son@example.com"))
        for ref, created in (("M-001", t0), ("M-002", t0.replace(hour=13)), ("M-003", t0.replace(hour=13)),
                             ("M-004", t0.replace(hour=15))):
            db.session.add(Mortgage(reference=ref, borrower_name="B", property_address="A", principal_amount=1000,
                                    interest_rate=10, funded_date=date(2026, 1, 1), first_payment_date=date(2026, 2, 1),
                                    maturity_date=date(2027, 1, 1), created_at=created, updated_at=created))
        db.session.add_all([
            AuditLog(at=t0.replace(second=1), user_id=dad, action="mortgage_created", detail="M-001"),
            AuditLog(at=t0.replace(hour=13, second=4), user_id=son, action="mortgages_imported", detail="2 from book.xlsx"),
        ])
        db.session.commit()
        db.session.remove()

        downgrade(directory=MIGRATIONS_DIR, revision="85ee460e866e")  # before mortgages recorded who added them
        upgrade(directory=MIGRATIONS_DIR)
        db.session.remove()
        by = {m.reference: (m.created_by.email if m.created_by else None) for m in Mortgage.query.all()}
        assert by == {"M-001": "dad@example.com", "M-002": "son@example.com", "M-003": "son@example.com",
                      "M-004": None}
