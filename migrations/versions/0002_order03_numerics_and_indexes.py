"""order 03 — adj_* to double precision, and the (ticker, date) indexes

Revision ID: 0002
Revises: 0001
Created: order 07

Captures the schema half of order 03. That order had three parts; only two of
them are schema:

  * Dates. j_date stayed varchar(10) and a real `date` column was already
    present — order 03 moved the QUERIES onto it. That is a change in db.py,
    not in the schema, so there is nothing here for it.
  * Numerics. Every adj_* column was `numeric` and was cast with ::float on
    every read — six million casts per market-wide scan. They become
    double precision here.
  * Indexes. (ticker, date DESC) INCLUDE (adj_final) on both price tables, so
    the single-ticker detail and chart lookups are index-only scans. The older
    (ticker, j_date) pair could not serve them and is dropped.

WHY THIS COMES BEFORE THE MATERIALIZED VIEWS (0003)

Chronologically order 02 built the views and order 03 changed the columns, but a
migration chain is not a diary. A materialized view holds the types of the
expressions it selects, so building the views first and then altering the
underlying columns would leave the views pinned to numeric until their next full
rebuild. Doing the type change first means the views are born correct.

This is the one revision here that does real work on a large table: ALTER TYPE
rewrites the whole table and holds an ACCESS EXCLUSIVE lock while it does. On the
6.1M-row production table expect minutes and plan a window. It is skipped
entirely when the columns are already double precision, so re-running is free.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

PRICE_TABLES = ("stockpricehistory", "etfpricehistory")
ADJ_COLUMNS = ("adj_open", "adj_high", "adj_low", "adj_close", "adj_final")

# The j_date-era indexes order 03 found unused (measured: 0 scans across a full
# workload) and dropped, reclaiming ~151 MB.
LEGACY_INDEXES = ("ix_sph_ticker_jdate", "ix_sph_jdate",
                  "ix_eph_ticker_jdate", "ix_eph_jdate")

NEW_INDEXES = {
    "stockpricehistory": "ix_sph_ticker_date",
    "etfpricehistory": "ix_eph_ticker_date",
}


def _column_types(bind, table):
    rows = bind.execute(sa.text(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = :t"), {"t": table}).fetchall()
    return {r[0]: r[1] for r in rows}


def _tables(bind):
    return set(sa.inspect(bind).get_table_names())


def upgrade():
    bind = op.get_bind()
    present = _tables(bind)

    for table in PRICE_TABLES:
        if table not in present:
            continue
        types = _column_types(bind, table)
        todo = [c for c in ADJ_COLUMNS
                if types.get(c) not in (None, "double precision")]
        if not todo:
            continue
        # ONE statement for all five columns, not five statements.
        #
        # ALTER COLUMN ... TYPE rewrites the entire table and holds an ACCESS
        # EXCLUSIVE lock while it does. PostgreSQL coalesces several ALTER
        # COLUMN clauses in a single ALTER TABLE into ONE rewrite, so this is
        # five times less work and five times less downtime than the obvious
        # loop — on the 6.1M-row production table that is the difference
        # between roughly a minute and roughly five.
        #
        # USING is required: numeric → double precision is not an implicit cast.
        clauses = ", ".join(
            f"ALTER COLUMN {c} TYPE double precision USING {c}::double precision"
            for c in todo)
        op.execute(f"ALTER TABLE {table} {clauses}")

    # --- the index the detail pages actually use ---------------------------
    for table, name in NEW_INDEXES.items():
        if table not in present:
            continue
        # INCLUDE (adj_final) makes the lookup index-only — it never touches
        # the heap. Matches db.ensure_indexes(), which stays in step so a
        # database that has not been migrated yet still gets the index at boot.
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} "
                   f"(ticker, date DESC) INCLUDE (adj_final)")

    for name in LEGACY_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")


def downgrade():
    """Reverses the indexes. Does NOT reverse the type change.

    double precision → numeric is not lossless: the binary double 0.1 does not
    round-trip to the decimal 0.1, so every adjusted price would shift in the
    last places and the returns computed from them would move. A downgrade that
    silently corrupts prices is worse than no downgrade."""
    bind = op.get_bind()
    present = _tables(bind)
    for table, name in NEW_INDEXES.items():
        if table in present:
            op.execute(f"DROP INDEX IF EXISTS {name}")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sph_ticker_jdate "
               "ON stockpricehistory (ticker, j_date)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_eph_ticker_jdate "
               "ON etfpricehistory (ticker, j_date)")
