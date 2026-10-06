"""mortgagees with shares replace the owners text

Each mortgage gets a list of mortgagees (name + percentage). Any owners text already
entered becomes one mortgagee owning 100%, ready to be split on the mortgage form.

Revision ID: 85ee460e866e
Revises: 77dff3b1fd16
Create Date: 2026-10-06 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '85ee460e866e'
down_revision = '77dff3b1fd16'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mortgagee',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mortgage_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('share_pct', sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['mortgage_id'], ['mortgage.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mortgagee') as batch_op:
        batch_op.create_index(batch_op.f('ix_mortgagee_mortgage_id'), ['mortgage_id'], unique=False)

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, owners FROM mortgage WHERE owners IS NOT NULL AND owners <> ''")).all()
    for mortgage_id, owners in rows:
        conn.execute(sa.text("INSERT INTO mortgagee (mortgage_id, name, share_pct, sort_order) "
                             "VALUES (:m, :name, 100, 0)"), {"m": mortgage_id, "name": owners.strip()[:200]})

    with op.batch_alter_table('mortgage') as batch_op:
        batch_op.drop_column('owners')


def downgrade():
    with op.batch_alter_table('mortgage') as batch_op:
        batch_op.add_column(sa.Column('owners', sa.String(length=300), nullable=True))
    conn = op.get_bind()
    labels = {}
    for mortgage_id, name, share in conn.execute(sa.text(
            "SELECT mortgage_id, name, share_pct FROM mortgagee ORDER BY mortgage_id, sort_order")).all():
        labels.setdefault(mortgage_id, []).append(f"{name} {share:g}%")
    for mortgage_id, parts in labels.items():
        conn.execute(sa.text("UPDATE mortgage SET owners = :o WHERE id = :m"), {"o": "; ".join(parts)[:300], "m": mortgage_id})
    with op.batch_alter_table('mortgagee') as batch_op:
        batch_op.drop_index(batch_op.f('ix_mortgagee_mortgage_id'))
    op.drop_table('mortgagee')
