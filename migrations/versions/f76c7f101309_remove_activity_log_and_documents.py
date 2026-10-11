"""remove the activity log and documents

The per-mortgage activity log (with follow-up reminders) and document storage are
removed. Nothing recorded in them is thrown away:

* activity entries are appended to their mortgage's notes, oldest first;
* uploaded documents, if there are any, move to an archived_mortgage_document
  table (no longer shown in the app, but recoverable), and each mortgage's notes list
  the files it had.

Revision ID: f76c7f101309
Revises: 6c4d73648031
Create Date: 2026-10-11 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'f76c7f101309'
down_revision = '6c4d73648031'
branch_labels = None
depends_on = None

KINDS = {"note": "Note", "call": "Phone call", "email": "Email", "meeting": "Meeting",
         "letter": "Letter / notice", "legal": "Legal / enforcement"}


def _day(value):
    return str(value)[:10] if value is not None else ""  # date/datetime objects or SQLite strings


def _append_notes(conn, notes_by_mortgage, heading):
    for mortgage_id, lines in notes_by_mortgage.items():
        current = conn.execute(sa.text("SELECT notes FROM mortgage WHERE id = :id"), {"id": mortgage_id}).scalar()
        block = heading + "\n" + "\n".join(lines)
        text = f"{current.rstrip()}\n\n{block}" if current and current.strip() else block
        conn.execute(sa.text("UPDATE mortgage SET notes = :n WHERE id = :id"), {"n": text, "id": mortgage_id})


def upgrade():
    conn = op.get_bind()

    entries = {}
    for mortgage_id, kind, body, follow_up, done, created, name, email in conn.execute(sa.text(
            'SELECT a.mortgage_id, a.kind, a.body, a.follow_up_on, a.done, a.created_at, u.name, u.email '
            'FROM mortgage_activity a LEFT JOIN "user" u ON u.id = a.user_id '
            'ORDER BY a.mortgage_id, a.created_at, a.id')):
        who = f" ({name or email})" if (name or email) else ""
        line = f"{_day(created)} {KINDS.get(kind, kind)}{who}: {body.strip()}"
        if follow_up:
            line += f" [follow up {_day(follow_up)}{', done' if done else ''}]"
        entries.setdefault(mortgage_id, []).append(line)
    _append_notes(conn, entries, "Activity log:")

    if conn.execute(sa.text("SELECT COUNT(*) FROM mortgage_document")).scalar():
        op.execute(
            "CREATE TABLE archived_mortgage_document AS "
            "SELECT d.id, d.mortgage_id, m.reference AS mortgage_reference, d.filename, d.content_type, d.size, "
            "d.category, d.note, d.content, d.uploaded_at "
            "FROM mortgage_document d LEFT JOIN mortgage m ON m.id = d.mortgage_id"
        )
        files = {}
        for mortgage_id, filename, uploaded in conn.execute(sa.text(
                "SELECT mortgage_id, filename, uploaded_at FROM mortgage_document ORDER BY mortgage_id, uploaded_at, id")):
            files.setdefault(mortgage_id, []).append(f"{filename} (uploaded {_day(uploaded)})")
        _append_notes(conn, files, "Documents (archived, no longer shown in the app):")

    op.drop_table('mortgage_activity')
    op.drop_table('mortgage_document')


def downgrade():
    # The tables come back empty; what was moved into notes stays there.
    op.create_table(
        'mortgage_activity',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mortgage_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('follow_up_on', sa.Date(), nullable=True),
        sa.Column('done', sa.Boolean(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['mortgage_id'], ['mortgage.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mortgage_activity', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mortgage_activity_follow_up_on'), ['follow_up_on'], unique=False)
        batch_op.create_index(batch_op.f('ix_mortgage_activity_mortgage_id'), ['mortgage_id'], unique=False)
    op.create_table(
        'mortgage_document',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('mortgage_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=120), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('note', sa.String(length=300), nullable=True),
        sa.Column('content', sa.LargeBinary(), nullable=False),
        sa.Column('uploaded_by_id', sa.Integer(), nullable=True),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['mortgage_id'], ['mortgage.id'], ),
        sa.ForeignKeyConstraint(['uploaded_by_id'], ['user.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('mortgage_document', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_mortgage_document_mortgage_id'), ['mortgage_id'], unique=False)
