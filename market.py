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
    active = jobs.active_job_id()
    if active:
        raise RuntimeError(
            "یک به‌روزرسانی هم‌اکنون در حال اجراست؛ تا پایان آن صبر کنید.")

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


def stop_job():
    """Ask the running job to stop. One UPDATE, visible to every worker.

    There is no process to kill any more, and nothing to hard-kill it with: the
    workers poll jobs.control_flags() between symbols and abandon the rest of
    their batch. The symbol in flight finishes and is written — which is what
    makes the stop safe to resume from rather than something that can leave a
    half-written symbol behind."""
    import jobs
    jid = jobs.active_job_id()
    if not jid:
        return False
    jobs.request_stop(jid)
    return True


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
        return jobs.snapshot()
    except Exception as e:
        log.error("job_status failed", extra={"error": str(e)})
        return {"active": False, "running": False, "kind": None, "start": None,
                "end": None, "full": False, "subset": 0, "stopped": False,
                "paused": False, "processed": 0, "success": 0, "failed": 0,
                "success_list": [], "failed_list": [], "current": None,
                "result": None, "elapsed": 0, "job_id": None, "total": 0,
                "error": str(e)}


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
        tasks.refresh_analytics_only.delay(reason)
        return True
    except Exception as e:
        log.warning("celery unavailable — refreshing analytics in-process", extra={"error": str(e)})

    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return False
        _refreshing = True

    def _run():
        global _refreshing
        try:
            import db
            db.refresh_analytics()
        except Exception as exc:
            log.error("refresh_analytics failed", extra={"error": str(exc)})
        finally:
            with _refresh_lock:
                _refreshing = False

    threading.Thread(target=_run, name="refresh-analytics", daemon=True).start()
    return True


import threading
_refresh_lock = threading.Lock()
_refreshing = False


def analytics_refreshing():
    """True while a refresh is in flight. Covers both routes: the local thread
    fallback above, and a job sitting in the 'finalizing' state, which is the
    Celery task doing exactly the same work on another machine."""
    with _refresh_lock:
        if _refreshing:
            return True
    try:
        import jobs
        j = jobs.current_job()
        return bool(j and j["status"] == "finalizing")
    except Exception:
        return False
