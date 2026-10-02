import io
from decimal import Decimal

from app import db
from app.models import BankTransaction, Mortgage, MortgageTransaction, PendingUpload, StatementImport
from app.services import matching

from .test_mortgages import create

CSV = (
    "Date,Description,Amount\n"
    "2026-02-01,E-TRANSFER AUTODEPOSIT JANE SMITH 88231,1666.67\n"
    "2026-02-03,MOBILE CHEQUE DEPOSIT,9750.00\n"
    "2026-02-05,COFFEE SHOP,-4.50\n"
    "2026-02-10,E-TRANSFER AUTODEPOSIT BOB JONES,1666.67\n"
)


def upload(client, content=CSV, name="feb.csv", auto="1"):
    resp = client.post("/statements/upload", data={
        "file": (io.BytesIO(content.encode()), name), "account_name": "Chequing", "auto_match": auto,
    }, content_type="multipart/form-data")
    return resp


def confirm_mapping(client, resp, auto="1"):
    assert resp.status_code == 302 and "/statements/map/" in resp.headers["Location"]
    page = client.get(resp.headers["Location"])
    assert page.status_code == 200 and b"Check the columns" in page.data
    token = resp.headers["Location"].split("/map/")[1].split("?")[0]
    return client.post(f"/statements/map/{token}", data={
        "action": "import", "date": "0", "description": "1", "amount": "2", "date_format": "%Y-%m-%d",
        "has_header": "1", "header_row": "0", "account_name": "Chequing", "auto_match": auto,
    })


def test_csv_import_auto_matches_and_dedupes(client):
    m = create(client, record_lender_fee="", match_keywords="JANE SMITH")
    resp = confirm_mapping(client, upload(client))
    assert resp.status_code == 302
    assert BankTransaction.query.count() == 4
    assert PendingUpload.query.count() == 0
    jane = BankTransaction.query.filter(BankTransaction.description.like("%JANE%")).one()
    assert jane.status == "matched"
    txn = jane.mortgage_transactions[0]
    assert txn.mortgage_id == m.id and txn.interest == Decimal("1666.67")
    # Same amount but unknown payer: suggested, not auto-matched (only one mortgage → medium at best).
    bob = BankTransaction.query.filter(BankTransaction.description.like("%BOB%")).one()
    assert bob.status == "unmatched"

    # Re-import the same file: nothing new.
    confirm_mapping(client, upload(client))
    assert BankTransaction.query.count() == 4
    assert StatementImport.query.order_by(StatementImport.id.desc()).first().duplicate_count == 4
    assert client.get("/statements/reconcile").status_code == 200
    assert client.get("/statements/reconcile?status=matched&direction=all").status_code == 200
    assert client.get("/statements/").status_code == 200


def test_ambiguous_payer_not_auto_matched(client):
    create(client, record_lender_fee="")
    create(client, record_lender_fee="", borrower_name="Bob Jones", property_address="1 Oak St")
    confirm_mapping(client, upload(client))
    bob = BankTransaction.query.filter(BankTransaction.description.like("%BOB%")).one()
    assert bob.status == "matched"  # name + exact amount + due date → high, and only Bob's name matches
    jane = BankTransaction.query.filter(BankTransaction.description.like("%JANE%")).one()
    assert jane.mortgage_transactions[0].mortgage.borrower_name == "Jane Smith"


def test_manual_allocation_split_and_learn(client):
    m = create(client, record_lender_fee="")
    confirm_mapping(client, upload(client), auto="")
    cheque = BankTransaction.query.filter(BankTransaction.description.like("%CHEQUE%")).one()
    # Split a cheque across a payment and a fee.
    client.post(f"/statements/lines/{cheque.id}/allocate", data={"mortgage_id": m.id, "type": "payment", "amount": "1666.67"})
    cheque = db.session.get(BankTransaction, cheque.id)
    assert cheque.status == "unmatched" and matching.remaining(cheque) == Decimal("8083.33")
    client.post(f"/statements/lines/{cheque.id}/allocate", data={"mortgage_id": m.id, "type": "prepayment"})
    cheque = db.session.get(BankTransaction, cheque.id)
    assert cheque.status == "matched"
    assert db.session.get(Mortgage, m.id).balance() == Decimal("200000") - Decimal("8083.33")
    # Generic description is not learned as a keyword.
    assert "CHEQUE" not in (db.session.get(Mortgage, m.id).match_keywords or "")

    # Learned keyword from a specific description.
    bob = BankTransaction.query.filter(BankTransaction.description.like("%BOB%")).one()
    client.post(f"/statements/lines/{bob.id}/allocate", data={"mortgage_id": m.id, "type": "payment", "remember": "1"})
    assert "E-TRANSFER AUTODEPOSIT BOB JONES" in db.session.get(Mortgage, m.id).match_keywords

    # Unmatch removes the mortgage transactions.
    client.post(f"/statements/lines/{cheque.id}/restore")
    assert db.session.get(BankTransaction, cheque.id).status == "unmatched"
    assert MortgageTransaction.query.filter_by(bank_transaction_id=cheque.id).count() == 0


def test_payout_detection_and_reopen(client):
    m = create(client, record_lender_fee="")
    csv = "Date,Description,Amount\n2026-06-15,WIRE IN SMITH LAW TRUST,201500.00\n"
    confirm_mapping(client, upload(client, csv, "payout.csv"), auto="")
    line = BankTransaction.query.one()
    sugg = matching.suggestions(line)
    assert sugg[0].txn_type == "payout"
    client.post(f"/statements/lines/{line.id}/allocate", data={"mortgage_id": m.id, "type": "payout"})
    m = db.session.get(Mortgage, m.id)
    assert m.status == "paid_out" and m.balance() == 0
    t = m.transactions[-1]
    assert t.principal == Decimal("200000.00") and t.interest == Decimal("1500.00")
    client.post(f"/statements/lines/{line.id}/restore")
    assert db.session.get(Mortgage, m.id).status == "active"


def test_ignore_and_delete_statement(client):
    m = create(client, record_lender_fee="", match_keywords="JANE SMITH")
    confirm_mapping(client, upload(client))
    coffee = BankTransaction.query.filter(BankTransaction.description.like("%COFFEE%")).one()
    client.post(f"/statements/lines/{coffee.id}/ignore")
    assert db.session.get(BankTransaction, coffee.id).status == "ignored"
    imp = StatementImport.query.one()
    client.post(f"/statements/{imp.id}/delete")
    assert BankTransaction.query.count() == 0
    assert MortgageTransaction.query.filter_by(mortgage_id=m.id).count() == 0


def test_pdf_upload_rejected(client):
    resp = client.post("/statements/upload", data={"file": (io.BytesIO(b"%PDF-1.4"), "s.pdf")},
                       content_type="multipart/form-data", follow_redirects=True)
    assert b"download the statement as CSV" in resp.data


def test_links_manually_recorded_payment_instead_of_duplicating(client):
    m = create(client, record_lender_fee="", match_keywords="JANE SMITH")
    client.post(f"/mortgages/{m.id}/transactions", data={"type": "payment", "date": "2026-02-01", "amount": "1666.67"})
    confirm_mapping(client, upload(client))
    m = db.session.get(Mortgage, m.id)
    payments = [t for t in m.transactions if t.type == "payment"]
    assert len(payments) == 1, "bank line should link to the manual entry, not add a second payment"
    jane = BankTransaction.query.filter(BankTransaction.description.like("%JANE%")).one()
    assert jane.status == "matched" and payments[0].bank_transaction_id == jane.id
    # Unmatching keeps the hand-entered payment, just unlinked.
    client.post(f"/statements/lines/{jane.id}/restore")
    m = db.session.get(Mortgage, m.id)
    assert len([t for t in m.transactions if t.type == "payment"]) == 1
    assert m.transactions[0].bank_transaction_id is None
