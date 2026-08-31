"""custom_filters.alert_jd — the session an armed filter was last evaluated for

Revision ID: 0007
Revises: 0006
Created: filter designer — «بلاک هشدار»

One column. A saved graph carrying an «هشدار» block runs itself after every
market update and reports the symbols that are newly in its result; this is the
marker that says which trading session it has already been run for.

WHY IT IS NEEDED AND WHY IT IS ONE COLUMN

The de-duplication of the EVENTS needs no state at all — `alert_events` already
records what each filter told each user about, keyed `filter:<id>`, and that is
the only honest place for it: an alert is interesting precisely because an event
was written, so the events are the state and nothing can drift out of step with
them.

What that cannot express is "this filter has been evaluated for this session and
matched nothing new". `tasks.evaluate_alerts` runs on the update AND every three
hours as a safety net, and each armed filter is a market-wide scan — eight
hundred symbols against the whole graph. Without a marker, the common case (the
filter matched the same symbols it matched this morning, so no rows were
written) is indistinguishable from "never ran", and the scan repeats eight times
a day to write nothing eight times.

TEXT, holding a Jalali date like '1405-06-04' — the same shape and the same
source as `price_alerts.last_fired_jd`, which answers the same question for the
per-symbol rules. Nullable: an existing filter has never been evaluated, and
NULL is what that means. No index — the only reader already has the row.

`filter_engine.ensure_tables()` adds the same column idempotently at boot, for
the reasons 0006 lists: the local `python app.py` path where nobody has run
Alembic, and a deployment whose first request beats the migration.
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TABLE = "custom_filters"
COLUMN = "alert_jd"


def _columns(bind):
    if not sa.inspect(bind).has_table(TABLE):
        return None
    return {c["name"] for c in sa.inspect(bind).get_columns(TABLE)}


def upgrade():
    bind = op.get_bind()
    cols = _columns(bind)
    if cols is None:
        # 0006 has not run and neither has ensure_tables(). Nothing to alter;
        # whichever creates the table will create it with this column.
        return
    if COLUMN in cols:
        return                                   # ensure_tables() got here first
    op.add_column(TABLE, sa.Column(COLUMN, sa.Text, nullable=True))


def downgrade():
    bind = op.get_bind()
    cols = _columns(bind)
    if cols and COLUMN in cols:
        op.drop_column(TABLE, COLUMN)
