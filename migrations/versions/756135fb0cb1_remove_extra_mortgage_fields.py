"""remove extra mortgage fields

Drops fields beyond the core mortgage record: borrower email/phone, guarantors,
province, property type, appraisal date, PIN, "your share %", broker fee, lawyer,
renewal fee, NSF fee, prepayment terms, insurance expiry and property tax status.

Anything filled in (other than the defaults) is appended to the mortgage's notes
first, so no information is lost.

Revision ID: 756135fb0cb1
Revises: f76c7f101309
Create Date: 2026-10-11 12:00:00.000000

"""
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '756135fb0cb1'
down_revision = 'f76c7f101309'
branch_labels = None
depends_on = None

PROPERTY_TYPES = {"semi": "Semi-detached", "townhouse": "Townhouse", "condo": "Condo",
                  "multi_unit": "Multi-unit residential", "commercial": "Commercial", "mixed_use": "Mixed use",
                  "land": "Vacant land", "other": "Other"}  # "detached" was the default, so it isn't carried over

COLUMNS = [
    ("borrower_email", sa.String(length=200)), ("borrower_phone", sa.String(length=50)),
    ("guarantors", sa.String(length=300)), ("property_province", sa.String(length=50)),
    ("property_type", sa.String(length=30)), ("appraisal_date", sa.Date()), ("pin", sa.String(length=50)),
    ("ownership_pct", sa.Numeric(precision=6, scale=2)), ("broker_fee", sa.Numeric(precision=12, scale=2)),
    ("lawyer_name", sa.String(length=200)), ("renewal_fee", sa.Numeric(precision=12, scale=2)),
    ("nsf_fee", sa.Numeric(precision=12, scale=2)), ("prepayment_terms", sa.String(length=300)),
    ("insurance_expiry", sa.Date()), ("property_tax_status", sa.String(length=100)),
]


def _text(v):
    return str(v).strip() if v is not None and str(v).strip() else None


def _money(v):
    return f"${Decimal(str(v)):,.2f}" if v is not None and Decimal(str(v)) > 0 else None


def _details(row):
    """The removed values worth keeping, as note lines."""
    province = _text(row.property_province)
    if province and province.upper() in ("ON", "ONTARIO"):
        province = None
    share = None
    if row.ownership_pct is not None and Decimal(str(row.ownership_pct)) != 100:
        share = f"{Decimal(str(row.ownership_pct)).normalize():f}%"
    lines = [
        ("Email", _text(row.borrower_email)),
        ("Phone", _text(row.borrower_phone)),
        ("Guarantors", _text(row.guarantors)),
        ("Province", province),
        ("Property type", PROPERTY_TYPES.get(row.property_type)),
        ("Appraisal date", _text(row.appraisal_date) and str(row.appraisal_date)[:10]),
        ("PIN / legal", _text(row.pin)),
        ("Your share", share),
        ("Broker fee", _money(row.broker_fee)),
        ("Lawyer", _text(row.lawyer_name)),
        ("Renewal fee", _money(row.renewal_fee)),
        ("NSF fee", _money(row.nsf_fee)),
        ("Prepayment terms", _text(row.prepayment_terms)),
        ("Insurance expiry", _text(row.insurance_expiry) and str(row.insurance_expiry)[:10]),
        ("Property tax", _text(row.property_tax_status)),
    ]
    return [f"{label}: {value}" for label, value in lines if value]


def upgrade():
    conn = op.get_bind()
    names = ", ".join(name for name, _ in COLUMNS)
    for row in conn.execute(sa.text(f"SELECT id, notes, {names} FROM mortgage")).all():
        lines = _details(row)
        if not lines:
            continue
        block = "Other details:\n" + "\n".join(lines)
        notes = f"{row.notes.rstrip()}\n\n{block}" if row.notes and row.notes.strip() else block
        conn.execute(sa.text("UPDATE mortgage SET notes = :n WHERE id = :id"), {"n": notes, "id": row.id})

    with op.batch_alter_table('mortgage') as batch_op:
        for name, _ in COLUMNS:
            batch_op.drop_column(name)


def downgrade():
    # The columns come back empty; the values copied into notes stay there.
    with op.batch_alter_table('mortgage') as batch_op:
        for name, type_ in COLUMNS:
            batch_op.add_column(sa.Column(name, type_, nullable=True))
