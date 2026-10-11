"""The migration scripts must build exactly the schema the models describe."""
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from app import create_app, db

# Tables added by migrations after the baseline revision (keep in sync when adding tables).
POST_BASELINE_TABLES = ["term_history", "mortgagee"]
POST_BASELINE_COLUMNS = [("user", "form_fields"), ("mortgage", "created_by_id")]
# Columns the baseline had that later migrations removed (the old database still has them).
REMOVED_COLUMNS = [("mortgage", c) for c in (
    "borrower_email VARCHAR(200)", "borrower_phone VARCHAR(50)", "guarantors VARCHAR(300)",
    "property_province VARCHAR(50)", "property_type VARCHAR(30)", "appraisal_date DATE", "pin VARCHAR(50)",
    "ownership_pct NUMERIC(6, 2)", "broker_fee NUMERIC(12, 2)", "lawyer_name VARCHAR(200)",
    "renewal_fee NUMERIC(12, 2)", "nsf_fee NUMERIC(12, 2)", "prepayment_terms VARCHAR(300)",
    "insurance_expiry DATE", "property_tax_status VARCHAR(100)",
)]


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
    """A database made by create_all() before migrations existed upgrades cleanly."""
    import sqlalchemy as sa

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    legacy = create_app({"TESTING": True, "SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": url, "MARKET_FETCH_ENABLED": False})
    with legacy.app_context():
        for table in POST_BASELINE_TABLES:  # didn't exist back then
            db.session.execute(sa.text(f"DROP TABLE {table}"))
        for table, column in REMOVED_COLUMNS:
            db.session.execute(sa.text(f'ALTER TABLE "{table}" ADD COLUMN {column}'))
        db.session.commit()
        # Batch mode rebuilds the table, so indexed / foreign-key columns can go too.
        from alembic.operations import Operations

        with db.engine.begin() as conn:
            ops = Operations(MigrationContext.configure(conn))
            for table, column in POST_BASELINE_COLUMNS:
                index = f"ix_{table}_{column}"
                with ops.batch_alter_table(table) as batch:
                    if any(i["name"] == index for i in sa.inspect(conn).get_indexes(table)):
                        batch.drop_index(index)
                    batch.drop_column(column)
    app = create_app({"SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": url, "MARKET_FETCH_ENABLED": False})
    with app.app_context():
        assert set(POST_BASELINE_TABLES) <= set(sa.inspect(db.engine).get_table_names())
