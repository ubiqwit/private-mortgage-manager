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
