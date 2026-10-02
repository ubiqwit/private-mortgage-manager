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
    """A database made by create_all() before migrations existed upgrades cleanly."""
    import sqlalchemy as sa

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    legacy = create_app({"TESTING": True, "SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": url, "MARKET_FETCH_ENABLED": False})
    with legacy.app_context():
        db.session.execute(sa.text("DROP TABLE term_history"))  # didn't exist back then
        db.session.commit()
    app = create_app({"SECRET_KEY": "t", "SQLALCHEMY_DATABASE_URI": url, "MARKET_FETCH_ENABLED": False})
    with app.app_context():
        assert "term_history" in sa.inspect(db.engine).get_table_names()
