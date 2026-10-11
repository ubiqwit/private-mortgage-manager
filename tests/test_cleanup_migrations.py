"""Upgrades that remove features keep whatever was recorded in them."""
from datetime import date

import sqlalchemy as sa
from flask_migrate import downgrade, upgrade

from app import MIGRATIONS_DIR, create_app, db
from app.models import Mortgage

from .conftest import make_user


def _app(tmp_path, name):
    return create_app({"SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / name}",
                       "MARKET_FETCH_ENABLED": False})


def _mortgage(ref, notes=None):
    m = Mortgage(reference=ref, borrower_name=ref, property_address="1 Main St", principal_amount=100000,
                 interest_rate=10, funded_date=date(2026, 1, 1), first_payment_date=date(2026, 2, 1),
                 maturity_date=date(2027, 1, 1), notes=notes)
    db.session.add(m)
    return m


def test_activity_log_moves_into_notes_and_documents_are_archived(tmp_path):
    app = _app(tmp_path, "activity.db")
    with app.app_context():
        user = make_user("dad@example.com", role="editor")
        uid = user.id
        a, b, c = _mortgage("M-001", notes="Existing note"), _mortgage("M-002"), _mortgage("M-003")
        db.session.commit()
        ids = a.id, b.id, c.id
        db.session.remove()

        downgrade(directory=MIGRATIONS_DIR, revision="6c4d73648031")  # before the removal
        with db.engine.begin() as conn:
            rows = [(ids[0], "call", "Borrower promised payment by the 15th", "2026-03-10", False, uid, "2026-03-01 10:00:00"),
                    (ids[0], "letter", "Sent demand letter", None, False, None, "2026-04-02 09:00:00"),
                    (ids[1], "note", "Insurance renewed", "2026-05-01", True, uid, "2026-04-20 12:00:00")]
            for r in rows:
                conn.execute(sa.text(
                    "INSERT INTO mortgage_activity (mortgage_id, kind, body, follow_up_on, done, user_id, created_at) "
                    "VALUES (:m, :k, :b, :f, :d, :u, :c)"),
                    dict(zip(("m", "k", "b", "f", "d", "u", "c"), r, strict=True)))
            conn.execute(sa.text(
                "INSERT INTO mortgage_document (mortgage_id, filename, content_type, size, category, content, uploaded_at) "
                "VALUES (:m, 'appraisal.pdf', 'application/pdf', 3, 'appraisal', :blob, '2026-02-01 08:00:00')"),
                {"m": ids[1], "blob": b"PDF"})

        upgrade(directory=MIGRATIONS_DIR)
        db.session.remove()
        notes = {m.reference: m.notes for m in Mortgage.query.all()}
        assert notes["M-001"] == ("Existing note\n\nActivity log:\n"
                                  "2026-03-01 Phone call (dad@example.com): Borrower promised payment by the 15th "
                                  "[follow up 2026-03-10]\n"
                                  "2026-04-02 Letter / notice: Sent demand letter")
        assert notes["M-002"] == ("Activity log:\n2026-04-20 Note (dad@example.com): Insurance renewed "
                                  "[follow up 2026-05-01, done]\n\n"
                                  "Documents (archived, no longer shown in the app):\nappraisal.pdf (uploaded 2026-02-01)")
        assert notes["M-003"] is None
        tables = sa.inspect(db.engine).get_table_names()
        assert "mortgage_activity" not in tables and "mortgage_document" not in tables
        archived = db.session.execute(sa.text(
            "SELECT mortgage_reference, filename, content FROM archived_mortgage_document")).all()
        assert archived == [("M-002", "appraisal.pdf", b"PDF")]


def test_no_documents_means_no_archive_table(tmp_path):
    app = _app(tmp_path, "empty.db")
    with app.app_context():
        assert "archived_mortgage_document" not in sa.inspect(db.engine).get_table_names()
