"""baseline — the schema as it already exists in production

Revision ID: 0001
Revises:
Created: order 07

THIS REVISION MUST BE A NO-OP ON THE LIVE DATABASE.

The «Stock» database predates any migration tooling: stocks, stockpricehistory,
etf and etfpricehistory were created by the original Streamlit scripts, and
users / watchlist by db.init_db(). Writing a baseline that unconditionally
CREATEs them would fail on every existing deployment, and writing one that
does nothing at all would leave a fresh database with no tables.

So every object here is created only if the inspector says it is missing. On the
live database that is six no-ops and a row in alembic_version — which is exactly
what "reflect the current schema without trying to recreate it" means. On an
empty database (a new VPS, or the scratch database the restore test builds) the
same revision produces the real schema.

The column definitions are a faithful transcription of what `\\d` reports today,
including the parts that are wrong: users.created_at is TEXT rather than
timestamptz, and the price tables carry both a varchar Jalali j_date and a real
date. Those are recorded as-is because a baseline documents what IS. Revision
0002 is where order 03 corrects the numerics.
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has(table):
    return table in _inspector().get_table_names()


def upgrade():
    # --- reference tables --------------------------------------------------
    if not _has("stocks"):
        op.create_table(
            "stocks",
            sa.Column("stockid", sa.Integer, primary_key=True),
            sa.Column("ticker", sa.Text, nullable=False),
            sa.Column("name", sa.Text),
            sa.Column("market", sa.Text),
            sa.Column("sector", sa.Text),
            sa.Column("sub_sector", sa.Text),
        )

    if not _has("etf"):
        op.create_table(
            "etf",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("ticker", sa.Text, nullable=False),
            sa.Column("name", sa.Text),
            sa.Column("type", sa.Text),
        )

    # --- price history -----------------------------------------------------
    # numeric here, not double precision: this is the pre-order-03 shape, and
    # 0002 is what converts it. A baseline that already had the fix would make
    # 0002 a no-op on new databases and hide the conversion entirely.
    for table, fk in (("stockpricehistory", "stock_id"),
                      ("etfpricehistory", "etf_id")):
        if _has(table):
            continue
        op.create_table(
            table,
            sa.Column(fk, sa.Integer),
            sa.Column("ticker", sa.Text),
            sa.Column("name", sa.Text),
            sa.Column("j_date", sa.String(10)),
            sa.Column("date", sa.Date),
            sa.Column("weekday", sa.Text),
            sa.Column("open", sa.Numeric),
            sa.Column("high", sa.Numeric),
            sa.Column("low", sa.Numeric),
            sa.Column("close", sa.Numeric),
            sa.Column("final", sa.Numeric),
            sa.Column("volume", sa.BigInteger),
            sa.Column("value", sa.BigInteger),
            sa.Column("no", sa.Integer),
            sa.Column("adj_open", sa.Numeric),
            sa.Column("adj_high", sa.Numeric),
            sa.Column("adj_low", sa.Numeric),
            sa.Column("adj_close", sa.Numeric),
            sa.Column("adj_final", sa.Numeric),
        )

    # --- authentication ----------------------------------------------------
    if not _has("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("username", sa.Text, nullable=False, unique=True),
            sa.Column("display_name", sa.Text, nullable=False, server_default=""),
            sa.Column("password_hash", sa.Text, nullable=False, server_default=""),
            sa.Column("email", sa.Text, server_default=""),
            sa.Column("google_id", sa.Text),
            sa.Column("role", sa.Text, nullable=False, server_default="user"),
            # TEXT, matching db.init_db(). Wrong, but it is what production has;
            # correcting it needs its own revision and a data conversion.
            sa.Column("created_at", sa.Text, nullable=False),
            sa.Column("last_login", sa.Text, server_default=""),
        )
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google "
               "ON users(google_id)")

    if not _has("watchlist"):
        op.create_table(
            "watchlist",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger,
                      sa.ForeignKey("users.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("kind", sa.Text, nullable=False),
            sa.Column("ticker", sa.Text, nullable=False),
            sa.Column("entity_id", sa.BigInteger),
            sa.Column("created_at", sa.Text, nullable=False),
            sa.UniqueConstraint("user_id", "kind", "ticker"),
        )
    op.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user "
               "ON watchlist(user_id)")


def downgrade():
    """Deliberately refuses.

    Downgrading the baseline means dropping stocks and six million price rows.
    There is no circumstance in which that is the right response to a failed
    deployment, and an `alembic downgrade base` typed by mistake must not be
    able to do it. Restore from a backup instead — deploy/scripts/restore.sh."""
    raise RuntimeError(
        "Refusing to drop the baseline schema: this would destroy the price "
        "history and every user. To roll a database back, restore a dump with "
        "deploy/scripts/restore.sh."
    )
