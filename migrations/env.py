"""
migrations/env.py — Alembic runtime configuration.

Two things here are deliberate and worth reading before changing them.

1. THE URL COMES FROM THE ENVIRONMENT, not from alembic.ini. db.py already
   resolves STOCK_DB_* (and refuses to start without STOCK_DB_PASSWORD); this
   reuses that resolution so a migration can never be pointed at a different
   database than the application, and so no credential is committed.

2. MIGRATIONS TAKE AN ADVISORY LOCK. `alembic upgrade head` is wired into
   container startup, and compose can start web, worker and beat at the same
   moment. Without a lock, three processes would run the same CREATE TABLE
   concurrently and two would fail. A session-level advisory lock serialises
   them: the first migrates, the others block, then find nothing to do.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# No SQLAlchemy models in this project — the schema predates any ORM and the
# application talks to psycopg2 directly. Autogenerate is therefore not usable
# and every revision is written by hand, which is also why target_metadata is
# None. Revisions state their intent explicitly rather than diffing a model.
target_metadata = None

# An arbitrary but fixed key. Any process running migrations for this
# application uses the same one.
MIGRATION_LOCK_ID = 728301


def _database_url():
    """postgresql+psycopg2://… assembled from the app's own settings."""
    import db                      # raises loudly if STOCK_DB_PASSWORD is unset
    s = db.DB_SETTINGS
    from urllib.parse import quote_plus
    return (f"postgresql+psycopg2://{quote_plus(s['user'])}:"
            f"{quote_plus(s['password'])}@{s['host']}:{s['port']}/"
            f"{quote_plus(s['dbname'])}")


def run_migrations_offline():
    """Emit SQL to stdout instead of running it — `alembic upgrade head --sql`.

    Useful for review before touching a production database, though note that
    the revisions which guard on the CURRENT schema (the baseline, and the
    order 03 type change) cannot be rendered offline: they need to inspect the
    database to know what to do."""
    context.configure(url=_database_url(), target_metadata=target_metadata,
                      literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(_database_url(), poolclass=pool.NullPool,
                                future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Keep the version table in the same schema as everything else.
            version_table="alembic_version",
            compare_type=True,
        )
        # The lock is taken INSIDE begin_transaction(), and this ordering is
        # load-bearing. Executing anything on the connection first opens an
        # implicit transaction, which makes Alembic's begin_transaction() a
        # no-op nested block — and under SQLAlchemy 2.0 a connection closed
        # without an explicit commit ROLLS BACK. The symptom is the worst kind:
        # `alembic upgrade head` reports success, prints its "Running upgrade"
        # lines, exits 0, and leaves the database completely unchanged.
        #
        # pg_advisory_xact_lock rather than pg_advisory_lock: it is scoped to
        # the transaction and released automatically on commit OR rollback, so
        # a migration that dies half way cannot leave the lock held and block
        # every future deploy. Alembic runs the whole chain in one transaction,
        # so transaction scope covers exactly the right span.
        with context.begin_transaction():
            connection.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                               {"k": MIGRATION_LOCK_ID})
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
