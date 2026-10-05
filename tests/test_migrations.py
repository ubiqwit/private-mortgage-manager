"""The migration scripts must build exactly the schema the models describe."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import create_app, db


def test_migrations_match_models(tmp_path):
    app = create_app({
        "SECRET_KEY": "test",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'migrated.db'}",
        "MARKET_FETCH_ENABLED": False,
    })
    with app.app_context():
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
        assert diff == [], f"Models changed without a migration — run `flask --app run db migrate`: {diff}"


def test_legacy_database_without_migration_history_is_adopted(tmp_path):
    """A database made by create_all() before migrations existed upgrades cleanly.

    Built faithfully: the baseline schema only, with its version stamp removed — exactly
    what an early install looked like — plus a mortgage that must survive the upgrade.
    """
    import sqlalchemy as sa
    from flask_migrate import upgrade

    from app import MIGRATIONS_DIR

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    base = create_app({"SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": url, "MARKET_FETCH_ENABLED": False,
                       "AUTO_MIGRATE": False})
    with base.app_context():
        upgrade(directory=MIGRATIONS_DIR, revision="b33d016bc6db")
        db.session.execute(sa.text(
            "INSERT INTO mortgage (reference, borrower_name, property_address, principal_amount, interest_rate, "
            "funded_date, first_payment_date, maturity_date, status, created_at, updated_at) VALUES "
            "('M-001', 'Old Borrower', '1 Old St', 100000, 10, '2025-01-01', '2025-02-01', '2026-01-01', 'active', "
            "'2025-01-01', '2025-01-01')"))
        db.session.execute(sa.text("DROP TABLE alembic_version"))
        db.session.commit()

    app = create_app({"SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": url, "MARKET_FETCH_ENABLED": False})
    with app.app_context():
        tables = set(sa.inspect(db.engine).get_table_names())
        assert {"term_history", "mortgage_document", "mortgage_activity", "company"} <= tables
        row = db.session.execute(sa.text(
            "SELECT m.reference, c.name FROM mortgage m JOIN company c ON c.id = m.company_id")).one()
        assert tuple(row) == ("M-001", "My company")
