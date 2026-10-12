"""owners field replaces companies

Companies are removed again; instead each mortgage records its owners. Where more than
one company existed (or the only one had been renamed), each mortgage's company name is
kept in its new owners field so nothing is lost. Mortgage references and bank-line
fingerprints only had to be unique per company, so any repeats across companies are
renamed to keep them unique overall.

Revision ID: 77dff3b1fd16
Revises: b376b2c09d2e
Create Date: 2026-10-05 23:30:00.000000

"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '77dff3b1fd16'
down_revision = 'b376b2c09d2e'
branch_labels = None
depends_on = None


def _companies_migration():
    """The migration that added companies: its downgrade removes them, its upgrade adds them back."""
    path = Path(__file__).with_name("b376b2c09d2e_companies.py")
    spec = importlib.util.spec_from_file_location("companies_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _deduplicate(conn, table, column, rename):
    """Give every repeat of `column` after the first (lowest id) a new unique value."""
    rows = conn.execute(sa.text(f"SELECT id, {column} FROM {table} ORDER BY id")).all()
    seen = {value for _, value in rows}
    kept = set()
    for row_id, value in rows:
        if value not in kept:
            kept.add(value)
            continue
        n = 2
        while rename(value, row_id, n) in seen:
            n += 1
        new = rename(value, row_id, n)
        seen.add(new)
        conn.execute(sa.text(f"UPDATE {table} SET {column} = :v WHERE id = :id"), {"v": new, "id": row_id})


def upgrade():
    with op.batch_alter_table('mortgage') as batch_op:
        batch_op.add_column(sa.Column('owners', sa.String(length=300), nullable=True))

    conn = op.get_bind()
    companies = conn.execute(sa.text("SELECT id, name FROM company ORDER BY id")).all()
    if len(companies) > 1 or (companies and companies[0][1] != 'My company'):
        for company_id, name in companies:
            conn.execute(sa.text("UPDATE mortgage SET owners = :name WHERE company_id = :cid"),
                         {"name": name[:300], "cid": company_id})

    _deduplicate(conn, 'mortgage', 'reference', lambda ref, _id, n: f"{ref}-{n}"[:40])
    _deduplicate(conn, 'bank_transaction', 'fingerprint', lambda fp, row_id, _n: f"{fp[:50]}:{row_id}")

    # Closed months: keep the first company's date (the one existing data was in).
    closed = conn.execute(sa.text("SELECT key, value FROM setting WHERE key LIKE 'books_closed_through:%'")).all()
    if closed:
        first = min(closed, key=lambda kv: int(kv[0].split(':', 1)[1]))
        conn.execute(sa.text("DELETE FROM setting WHERE key LIKE 'books_closed_through%'"))
        conn.execute(sa.text("INSERT INTO setting (key, value) VALUES ('books_closed_through', :v)"), {"v": first[1]})

    _companies_migration().downgrade()


def downgrade():
    _companies_migration().upgrade()
    with op.batch_alter_table('mortgage') as batch_op:
        batch_op.drop_column('owners')
