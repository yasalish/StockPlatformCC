"""
celery_app.py — پیکربندی Celery
The Celery application: broker, result backend, reliability settings and the
Beat schedule.

Broker and backend are the Redis introduced in order 04, on SEPARATE logical
databases from the analytics cache. That matters: the cache is configured with
`maxmemory-policy allkeys-lru`, and an LRU eviction that removed a queued task or
a task result would lose work silently. Redis applies maxmemory per instance
rather than per database, so this is not a complete separation — it does keep
`FLUSHDB` on one from destroying the other, and it makes the keyspaces legible
in `redis-cli --scan`. A dedicated Redis instance for the broker is the correct
production step if the queue ever grows.
"""
import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import (beat_init, setup_logging as _celery_setup_logging,
                            task_postrun, task_prerun, worker_init)

import cache          # reuses the REDIS_* settings and the .env loading
import observability  # structured JSON logging + Sentry (order 07)


def _redis_url(db_index):
    if os.environ.get("REDIS_URL"):
        base = os.environ["REDIS_URL"].rsplit("/", 1)[0]
        return f"{base}/{db_index}"
    auth = f":{cache.REDIS_PASSWORD}@" if cache.REDIS_PASSWORD else ""
    return f"redis://{auth}{cache.REDIS_HOST}:{cache.REDIS_PORT}/{db_index}"


BROKER_DB = int(os.environ.get("CELERY_BROKER_DB", "1"))
RESULT_DB = int(os.environ.get("CELERY_RESULT_DB", "2"))

# ---------------------------------------------------------------------------
# Two queues, and the reason is head-of-line blocking
#
# On Windows --pool=solo is the only pool available (Celery's prefork needs
# fork(); finpy_tse drives asyncio/aiohttp and misbehaves shared across
# threads), which means ONE task at a time per worker. Put the analytics
# rebuild on the same queue as the fetches and that single slot is held for the
# whole rebuild — 350 s at best, six minutes measured — while everything else
# waits behind it:
#
#   * a job's batches sit unclaimed, so /update shows «در حال دریافت: …» with no
#     symbol name and no progress, exactly as if the updater were broken;
#   * tasks.reconcile cannot run, so the watchdog that recovers a stalled job is
#     itself stalled;
#   * a job left in 'stopping' cannot be closed out, so it stays the active job
#     and every later «اجرای به‌روزرسانی» is refused with "one is already
#     running".
#
# That is not a theoretical ordering: it is what a manual row delete followed by
# an update did on this machine, and all three symptoms were reported together.
# Fetching and maintenance therefore get separate queues and separate workers,
# so the slow, rare, interruptible-by-nobody work cannot starve the fast work
# that the page and the stop button depend on.
# ---------------------------------------------------------------------------
FETCH_QUEUE = os.environ.get("CELERY_FETCH_QUEUE", "updates")
MAINTENANCE_QUEUE = os.environ.get("CELERY_MAINTENANCE_QUEUE", "maintenance")

app = Celery(
    "boursenegar",
    broker=os.environ.get("CELERY_BROKER_URL") or _redis_url(BROKER_DB),
    backend=os.environ.get("CELERY_RESULT_BACKEND") or _redis_url(RESULT_DB),
    include=["tasks"],
)

app.conf.update(
    # --- serialisation ----------------------------------------------------
    # JSON, not pickle: the arguments are ticker lists and Jalali date strings,
    # and a pickle broker is a remote-code-execution surface.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=int(os.environ.get("CELERY_RESULT_EXPIRES", 86400)),

    # --- time -------------------------------------------------------------
    # Beat's crontab below is expressed in Tehran local time, which is the only
    # timezone the market's opening hours make sense in.
    timezone=os.environ.get("TZ", "Asia/Tehran"),
    enable_utc=True,

    # --- reliability: the settings the mid-flight kill depends on ----------
    #
    # acks_late means a task is acknowledged AFTER it finishes, not when it is
    # handed to a worker. Kill a worker mid-task and the broker still holds the
    # message, so another worker picks it up. With the default (ack on receipt)
    # the task would simply vanish with the process — which is the current bug
    # this order exists to fix, in a different costume.
    task_acks_late=True,
    # A worker that dies without acking normally leaves the message until the
    # visibility timeout expires. This makes the loss explicit and immediate.
    task_reject_on_worker_lost=True,
    # Prefetch 1: a worker holds one message at a time. With the default of 4,
    # killing a worker would strand three untouched messages for a full
    # visibility timeout before they were redelivered.
    worker_prefetch_multiplier=1,

    # Redis has no server-side ack, so kombu re-queues anything not acked within
    # this window. It MUST exceed the longest a single batch can take, or a task
    # still legitimately running gets handed to a second worker as well — which
    # is why it cannot be short enough to double as crash recovery. That job
    # belongs to tasks.reconcile(), which notices a stalled job in seconds
    # rather than waiting this out. Batches are sized (CELERY_BATCH_SIZE) to
    # finish far inside it.
    broker_transport_options={
        "visibility_timeout": int(os.environ.get("CELERY_VISIBILITY_TIMEOUT", 900)),
    },
    result_backend_transport_options={"visibility_timeout": 3600},
    broker_connection_retry_on_startup=True,

    # --- limits -----------------------------------------------------------
    task_track_started=True,
    # Hard ceiling on one batch. Well under visibility_timeout above.
    task_time_limit=int(os.environ.get("CELERY_TASK_TIME_LIMIT", 1800)),
    task_soft_time_limit=int(os.environ.get("CELERY_TASK_SOFT_TIME_LIMIT", 1500)),
    # Recycle a worker child periodically: finpy_tse/aiohttp leak sockets over
    # long runs, and a fresh child every 200 batches costs nothing.
    worker_max_tasks_per_child=int(os.environ.get("CELERY_MAX_TASKS_PER_CHILD", 200)),
    worker_send_task_events=True,
    task_send_sent_event=True,

    task_default_queue=FETCH_QUEUE,

    # Routing, so `.delay()` puts each task where its worker is listening and no
    # caller has to remember a queue name. Only fetch_batch belongs on the fetch
    # queue; everything else is maintenance — including reconcile, which exists
    # precisely to rescue a fetch queue that has gone quiet and so must never
    # queue behind it.
    task_routes={
        "tasks.fetch_batch": {"queue": FETCH_QUEUE},
        "tasks.finalize_update": {"queue": MAINTENANCE_QUEUE},
        "tasks.refresh_analytics_only": {"queue": MAINTENANCE_QUEUE},
        "tasks.reconcile": {"queue": MAINTENANCE_QUEUE},
        "tasks.nightly_update": {"queue": MAINTENANCE_QUEUE},
    },
)

# ---------------------------------------------------------------------------
# Beat — the nightly fetch
#
# The Tehran Stock Exchange trades SATURDAY to WEDNESDAY. Thursday and Friday are
# the Iranian weekend and there is nothing to fetch, so they are excluded rather
# than fetched-and-found-empty (which would look exactly like a TSETMC outage in
# the failure counts).
#
# Celery's crontab day_of_week follows cron: 0=Sunday … 6=Saturday. So trading
# days are 6,0,1,2,3 — i.e. everything except 4 (Thursday) and 5 (Friday).
#
# The market closes at 12:30 Tehran time and TSETMC publishes adjusted closes
# some hours later; 20:30 leaves a wide margin while still finishing overnight.
# ---------------------------------------------------------------------------
TRADING_DAYS = os.environ.get("BEAT_TRADING_DAYS", "6,0,1,2,3")
BEAT_HOUR = int(os.environ.get("BEAT_HOUR", "20"))
BEAT_MINUTE = int(os.environ.get("BEAT_MINUTE", "30"))

app.conf.beat_schedule = {
    "nightly-stock-update": {
        "task": "tasks.nightly_update",
        "schedule": crontab(hour=BEAT_HOUR, minute=BEAT_MINUTE,
                            day_of_week=TRADING_DAYS),
        "kwargs": {"kind": "stock"},
        "options": {"queue": MAINTENANCE_QUEUE, "expires": 6 * 3600},
    },
    "nightly-etf-update": {
        # Half an hour after the stocks so the two runs do not compete for
        # TSETMC connections, which is a common source of the timeouts that
        # look like "no data".
        "task": "tasks.nightly_update",
        "schedule": crontab(hour=BEAT_HOUR + 1, minute=BEAT_MINUTE,
                            day_of_week=TRADING_DAYS),
        "kwargs": {"kind": "etf"},
        "options": {"queue": MAINTENANCE_QUEUE, "expires": 6 * 3600},
    },
    "reconcile-stalled-jobs": {
        # The safety net for work Celery cannot recover by itself: a batch whose
        # worker died holding it. tasks.reconcile() re-dispatches the symbols
        # still outstanding once a job has visibly stopped moving. A worker also
        # runs this the moment it boots, so a restarted container recovers
        # immediately; this schedule covers the case where nothing restarts.
        "task": "tasks.reconcile",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": MAINTENANCE_QUEUE, "expires": 240},
    },
}


# ---------------------------------------------------------------------------
# Observability (order 07)
#
# Celery installs its own logging configuration on startup and would otherwise
# overwrite the JSON formatter with its plain-text one. Connecting to the
# setup_logging signal is the documented way to tell it not to: the handler
# below wins, so a worker's output is the same one-line JSON the web process
# emits and both can be shipped to the same place.
# ---------------------------------------------------------------------------
@_celery_setup_logging.connect
def _configure_celery_logging(**_kwargs):
    observability.setup_logging()


@worker_init.connect
def _worker_sentry(**_kwargs):
    observability.setup_logging()
    observability.setup_sentry("worker")


@beat_init.connect
def _beat_sentry(**_kwargs):
    observability.setup_logging()
    observability.setup_sentry("beat")


@task_prerun.connect
def _task_request_id(task_id=None, **_kwargs):
    """Give each task run the same kind of correlation id an HTTP request gets.

    Celery's task id already identifies the run; adopting it as the request id
    means every log line a task emits — including the ones from db.py and
    cache.py, which know nothing about Celery — carries it, so one grep follows
    a single ticker batch through the whole system."""
    observability.set_request_id(task_id)


@task_postrun.connect
def _task_clear_request_id(**_kwargs):
    observability.set_request_id(None)
