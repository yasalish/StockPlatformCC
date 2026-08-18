"""order 06 — the update_job / update_job_ticker control plane

Revision ID: 0004
Revises: 0003
Created: order 07

The tables that replaced update_stop.flag and update_job.meta.json when the
market-data fetch moved onto Celery. They were being created at boot by
jobs.ensure_tables(); this is where they properly belong now that there is
migration tooling, and the DDL is transcribed here rather than delegated
because — unlike the materialized views — these are real tables holding real
state, and their shape is a contract that must be frozen.

jobs.ensure_tables() is deliberately NOT removed. It stays idempotent and
harmless, and it keeps two things working: the local `python app.py` path with
no Alembic run, and a Celery worker started against a database whose migration
has not been applied yet.
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "update_job" not in tables:
        op.create_table(
            "update_job",
            sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
            sa.Column("kind", sa.Text, nullable=False),
            sa.Column("start_date", sa.Text),
            sa.Column("end_date", sa.Text),
            sa.Column("full_rebuild", sa.Boolean, nullable=False,
                      server_default=sa.text("FALSE")),
            # queued → running → finalizing → done | stopped | failed
            sa.Column("status", sa.Text, nullable=False,
                      server_default="queued"),
            # The stop/pause "flags" — a row update, so every web worker and
            # every Celery worker sees them. This is the whole point.
            sa.Column("stop_requested", sa.Boolean, nullable=False,
                      server_default=sa.text("FALSE")),
            sa.Column("pause_requested", sa.Boolean, nullable=False,
                      server_default=sa.text("FALSE")),
            sa.Column("total", sa.Integer, nullable=False, server_default="0"),
            sa.Column("subset", sa.Integer, nullable=False, server_default="0"),
            sa.Column("result", sa.Text),
            sa.Column("created_by", sa.Text),
            sa.Column("source", sa.Text, nullable=False, server_default="manual"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        )

    if "update_job_ticker" not in tables:
        op.create_table(
            "update_job_ticker",
            sa.Column("job_id", sa.BigInteger,
                      sa.ForeignKey("update_job.id", ondelete="CASCADE"),
                      nullable=False),
            sa.Column("ticker", sa.Text, nullable=False),
            sa.Column("entity_id", sa.Integer),
            # pending → running → ok | failed | skipped. The claim that makes a
            # killed worker's redelivered batch skip finished symbols keys on it.
            sa.Column("status", sa.Text, nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
            sa.Column("rows_written", sa.Integer),
            sa.Column("error", sa.Text),
            sa.Column("started_at", sa.DateTime(timezone=True)),
            sa.Column("finished_at", sa.DateTime(timezone=True)),
            sa.PrimaryKeyConstraint("job_id", "ticker"),
        )

    op.execute("CREATE INDEX IF NOT EXISTS ix_ujt_job_status "
               "ON update_job_ticker (job_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_uj_created "
               "ON update_job (created_at DESC)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS update_job_ticker")
    op.execute("DROP TABLE IF EXISTS update_job")
