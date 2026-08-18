"""order 02 — the materialized analytics views

Revision ID: 0003
Revises: 0002
Created: order 07

Creates the market-wide analytics views (mv_market_gainer_*, mv_period_gainer_*,
mv_perf_prices_*, mv_strategy_*, mv_filter_*, mv_score_* and their inputs) with
the UNIQUE indexes that let REFRESH ... CONCURRENTLY run without blocking
readers.

WHERE THE SQL COMES FROM, AND WHY

A revision normally freezes its DDL, and for a table that is right: the shape is
the contract. These views are different. Their SQL is GENERATED at runtime by
analytics_views.all_views() from PERIODS, CALC_PERIODS, PERF_PERIODS and
SCORE_WEIGHTS in db.py — deliberately, because that is the mechanism by which
the views cannot drift from the Python they replaced. Pasting a snapshot here
would break that the moment someone adds a period: the code would compute one
thing and this frozen revision another, and nothing would notice.

So the SQL is read from db.analytics_catalogue(), which is pure data — a list of
(name, ddl, unique_columns). The trade is that this revision reproduces whatever
the checked-out code defines rather than a fixed historical artefact. For
derived, rebuildable objects that is correct.

IT MUST RUN ON THE MIGRATION'S OWN CONNECTION

The obvious implementation — call db.ensure_analytics_views() — deadlocks, and
does so silently. That helper opens its own pooled connection, and revision 0002
has just taken an ACCESS EXCLUSIVE lock on stockpricehistory inside the
migration's still-uncommitted transaction. The second connection's first read of
that table waits for a lock the first connection will not release until the
migration finishes, and the migration will not finish until the second
connection returns. Both sides wait forever; PostgreSQL sees no cycle to break
because one side is merely idle-in-transaction.

Every statement below therefore runs on op.get_bind() — the one connection
Alembic owns and will commit.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _catalogue():
    """[(view_name, ddl, unique_index_columns)] in dependency order."""
    import db
    return db.analytics_catalogue()


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "stockpricehistory" not in tables:
        return

    # An empty price table means a fresh deployment. The views would build
    # instantly and hold nothing, and the first data update rebuilds them
    # anyway — so skip, and keep `alembic upgrade head` fast on a new VPS.
    rows = bind.execute(sa.text("SELECT count(*) FROM stockpricehistory")).scalar()
    if not rows:
        print("[0003] price history is empty — deferring the analytics views to "
              "the first data update")
        return

    existing = {r[0] for r in bind.execute(
        sa.text("SELECT matviewname FROM pg_matviews")).fetchall()}

    made = []
    for name, ddl, unique_cols in _catalogue():
        if name in existing:
            continue
        # exec_driver_sql, NOT op.execute().
        #
        # op.execute() wraps a string in sqlalchemy.text(), which scans it for
        # :name bind parameters — including inside SQL comments. One of the
        # generated views carries the comment "seed is sum(vals[:9])/9", and
        # `:9` is a perfectly good parameter name, so text() demanded a value
        # for it and the migration died with "A value is required for bind
        # parameter '9'". Generated DDL must never be parameter-parsed; this
        # sends the statement to psycopg2 untouched.
        bind.exec_driver_sql(ddl)
        # Not optional: REFRESH MATERIALIZED VIEW CONCURRENTLY requires a unique
        # index, and CONCURRENTLY is what keeps the pages readable during the
        # nightly refresh.
        bind.exec_driver_sql(f"CREATE UNIQUE INDEX IF NOT EXISTS ux_{name} "
                             f"ON {name} ({unique_cols})")
        made.append(name)

    print(f"[0003] analytics views: {len(made)} built"
          + (f" ({', '.join(made)})" if made else ", all present already"))


def downgrade():
    """Drops them. Safe, unlike the other downgrades here: a materialized view
    holds no source data and is rebuilt by the next data update. The application
    is explicitly migration-safe against their absence — it falls back to live
    computation and says so at startup."""
    bind = op.get_bind()
    for name, _, _ in reversed(_catalogue()):
        bind.exec_driver_sql(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
