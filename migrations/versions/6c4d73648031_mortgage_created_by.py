"""record who added each mortgage

Adds mortgage.created_by_id. Existing mortgages are matched to the person who added
them using the audit log: "mortgage_created" entries name the reference, and
spreadsheet imports / demo loads are matched by time (logged within two minutes of
the mortgages being created). Anything that can't be matched stays unassigned.

Revision ID: 6c4d73648031
Revises: 85ee460e866e
Create Date: 2026-10-06 14:00:00.000000

"""
from datetime import datetime, timedelta

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '6c4d73648031'
down_revision = '85ee460e866e'
branch_labels = None
depends_on = None

WINDOW = timedelta(minutes=2)


def _dt(value):
    # SQLite hands back strings; PostgreSQL datetimes.
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def upgrade():
    with op.batch_alter_table('mortgage') as batch_op:
        batch_op.add_column(sa.Column('created_by_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_mortgage_created_by_id'), ['created_by_id'], unique=False)
        batch_op.create_foreign_key('fk_mortgage_created_by_id_user', 'user', ['created_by_id'], ['id'],
                                    ondelete='SET NULL')

    conn = op.get_bind()
    users = {uid for (uid,) in conn.execute(sa.text('SELECT id FROM "user"'))}
    log = [(_dt(at), uid, action, detail or "") for at, uid, action, detail in conn.execute(sa.text(
        "SELECT at, user_id, action, detail FROM audit_log WHERE user_id IS NOT NULL AND action IN "
        "('mortgage_created', 'mortgages_imported', 'demo_loaded') ORDER BY at"))]
    for mortgage_id, reference, created_at in conn.execute(sa.text("SELECT id, reference, created_at FROM mortgage")).all():
        created_at = _dt(created_at)
        candidates = [
            (abs(at - created_at), uid) for at, uid, action, detail in log
            if uid in users and abs(at - created_at) <= WINDOW
            and (action != 'mortgage_created' or detail == reference)
        ]
        if candidates:
            conn.execute(sa.text("UPDATE mortgage SET created_by_id = :u WHERE id = :m"),
                         {"u": min(candidates)[1], "m": mortgage_id})


def downgrade():
    with op.batch_alter_table('mortgage') as batch_op:
        batch_op.drop_constraint('fk_mortgage_created_by_id_user', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_mortgage_created_by_id'))
        batch_op.drop_column('created_by_id')
