"""companies

Adds companies and per-company data. Existing data moves into one company called
"My company" (rename it in the app); existing editors/viewers get access to it, and
the closed-books date carries over to it.

Revision ID: b376b2c09d2e
Revises: df3e9e3e4414
Create Date: 2026-10-05 22:00:08.020419

"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'b376b2c09d2e'
down_revision = 'df3e9e3e4414'
branch_labels = None
depends_on = None

# Unnamed UNIQUE constraints from the initial schema get these names during SQLite batch
# rebuilds; PostgreSQL named them <table>_<column>_key itself.
NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _old_unique_name(table, column):
    return f"{table}_{column}_key" if op.get_bind().dialect.name == "postgresql" else f"uq_{table}_{column}"


def upgrade():
    op.create_table(
        'company',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_table(
        'user_company',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['company.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'company_id'),
    )

    # Every installation starts with one company that owns everything already recorded.
    conn = op.get_bind()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    company = sa.table('company', sa.column('id', sa.Integer), sa.column('name', sa.String),
                       sa.column('created_at', sa.DateTime), sa.column('updated_at', sa.DateTime))
    conn.execute(company.insert().values(name='My company', created_at=now, updated_at=now))
    default_id = conn.execute(sa.text("SELECT id FROM company WHERE name = 'My company'")).scalar_one()

    for table in ('mortgage', 'statement_import', 'bank_transaction'):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column('company_id', sa.Integer(), nullable=True))
        conn.execute(sa.text(f'UPDATE {table} SET company_id = :cid'), {"cid": default_id})

    with op.batch_alter_table('mortgage', naming_convention=NAMING) as batch_op:
        batch_op.alter_column('company_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_constraint(_old_unique_name('mortgage', 'reference'), type_='unique')
        batch_op.create_unique_constraint('uq_mortgage_company_reference', ['company_id', 'reference'])
        batch_op.create_index(batch_op.f('ix_mortgage_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key('fk_mortgage_company_id_company', 'company', ['company_id'], ['id'])

    with op.batch_alter_table('statement_import') as batch_op:
        batch_op.alter_column('company_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_index(batch_op.f('ix_statement_import_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key('fk_statement_import_company_id_company', 'company', ['company_id'], ['id'])

    with op.batch_alter_table('bank_transaction', naming_convention=NAMING) as batch_op:
        batch_op.alter_column('company_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_constraint(_old_unique_name('bank_transaction', 'fingerprint'), type_='unique')
        batch_op.create_unique_constraint('uq_bank_transaction_company_fingerprint', ['company_id', 'fingerprint'])
        batch_op.create_index(batch_op.f('ix_bank_transaction_company_id'), ['company_id'], unique=False)
        batch_op.create_foreign_key('fk_bank_transaction_company_id_company', 'company', ['company_id'], ['id'])

    # Existing editors/viewers keep working in the same data (admins see every company anyway).
    conn.execute(sa.text("INSERT INTO user_company (user_id, company_id) "
                         "SELECT id, :cid FROM \"user\" WHERE role <> 'admin'"), {"cid": default_id})
    # Closed months become the default company's.
    conn.execute(sa.text("UPDATE setting SET key = :new WHERE key = 'books_closed_through'"),
                 {"new": f"books_closed_through:{default_id}"})


def downgrade():
    with op.batch_alter_table('bank_transaction', naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint('fk_bank_transaction_company_id_company', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_bank_transaction_company_id'))
        batch_op.drop_constraint('uq_bank_transaction_company_fingerprint', type_='unique')
        batch_op.create_unique_constraint(_old_unique_name('bank_transaction', 'fingerprint'), ['fingerprint'])
        batch_op.drop_column('company_id')
    with op.batch_alter_table('statement_import') as batch_op:
        batch_op.drop_constraint('fk_statement_import_company_id_company', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_statement_import_company_id'))
        batch_op.drop_column('company_id')
    with op.batch_alter_table('mortgage', naming_convention=NAMING) as batch_op:
        batch_op.drop_constraint('fk_mortgage_company_id_company', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_mortgage_company_id'))
        batch_op.drop_constraint('uq_mortgage_company_reference', type_='unique')
        batch_op.create_unique_constraint(_old_unique_name('mortgage', 'reference'), ['reference'])
        batch_op.drop_column('company_id')
    op.drop_table('user_company')
    op.drop_table('company')
