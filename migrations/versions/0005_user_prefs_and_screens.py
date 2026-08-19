"""order 09 — user_prefs / saved_screens (تنظیمات کاربر و غربالگرهای ذخیره‌شده)

Revision ID: 0005
Revises: 0004
Created: order 09

The two tables behind the تنظیمات screen: one row of display preferences per
account, and the saved filter presets («غربالگرهای ذخیره‌شده») the market screens
write. Both are real tables holding real user state, so — unlike the
materialized views of 0003 — their shape is a contract that is frozen here.

`db.init_db()` creates the same two tables idempotently at boot, exactly as
`jobs.ensure_tables()` still does for the order-06 job tables, and for the same
two reasons: the local `python app.py` path where nobody has run Alembic, and a
fresh deployment whose first request arrives before the migration does. The DDL
is transcribed rather than delegated so that reading this file tells you the
shape without cross-referencing a Python module — but the DEFAULTS in both
places must stay identical to `prefs.DEFAULTS`. They are what an account that
has never opened the settings page renders as, and a mismatch shows up as "the
site looks different on my other laptop", which is a miserable bug to chase.

No data migration and no backfill: `db.get_prefs()` merges a stored row UNDER
`prefs.DEFAULTS`, so every pre-existing account already answers with the
defaults and adding a preference later is a column plus a dict key.
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


# (column, SQLAlchemy type, SQL type, server default) — mirrors db._PREF_COLUMNS
# plus the original seven. Declared as data so the CREATE TABLE and the
# ADD COLUMN repair path below cannot disagree about a default; the SQL type is
# spelled out rather than derived so the ALTER statements read as the plain SQL
# they are.
_PREF_COLUMNS = [
    ("theme",          sa.Text,    "TEXT",    "'light'"),
    ("digits",         sa.Text,    "TEXT",    "'fa'"),
    ("default_kind",   sa.Text,    "TEXT",    "'stock'"),
    ("rows_per_page",  sa.Integer, "INTEGER", "50"),
    ("default_period", sa.Text,    "TEXT",    "'p20'"),
    ("density",        sa.Text,    "TEXT",    "'comfortable'"),
    ("reduce_motion",  sa.Boolean, "BOOLEAN", "FALSE"),
    ("font_scale",     sa.Text,    "TEXT",    "'md'"),
    ("top_scrollbar",  sa.Boolean, "BOOLEAN", "TRUE"),
    ("scrollbar_size", sa.Text,    "TEXT",    "'lg'"),
    ("sticky_head",    sa.Boolean, "BOOLEAN", "TRUE"),
    ("zebra",          sa.Boolean, "BOOLEAN", "FALSE"),
    ("updown_scheme",  sa.Text,    "TEXT",    "'classic'"),
    ("auto_refresh",   sa.Integer, "INTEGER", "0"),
    ("wide",           sa.Boolean, "BOOLEAN", "FALSE"),
]


def upgrade():
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())

    if "user_prefs" not in tables:
        op.create_table(
            "user_prefs",
            sa.Column("user_id", sa.BigInteger,
                      sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            *[sa.Column(name, type_, nullable=False, server_default=sa.text(default))
              for name, type_, _sql, default in _PREF_COLUMNS],
            sa.Column("updated_at", sa.Text, nullable=False),
        )
    else:
        # A database that ran an older build of this migration (or that was
        # bootstrapped by db.init_db() before a preference existed) gets the
        # missing columns rather than an error. IF NOT EXISTS keeps it
        # re-runnable, which is what makes this safe to apply to any of them.
        for name, _type, sql_type, default in _PREF_COLUMNS:
            op.execute(
                f"ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS {name} "
                f"{sql_type} NOT NULL DEFAULT {default}"
            )

    if "saved_screens" not in tables:
        op.create_table(
            "saved_screens",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.BigInteger,
                      sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column("kind", sa.Text, nullable=False),      # stock|etf
            sa.Column("page", sa.Text, nullable=False),      # market|screener|performance|…
            # The query string verbatim, without the leading '?'. Storing it as
            # text rather than parsed columns is deliberate: the filters on those
            # pages change shape as the platform grows, and a preset that is just
            # "the URL that worked" cannot go stale in a way that needs a
            # migration to repair.
            sa.Column("query", sa.Text, nullable=False, server_default=""),
            sa.Column("created_at", sa.Text, nullable=False),
            # Re-saving under a name you already used is a collision the API can
            # report as «این نام قبلاً استفاده شده» — not a silent duplicate that
            # leaves two presets with one name in the list.
            sa.UniqueConstraint("user_id", "name", name="uq_saved_screens_user_name"),
        )
        op.create_index("idx_screens_user", "saved_screens", ["user_id", sa.text("id DESC")])


def downgrade():
    """Genuinely reversible, unlike the baseline's refusal — these tables hold
    display preferences and saved URLs, not six million price rows. What is lost
    is every user's chosen theme and every saved غربالگر preset; nothing that
    cannot be re-entered in a minute, and nothing that affects market data."""
    op.drop_table("saved_screens")
    op.drop_table("user_prefs")
