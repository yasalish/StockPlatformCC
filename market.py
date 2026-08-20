"""
market.py — مدیریت کار به‌روزرسانی قیمت‌ها
The web process's view of the market-data update. Since order 06 it does no work
itself: it creates a job row, puts batches on the Celery queue, and reads state
back out of PostgreSQL.

WHAT THIS USED TO BE, AND WHY IT CHANGED

The updater ran as a subprocess spawned from a Flask request, tracked by a
module-level `_job` dict and a `threading.Thread`, with three control files on
local disk (update_stop.flag, update_pause.flag, update_job.meta.json) and a log
file that job_status() re-parsed on every poll.

Every part of that assumed one process:

  * `_job["proc"]` is a handle only the worker that spawned it holds, so
    is_running() answered "no" in the other three Gunicorn workers;
  * a «توقف» click landing on worker 2 wrote a flag file that the subprocess of
    worker 1 did poll — but only because they shared a filesystem. In separate
    containers they do not, and the stop silently does nothing;
  * progress came from scraping stdout, so it existed only where the log did;
  * the job died with its worker, with no retries and no history.

All of it is now rows in PostgreSQL (jobs.py) and messages on Redis (tasks.py),
both of which every process can see. The public functions below keep their
previous names and return shapes so app.py and update.html did not have to be
rewritten around a new vocabulary.
"""
import os

import jdatetime
from datetime import datetime, timedelta

import observability
log = observability.get_logger("boursenegar.market")

BASE = os.path.dirname(os.path.abspath(__file__))

# Detect finpy_tse WITHOUT importing it into the web process. The web process
# only enqueues jobs now — the Celery worker is what actually fetches — but the
# /update page still greys itself out when the package is absent from the image.
import importlib.util
UPDATER_AVAILABLE = importlib.util.find_spec("finpy_tse") is not None
UPDATER_ERROR = None if UPDATER_AVAILABLE else "بستهٔ finpy-tse نصب نشده است"


def yesterday_jalali():
    g = datetime.now() - timedelta(days=1)
    return str(jdatetime.date.fromgregorian(year=g.year, month=g.month, day=g.day))


def next_day(jdate):
    try:
        y, m, d = map(int, str(jdate).split("-"))
        return str(jdatetime.date(y, m, d) + jdatetime.timedelta(days=1))
    except Exception:
        return jdate


# ---------------------------------------------------------------------------
# Starting and controlling a run
# ---------------------------------------------------------------------------
def start_job(kind, start, end, full=False, tickers=None, carry_failed=False,
              created_by=None, source="manual"):
    """Create the job and queue its batches. Returns the job id.

    `carry_failed` is accepted for signature compatibility with the old
    subprocess implementation but is no longer needed: failures are rows in
    update_job_ticker, so a retry of three symbols cannot hide the other
    forty that are still failing — they are simply still there, in their own
    job, with their own attempt counts.
    """
    import jobs
    import tasks

    jobs.ensure_tables()

    # Before anything else, and before the active-job check in particular: the
    # recovery that check asks for (reconcile) is itself a queued task, so on a
    # machine whose worker has died nudging it would recover nothing. Queueing
    # work at a queue nobody consumes is also the other way this feature dies
    # quietly — the job row appears and no symbol is ever claimed. No-op in
    # production, where the workers are declared services.
    ensure_local_worker()

    # Two different questions, and conflating them is what took the update form
    # off the page for six minutes after every «توقف».
    #
    #   active   — the newest unfinished job, INCLUDING one that is only
    #              rebuilding the analytics. Worth reaping: a stopped-but-unclosed
    #              job, a lost finalize and a rebuild whose worker died are all
    #              ghosts that pass that test for ever.
    #   blocking — the newest job that a new run would actually collide with.
    #              'finalizing' is deliberately not one: every symbol is already
    #              terminal and the rebuild left behind touches nothing a new run
    #              touches. See jobs.blocking_job_id().
    active = jobs.active_job_id()
    if active:
        jobs.reap_dead_job(active)

    blocking = jobs.blocking_job_id()
    if blocking:
        raise RuntimeError(_busy_message(blocking))

    job_id = jobs.create_job(kind, start, end, full=full, tickers=tickers,
                             created_by=created_by, source=source)
    work = [t for _, t in jobs.tse_reference(kind, tickers)]
    try:
        tasks.dispatch_job(job_id, kind, work, start, end, full)
    except Exception as e:
        # The broker is unreachable. Close the job out rather than leaving a
        # 'queued' row that the UI would show as a run that never progresses.
        jobs.finish_job(job_id, status="failed",
                        result=f"RESULT error=broker_unavailable: {e}")
        raise RuntimeError(f"صف کاری در دسترس نیست (Redis/Celery): {e}") from e
    return job_id


def _busy_message(job_id):
    """Why the new run was refused, in enough detail to act on.

    "One is already running" is true and useless when the run in question has
    not moved for nine hours: it reads as a bug in the button rather than as a
    job needing a «توقف». So say which job, what state it is in, and how long it
    has been silent — and ask the reconciler to look at it on the way out."""
    import jobs
    try:
        job = jobs.get_job(job_id)
        idle = jobs.last_activity(job_id)
    except Exception:
        return "یک به‌روزرسانی هم‌اکنون در حال اجراست؛ تا پایان آن صبر کنید."

    status = (job or {}).get("status", "?")
    msg = (f"کار #{job_id} ({_STATUS_FA.get(status, status)}) هم‌اکنون فعال است؛ "
           "تا پایان آن صبر کنید یا دکمهٔ «توقف» را بزنید.")
    if idle is None or idle >= 90:
        idle_txt = "هرگز" if idle is None else f"{int(idle)} ثانیه پیش"
        msg += f" آخرین پیشرفت: {idle_txt}. درخواست بازیابی ارسال شد."
        try:
            import tasks
            tasks.reconcile.delay(job_id=job_id)
        except Exception as e:
            log.warning("could not ask for a reconcile", extra={"error": str(e)})
    return msg


_STATUS_FA = {"queued": "در صف", "running": "در حال اجرا", "stopping": "در حال توقف",
              "finalizing": "بازسازی تحلیل‌ها", "stopped": "متوقف",
              "done": "پایان‌یافته", "failed": "ناموفق"}


def ensure_local_worker():
    """Start the local Celery workers again if they died. Returns their states,
    or None when this is not a machine that manages its own workers.

    `python app.py` starts them (dev_boot), but nothing restarted one that died
    mid-session — and a dead worker looks exactly like a broken updater: the job
    row appears, no symbol is ever claimed, and the page shows «در حال دریافت»
    with nothing after it. Checking here costs one file read per started job.

    In production the workers are compose services with their own restart
    policy, and a web container starting them would be wrong; APP_ENV and
    BN_AUTOSTART_WORKER=0 both switch this off."""
    if os.environ.get("APP_ENV", "development").strip().lower() == "production":
        return None
    try:
        import dev_boot
    except Exception:
        return None
    try:
        states = dev_boot.ensure_workers()
    except Exception as e:
        log.warning("could not check the local celery workers",
                    extra={"error": str(e)})
        return None
    if any(v == "started" for v in states.values()):
        log.info("restarted a local celery worker before queueing a job",
                 extra=states)
    return states


def local_worker_states():
    """{role: True/False} for the workers this machine runs, or None.

    Cheap on purpose — two pid-file reads and a process-handle check — because
    /update/status polls every three seconds. None means "not knowable here",
    which is the honest answer in production: the workers are their own
    containers, this process has no pid file for them, and reporting False would
    put a false alarm on the page of a perfectly healthy deployment."""
    if os.environ.get("APP_ENV", "development").strip().lower() == "production":
        return None
    try:
        import dev_boot
        return {role: dev_boot.worker_running(role)
                for role in dev_boot.WORKER_ROLES}
    except Exception:
        return None


def stop_job():
    """Ask the running job to stop. One UPDATE, visible to every worker.

    There is no process to kill any more, and nothing to hard-kill it with: the
    workers poll jobs.control_flags() between symbols and abandon the rest of
    their batch. The symbol in flight finishes and is written — which is what
    makes the stop safe to resume from rather than something that can leave a
    half-written symbol behind.

    The UPDATE alone was not enough, though, and that is what «توقف نمی‌کند» was.
    Recording the wish leaves the *ending* of the job to a Celery worker, and the
    states people press the button in are exactly the ones where no worker will
    read it soon: none running, or the single solo worker busy for minutes on an
    analytics rebuild. So the job is also CLOSED here whenever nothing is in
    flight — same request, no queue involved."""
    import jobs
    jid = jobs.active_job_id()
    if not jid:
        return False
    jobs.request_stop(jid)
    closed = jobs.close_stopped_job(jid)
    log.info("stop requested", extra={"job_id": jid, "closed": closed})
    if closed:
        _refresh_after_stop(jid)
    return True


def _refresh_after_stop(job_id):
    """A stopped run that wrote prices leaves the analytics behind those prices.

    On a thread, because the alternative is doing it in the request: the worker
    check inside refresh_analytics_async() sends a Celery control broadcast whose
    FIRST call in a process costs seconds, and «توقف» has to answer instantly —
    the whole point of closing the job here. A run that wrote nothing needs none
    of this, which is the common case for a stop."""
    import threading

    def run():
        import jobs
        try:
            if jobs.rows_written(job_id):
                refresh_analytics_async(f"job {job_id} stopped")
        except Exception as e:
            log.error("could not refresh analytics after a stop",
                      extra={"job_id": job_id, "error": str(e)})

    threading.Thread(target=run, name=f"post-stop-refresh-{job_id}",
                     daemon=True).start()


def pause_job():
    import jobs
    jid = jobs.active_job_id()
    if not jid:
        return False
    jobs.request_pause(jid, True)
    return True


def resume_job():
    import jobs
    jid = jobs.active_job_id()
    if not jid:
        return False
    jobs.request_pause(jid, False)
    return True


def job_status():
    """Progress snapshot for /update, straight out of PostgreSQL.

    Same keys the log-scraping version returned, so update.html keeps working,
    plus `total`, `job_id`, `attempts_total` and a per-symbol `attempts` inside
    failed_list — the retry counts the old version had no way to know."""
    import jobs
    try:
        jobs.ensure_tables()
        snap = jobs.snapshot()
        # 'stopping' means «توقف» was recorded and one symbol was still in
        # flight, so stop_job() deliberately left the ending to the worker
        # holding it. If that worker never comes back, nothing else in the
        # system would ever close the job — and a job that never closes is the
        # ghost that blocks every later update. The page polls every three
        # seconds; that makes this the cheapest possible place to notice. It
        # only writes once, and only once the symbol is past the in-flight
        # grace, so a live worker mid-fetch is still waited for.
        if snap.get("status") == "stopping" and snap.get("job_id"):
            if jobs.close_stopped_job(snap["job_id"]):
                snap = jobs.snapshot(snap["job_id"])
        return snap
    except Exception as e:
        log.error("job_status failed", extra={"error": str(e)})
        return {"active": False, "running": False, "kind": None, "start": None,
                "end": None, "full": False, "subset": 0, "stopped": False,
                "paused": False, "processed": 0, "success": 0, "failed": 0,
                "success_list": [], "failed_list": [], "current": None,
                "result": None, "elapsed": 0, "job_id": None, "total": 0,
                "idle": None, "stalled": False, "error": str(e)}


def last_job_params():
    import jobs
    try:
        return jobs.last_job_params()
    except Exception:
        return None


def resume_job_tasks(job_id=None):
    """Re-queue whatever is left of a job.

    Celery redelivers the tasks of a killed worker by itself, so this is not the
    normal recovery path. It is for the case the broker cannot cover: messages
    lost with a flushed Redis, or a job left half-done by a stop that is now
    being continued."""
    import jobs
    import tasks
    jid = job_id or jobs.active_job_id()
    if not jid:
        return None
    job = jobs.get_job(jid)
    if not job:
        return None
    jobs.release_stale(jid, older_than_seconds=0)
    remaining = jobs.pending_tickers(jid)
    if not remaining:
        tasks._maybe_finalize(jid)
        return {"job_id": jid, "remaining": 0}
    tasks.dispatch_job(jid, job["kind"], remaining,
                       job["start_date"], job["end_date"], job["full_rebuild"])
    return {"job_id": jid, "remaining": len(remaining)}


# ---------------------------------------------------------------------------
# Analytics refresh
# ---------------------------------------------------------------------------
def _worker_listening(timeout=1.0):
    """True if at least one Celery worker answers a broadcast ping.

    `.delay()` returning without raising proves only that the BROKER accepted
    the message — not that anything will ever run it. With Redis up and no
    worker consuming the queue (the normal state of a laptop where `python
    app.py` was started but start_local.ps1 was not), the refresh sits in
    `updates` indefinitely, the clear_cache() at the end of the task never runs,
    and every analytics page keeps serving pre-delete numbers until the 6-hour
    TTL expires. Nothing in the log says so, because from the web process's
    point of view the publish succeeded.

    So: ask. A broadcast ping is bounded by `timeout` and this path only runs
    after a manual row delete, which is rare and already slow."""
    try:
        import tasks
        replies = tasks.refresh_analytics_only.app.control.ping(timeout=timeout)
        if replies:
            return True
    except Exception as e:
        # Broker unreachable, or control disabled — either way, do it here.
        log.warning("could not ask whether a celery worker is listening",
                    extra={"error": str(e)})
        return False
    # No reply is not the same as no worker. --pool=solo — the only pool that
    # works on Windows, see start_local.ps1 — runs the task on the very thread
    # that answers control broadcasts, so a worker mid-refresh is silent for the
    # several MINUTES the refresh takes. `celery inspect ping` times out on it
    # too. Treating that as "no worker" would start a duplicate rebuild in the
    # web process every time a delete landed during one.
    return _worker_has_claimed_work()


def _worker_has_claimed_work():
    """True if the broker holds a task some worker has taken but not finished.

    Redis' Kombu transport keeps those in the `unacked` hash — with acks_late
    the entry survives until the task actually completes, which is exactly the
    window a solo worker cannot answer a ping in. Reading it is one HLEN.

    A stale entry left by a killed worker would read as "present" here; that is
    the safe direction, because Celery redelivers such a message as soon as a
    worker starts, whereas a false "absent" costs a duplicate multi-minute
    rebuild."""
    try:
        import redis
        from celery_app import app as _celery
        r = redis.Redis.from_url(_celery.conf.broker_url,
                                 socket_connect_timeout=0.5, socket_timeout=0.5)
        return r.hlen("unacked") > 0
    except Exception:
        return False


def refresh_analytics_async(reason=""):
    """Rebuild the materialized analytics off the request path.

    At the end of an update this is not called at all — tasks.finalize_update()
    is the tail of the chain and does it there. This remains for the one case
    that changes prices WITHOUT a job: deleting rows by hand on /update.

    Prefers the Celery worker, so the work happens on the machine built for it
    and survives a web-worker restart. Falls back to a thread when the broker is
    unreachable, because a missed refresh means the pages keep serving the old
    numbers with no indication why."""
    import threading
    try:
        import tasks
        if _worker_listening():
            tasks.refresh_analytics_only.delay(reason)
            return True
        log.warning("no celery worker is consuming the queue — refreshing "
                    "analytics in-process instead", extra={"reason": reason})
    except Exception as e:
        log.warning("celery unavailable — refreshing analytics in-process", extra={"error": str(e)})

    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return False
        _refreshing = True

    def _run():
        global _refreshing
        import cache
        # The module flag above is per PROCESS: it stops this web worker from
        # starting two, and says nothing about the other three, or about a
        # Celery worker that took the same job. The Redis lock is what all of
        # them share. Skipping here is the right answer — whoever holds it is
        # rebuilding the very views this call wanted.
        claimed = cache.claim_refresh(owner="market-fallback")
        try:
            if not claimed:
                log.info("analytics are already being rebuilt elsewhere — "
                         "skipping the in-process refresh")
                return
            import db
            db.refresh_analytics()
        except Exception as exc:
            log.error("refresh_analytics failed", extra={"error": str(exc)})
        finally:
            if claimed:
                cache.release_refresh()
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=_run, name="refresh-analytics", daemon=True).start()
    return True


import threading
_refresh_lock = threading.Lock()
_refreshing = False


def analytics_refreshing():
    """True while a refresh is in flight, by any of the three routes:

      * the local thread fallback above — a per-process flag;
      * the shared Redis lock, which is how a rebuild running in a Celery
        worker (or in another Gunicorn worker) is visible from here at all;
      * a job sitting in 'finalizing', which is the tail of an update doing the
        same work. That one does NOT take the lock — a finalize must never skip
        its rebuild, because the prices it just wrote are the reason for it — so
        it has to be checked separately.
    """
    with _refresh_lock:
        if _refreshing:
            return True
    try:
        import cache
        if cache.refresh_in_progress():
            return True
    except Exception:
        pass
    try:
        import jobs
        j = jobs.current_job()
        return bool(j and j["status"] == "finalizing")
    except Exception:
        return False
