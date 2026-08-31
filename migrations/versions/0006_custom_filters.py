"""custom_filters — «طراحی فیلتر»: the graphs users draw for themselves

Revision ID: 0006
Revises: 0005
Created: filter designer

One table: the node graphs a user builds on /filter-designer. Like `saved_screens`
in 0005 this is real user state, so its shape is a contract frozen here rather
than a derived artifact that can be rebuilt.

WHY THE GRAPH IS ONE JSONB COLUMN AND NOT A SCHEMA

The obvious relational shape is `custom_filter_nodes` and `custom_filter_edges`
with a foreign key each, and it would be wrong here. Nothing ever queries INSIDE
a graph — the only two reads are "list this user's filters by name" and "give me
that whole graph so the canvas can draw it" — while the node catalogue
(`filter_engine.NODE_TYPES`) grows a new node type and a new parameter every time
someone asks for an indicator. Normalised, each of those is a migration; as
JSONB, each is one entry in a Python list. `filter_engine.normalise()` validates
every graph against the catalogue on the way in and clamps every parameter, so
the column is schemaless but never unvalidated.

The node COORDINATES are stored with it on purpose. A filter that reopens as a
pile of chips in the corner is a black box; the layout is how its author reads
their own logic back, and it is as much a part of the saved artifact as the
edges are.

`filter_engine.ensure_tables()` creates the same table idempotently at boot, for
the same two reasons `db.init_db()` and `jobs.ensure_tables()` do: the local
`python app.py` path where nobody has run Alembic, and a fresh deployment whose
first request arrives before the migration does. The DDL is transcribed here
rather than delegated so that reading this file tells you the shape.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLE = "custom_filters"
INDEX = "idx_custom_filters_user"


def _has_table(bind, name):
    return sa.inspect(bind).has_table(name)


def upgrade():
    bind = op.get_bind()
    if _has_table(bind, TABLE):
        # ensure_tables() got here first (a laptop, or a boot that beat the
        # migration). Nothing to do — CREATE TABLE IF NOT EXISTS produced the
        # identical shape.
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("kind", sa.Text, nullable=False, server_default="stock"),
        sa.Column("description", sa.Text, nullable=False, server_default=""),
        sa.Column("graph", postgresql.JSONB, nullable=False),
        # TEXT timestamps, matching every other table in this database
        # (db._utcnow()). Not a preference — a mixed schema where half the
        # tables are TIMESTAMPTZ and half are ISO strings is worse than either.
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        # Two filters with one name is a mistake rather than a preference: the
        # picker shows the name and nothing else, and save-under-an-existing-name
        # is how the UI expresses "update this one".
        sa.UniqueConstraint("user_id", "name", name="custom_filters_user_id_name_key"),
    )
    # The picker's only access path: one user's filters, most recently edited
    # first.
    op.create_index(INDEX, TABLE, ["user_id", sa.text("updated_at DESC")])


def downgrade():
    bind = op.get_bind()
    if not _has_table(bind, TABLE):
        return
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
