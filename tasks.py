"""
tasks.py — وظیفه‌های Celery برای دریافت داده‌های بازار
The market-data update, as Celery tasks.

SHAPE OF A RUN

    market.start_job()  →  jobs.create_job()          one row per symbol, 'pending'
                        →  group(fetch_batch × N)     one task per batch of symbols
                              each batch, on finishing, asks the database
                              "is anything still outstanding?" — and whoever
                              gets the answer "no" enqueues:
                        →  finalize_update()          refresh_analytics() + cache bump

WHY NOT A CHORD

`chord(group(...))(finalize)` is the idiomatic way to say "run this after all of
those", and it is wrong here. A chord counts completed header tasks in Redis and
fires when the count reaches the group size. With task_acks_late (which is what
makes a killed worker's task come back at all) a worker killed between finishing
a task and acknowledging it causes that task to run a second time — incrementing
the counter twice and firing the callback while symbols are still outstanding.
The analytics would then be refreshed against a half-written table, and the run
would look finished while it was not.

jobs.claim_finalize() asks PostgreSQL instead, in a single atomic statement that
only succeeds when no symbol is left pending or running. Exactly one caller wins,
however many times tasks are redelivered. The last task in the chain still calls
db.refresh_analytics() and still bumps the cache version — it is just chosen by
the database rather than by a counter that redelivery can corrupt.
"""
import os
import time

from celery.signals import worker_ready
from celery.utils.log import get_task_logger

from celery_app import MAINTENANCE_QUEUE, app
import jobs
import tse_fetch

log = get_task_logger(__name__)

# Symbols per task. Small enough that a killed worker loses little work and that
# a batch finishes far inside the broker's visibility timeout; large enough that
# 780 symbols do not become 780 messages with their own scheduling overhead.
BATCH_SIZE = int(os.environ.get("CELERY_BATCH_SIZE", "25"))

# Per-symbol retries INSIDE a batch. This is separate from Celery's task-level
# retry: one unreachable symbol should not send its 24 healthy neighbours round
# again. Only a failure that looks like the whole service is down does that.
TICKER_RETRIES = int(os.environ.get("UPDATE_TICKER_RETRIES", "3"))
TICKER_BACKOFF = float(os.environ.get("UPDATE_TICKER_BACKOFF", "3"))

# If this many symbols in a row fail transiently, TSETMC itself is down. Retry
# the whole batch with Celery's exponential backoff rather than burning through
# the remaining symbols recording failures that are really one outage.
OUTAGE_STREAK = int(os.environ.get("UPDATE_OUTAGE_STREAK", "8"))

# How long after a worker boots to look for orphaned work, and how idle a job
# must be at that point to count as abandoned. Short, because a worker starting
# up is the strongest hint available that another one just died.
BOOT_RECONCILE_DELAY = int(os.environ.get("UPDATE_BOOT_RECONCILE_DELAY", "10"))
BOOT_STALE_AFTER = int(os.environ.get("UPDATE_BOOT_STALE_AFTER", "20"))


class ServiceOutage(Exception):
    """Raised when enough consecutive symbols fail that the service, not the
    symbol, is the problem. The number 8 is not arbitrary: the last logged run
    before this order recorded exactly eight consecutive 'No data returned'
    errors and reported them as eight symbols with no data."""


@app.task(bind=True, name="tasks.fetch_batch",
          autoretry_for=(ServiceOutage,),
          retry_backoff=30,          # 30s, 60s, 120s …
          retry_backoff_max=900,
          retry_jitter=True,
          max_retries=5,
          acks_late=True)
def fetch_batch(self, job_id, kind, tickers, start, end, full=False):
    """Fetch and store one batch of symbols.

    Safe to run more than once with the same arguments: every symbol is taken
    through jobs.claim_ticker(), which refuses to hand out one that already
    finished, and tse_fetch.store() replaces rather than appends. That pair is
    what "resumes without losing or duplicating tickers" reduces to."""
    jobs.mark_started(job_id)
    done = failed = skipped = 0
    streak = 0

    for ticker in tickers:
        stop, paused = jobs.control_flags(job_id)
        if stop:
            log.info("job %s: stop requested — abandoning rest of batch", job_id)
            break
        if paused and jobs.wait_while_paused(job_id):
            break

        claim = jobs.claim_ticker(job_id, ticker)
        if claim is None:
            # Already finished — this batch is a redelivery, or a retry that
            # overlaps work another worker completed. Skipping is the whole
            # point: without it, this is where duplicate rows would come from.
            skipped += 1
            continue

        entity_id, attempt_no = claim["entity_id"], claim["attempts"]
        try:
            rows = _fetch_one_with_retries(kind, entity_id, ticker, start, end, full)
            jobs.mark_ok(job_id, ticker, rows)
            done += 1
            streak = 0
        except tse_fetch.FetchError as e:
            # Recorded as a FAILED symbol carrying its attempt count, never as a
            # silent zero. The /update page shows the count next to the symbol.
            jobs.mark_failed(job_id, ticker, e.reason, str(e),
                             extra_attempts=getattr(e, "tries", 1) - 1)
            failed += 1
            if isinstance(e, (tse_fetch.TransientFetchError, tse_fetch.NoDataError)):
                streak += 1
            else:
                streak = 0
            log.warning("job %s %s: %s (attempt %s) — %s",
                        job_id, ticker, e.reason, attempt_no, e)
            if streak >= OUTAGE_STREAK:
                jobs.heartbeat(job_id)
                raise ServiceOutage(
                    f"{streak} consecutive failures fetching {kind} — "
                    f"treating as a TSETMC outage, retrying the batch")

    jobs.heartbeat(job_id)
    log.info("job %s batch: ok=%s failed=%s skipped=%s", job_id, done, failed, skipped)
    _maybe_finalize(job_id)
    # Belt and braces for the watchdog. The chain is normally armed at dispatch
    # and then kept alive by reconcile() re-arming itself, and both of those can
    # be lost with the worker that held the pending message. This is the one
    # place guaranteed to run repeatedly while a job is alive, and the NX key
    # inside _rearm() means it is a no-op whenever a chain already exists.
    _rearm(job_id, STALE_AFTER)
    return {"job_id": job_id, "ok": done, "failed": failed, "skipped": skipped}


def _fetch_one_with_retries(kind, entity_id, ticker, start, end, full):
    """Per-symbol retry with a short linear backoff, so one flaky symbol does not
    drag its whole batch through Celery's much longer exponential backoff."""
    last = None
    for attempt in range(1, TICKER_RETRIES + 1):
        try:
            return tse_fetch.fetch_and_store(kind, entity_id, ticker, start, end, full)
        except (tse_fetch.TransientFetchError, tse_fetch.NoDataError) as e:
            last = e
            # Stamp how many times this symbol was actually tried, so the count
            # the UI shows is the real one rather than "claimed once".
            e.tries = attempt
            if attempt < TICKER_RETRIES:
                time.sleep(TICKER_BACKOFF * attempt)
        except tse_fetch.FetchError as e:
            e.tries = attempt
            raise                     # a StoreError will not fix itself
    raise last


def _maybe_finalize(job_id):
    """Enqueue the tail of the chain if this batch was the last one outstanding."""
    try:
        if jobs.claim_finalize(job_id):
            finalize_update.delay(job_id)
    except Exception as e:                       # never fail a good batch on this
        log.error("job %s: could not schedule finalize: %s", job_id, e)


@app.task(name="tasks.finalize_update", acks_late=True)
def finalize_update(job_id):
    """The last task in the chain.

    Rebuilds the materialized analytics from order 02 against the newly written
    prices, then bumps the Redis analytics version from order 04 so every
    Gunicorn worker drops its cached rows at the same instant. Doing it in this
    order matters: bumping first would let a request repopulate the cache from
    the still-stale views."""
    import db
    timings = {}
    # acks_late means this message can be redelivered — a worker killed between
    # finishing the rebuild and acknowledging it gets handed the same job again,
    # and kombu also restores a dead consumer's unacked messages when the next
    # worker connects. Without this guard that costs another full pass over
    # twenty materialized views (six minutes, measured, and longer under load)
    # to produce byte-identical results, while the update page waits.
    # 'finalizing' is NOT terminal, so reconcile's deliberate re-enqueue of a
    # stuck tail still goes through.
    already = jobs.get_job(job_id)
    if already and already["status"] in ("done", "stopped", "failed") \
            and already["finished_at"]:
        log.info("job %s is already finished — skipping a duplicate finalize",
                 job_id)
        return {"job_id": job_id, "timings": timings, "duplicate": True,
                **jobs.summary_counts(job_id)}

    # A stopped run that got through nothing, or a run whose every symbol
    # failed, wrote no prices — so there is nothing for the views to be stale
    # against. Skipping the rebuild there is what makes «توقف» feel immediate
    # instead of leaving the page on «در حال بازسازی تحلیل‌ها…» for six minutes
    # to recompute identical numbers.
    written = 0
    try:
        written = jobs.rows_written(job_id)
    except Exception as e:
        log.error("job %s: could not count written rows: %s", job_id, e)
        written = 1                               # unknown → do the safe thing
    if not written:
        log.info("job %s: no rows written — skipping the analytics rebuild", job_id)
        jobs.finish_job(job_id)
        counts = jobs.summary_counts(job_id)
        log.info("job %s finished: ok=%s failed=%s of %s",
                 job_id, counts["ok"], counts["failed"], counts["total"])
        return {"job_id": job_id, "timings": timings, "skipped_refresh": True,
                **counts}

    log.info("job %s: refreshing materialized analytics", job_id)
    try:
        timings = db.refresh_analytics()          # this already calls clear_cache()
    except Exception as e:
        log.error("job %s: refresh_analytics failed: %s", job_id, e)
    finally:
        # Explicit and idempotent: refresh_analytics() bumps the version at the
        # end, but if it raised part-way the cache must still be invalidated —
        # some views may have been refreshed before the failure.
        try:
            db.clear_cache()
        except Exception as e:
            log.error("job %s: cache version bump failed: %s", job_id, e)
        jobs.finish_job(job_id)
    counts = jobs.summary_counts(job_id)
    log.info("job %s finished: ok=%s failed=%s of %s",
             job_id, counts["ok"], counts["failed"], counts["total"])
    # New prices exist as of this moment, so this is where an alert about
    # today's close becomes possible. Enqueued rather than called inline: the
    # analytics rebuild above already held this worker for minutes, and a
    # failure in alert evaluation must not mark a good update as failed.
    try:
        evaluate_alerts.apply_async(queue=MAINTENANCE_QUEUE)
    except Exception as e:
        log.error("job %s: could not queue alert evaluation: %s", job_id, e)
    return {"job_id": job_id, "timings": timings, **counts}


# How long a refresh message stays worth running. A rebuild asked for at 09:38
# because rows were deleted is not worth six minutes at 18:48 — by then any
# update, and the next nightly run, has rebuilt the views anyway. Without an
# expiry the message simply waits for a worker, however long that takes, which
# is how a queue drained on the next `python app.py` began with a rebuild
# nobody was waiting for.
REFRESH_EXPIRES = int(os.environ.get("ANALYTICS_REFRESH_EXPIRES", "1800"))


@app.task(name="tasks.refresh_analytics_only",
          # NOT acks_late, and this is the one task where that is right.
          # acks_late exists so a worker killed mid-task has its work redone —
          # correct for a fetch, whose result is rows nobody else will write.
          # This task's result is a rebuild that the next update, the next
          # delete and the nightly run all redo anyway, and its cost is minutes
          # of the only slot a solo worker has. Acknowledged on receipt, a
          # killed rebuild is simply lost; with acks_late it came back on EVERY
          # subsequent worker boot and blocked the fetch queue each time, which
          # is precisely the failure this file was reported for.
          acks_late=False,
          expires=REFRESH_EXPIRES)
def refresh_analytics_only(reason=""):
    """Rebuild the analytics without a data fetch — used after a manual row
    delete on the /update page, which changes the prices without a job."""
    import cache
    import db
    if not cache.claim_refresh(owner=f"task:{reason}"):
        log.info("analytics are already being rebuilt elsewhere — skipping (%s)",
                 reason)
        return {"skipped": True, "reason": "already-refreshing"}
    log.info("refreshing analytics (%s)", reason)
    try:
        return db.refresh_analytics()
    finally:
        db.clear_cache()
        cache.release_refresh()


@app.task(name="tasks.evaluate_alerts", acks_late=False)
def evaluate_alerts():
    """Check every active هشدار and record what fired.

    NOT acks_late: a lost evaluation is re-run by the next update or the next
    Beat tick, and the per-session guard in db.evaluate_alerts() means a repeat
    cannot duplicate an event — whereas an acks_late redelivery of a task that
    writes user-visible notifications is a task that can report the same move
    twice after a worker is killed."""
    import db
    import filter_engine

    out = db.evaluate_alerts()
    if out["fired"]:
        log.info("alerts: %s of %s rules fired", out["fired"], out["checked"])
        for e in out["events"]:
            log.info("alert fired: %s", e["message"])
    else:
        log.info("alerts: none of %s rules fired", out["checked"])

    # «بلاک هشدار» on a saved «طراحی فیلتر» graph. Separate from the price rules
    # above because it is a different shape of work — a market-wide scan per
    # filter, not one number per symbol — but it belongs on the same schedule:
    # both want to run the moment new prices exist, and both write into the same
    # notification feed. A failure here must not lose the price alerts that
    # already fired, so it is caught rather than allowed to fail the task.
    try:
        designed = filter_engine.evaluate_filter_alerts()
    except Exception:
        log.exception("designed filter alerts failed")
        designed = {"checked": 0, "fired": 0, "events": []}
    if designed["fired"]:
        log.info("filter alerts: %s symbols from %s saved filters",
                 designed["fired"], designed["checked"])
    return {**out, "filters": designed}


@app.task(name="tasks.nightly_update")
def nightly_update(kind="stock"):
    """Beat entry point: fetch everything since the last date already stored.

    Skips itself when a run is already in flight, so a long full rebuild started
    by hand is never trampled by the schedule."""
    import db
    import market

    # blocking_job_id(), not active_job_id(): a job that is only rebuilding its
    # analytics is not fetching anything, and skipping the night's fetch because
    # of it would leave the database a day behind until the next night.
    active = jobs.blocking_job_id()
    if active:
        log.warning("nightly %s skipped — job %s is still running", kind, active)
        return {"skipped": True, "active_job": active}

    latest = db.latest_date(kind)
    start = market.next_day(latest)      # market.FIRST_JALALI when there is none
    end = market.yesterday_jalali()
    if start > end:
        log.info("nightly %s: already up to date (latest %s)", kind, latest)
        return {"skipped": True, "reason": "up-to-date", "latest": latest}

    job_id = market.start_job(kind, start, end, source="beat")
    log.info("nightly %s: job %s queued for %s..%s", kind, job_id, start, end)
    return {"job_id": job_id, "kind": kind, "start": start, "end": end}


STALE_AFTER = int(os.environ.get("UPDATE_STALE_AFTER", "90"))

# 'finalizing' needs its own, much longer threshold. A job in that state has no
# symbol activity to measure — the single remaining task is a materialized-view
# rebuild that legitimately takes minutes with nothing to report — so the 90s
# rule would call a healthy finalize stalled and start a second one beside it.
#
# It has to exceed the slowest rebuild that can really happen. 900s was chosen
# against a measured 350s; a stopped etf run then took 600s (21:57→22:07) with
# 13 symbols written, which leaves a margin of 1.5x and none at all under load.
# 1800s instead. Waiting half an hour to notice a rebuild whose worker died used
# to be expensive because a 'finalizing' job blocked every new update; since
# jobs.blocking_job_id() stopped treating it as a collision, the only cost is a
# stale row on the page, and the alternative — a second six-to-ten-minute
# rebuild started beside a healthy one — is worse.
FINALIZE_STALE_AFTER = int(os.environ.get("UPDATE_FINALIZE_STALE_AFTER", "1800"))


@app.task(name="tasks.reconcile")
def reconcile(job_id=None, stale_seconds=None):
    """Put a stalled job back on the rails.

    THIS IS WHAT MAKES A KILLED WORKER RECOVER PROMPTLY.

    Celery's own answer to a lost worker is the broker's visibility timeout: an
    unacknowledged message is handed to another consumer only once that timeout
    expires. On Redis that is a client-side sweep, and the timeout has to be
    longer than the longest legitimate task or a task still running would be
    handed out twice — so it cannot also be short enough to be a recovery
    mechanism. Left to itself, killing a worker parks its batch for minutes.

    The job tables make a better answer available. If symbols are outstanding
    and NOTHING has moved for `stale_seconds`, the messages holding them are
    gone; the remaining symbols are simply re-dispatched. Should the original
    message reappear later, its batch finds every symbol already 'ok' and skips
    them, so the overlap costs nothing.

    Runs on a Beat schedule and, more importantly, whenever a worker boots —
    which is exactly what a restarted container does."""
    stale_seconds = STALE_AFTER if stale_seconds is None else stale_seconds
    jid = job_id or jobs.active_job_id()
    if not jid:
        return {"reconciled": None}
    job = jobs.get_job(jid)
    if not job or job["status"] in ("done", "stopped", "failed"):
        return {"reconciled": jid, "status": job["status"] if job else None}

    if job["status"] == "finalizing":
        # A worker killed DURING the rebuild leaves the job here for ever:
        # claim_finalize() will not re-enter a job that is already 'finalizing',
        # so nothing re-enqueues the tail and the only thing that ever would is
        # the broker's visibility timeout — an hour, during which the page shows
        # a running job and no new update can start. Re-enqueueing is safe:
        # finalize_update() is a refresh plus finish_job(), both idempotent.
        # NOT is_stalled(): it requires an outstanding symbol, and a
        # 'finalizing' job has none by construction, so it answered False here
        # every single time and this whole branch was unreachable. See
        # jobs.finalize_stalled().
        if jobs.finalize_stalled(jid, FINALIZE_STALE_AFTER):
            log.warning("job %s stuck finalizing — re-enqueueing the tail", jid)
            finalize_update.delay(jid)
        _rearm(jid, stale_seconds, renew=True)
        return {"reconciled": jid, "finalizing": True}

    if not jobs.is_stalled(jid, stale_seconds):
        # Either it is progressing, or everything is finished and finalize is
        # the only thing left to do. Check again later: a job that is healthy
        # NOW can be orphaned a second after this returns, and a single
        # boot-time check would miss exactly that.
        _maybe_finalize(jid)
        _rearm(jid, stale_seconds, renew=True)
        return {"reconciled": jid, "stalled": False}

    jobs.release_stale(jid, older_than_seconds=0)
    remaining = jobs.pending_tickers(jid)
    if not remaining:
        _maybe_finalize(jid)
        return {"reconciled": jid, "remaining": 0}

    stop, _ = jobs.control_flags(jid)
    if stop:
        jobs.finish_job(jid, status="stopped")
        return {"reconciled": jid, "stopped": True}

    log.warning("job %s stalled with %s symbols outstanding — re-dispatching",
                jid, len(remaining))
    dispatch_job(jid, job["kind"], remaining, job["start_date"],
                 job["end_date"], job["full_rebuild"])
    _rearm(jid, stale_seconds, renew=True)
    return {"reconciled": jid, "redispatched": len(remaining)}


def _rearm(job_id, delay, renew=False):
    """Keep exactly one watchdog alive for as long as a job is running.

    Without this, recovery would depend entirely on Beat's five-minute schedule,
    and a deployment running workers but no Beat would never recover at all.
    Re-arming turns the reconciler into a watchdog that follows the job.

    The Redis SET NX is what keeps it to ONE chain: several workers all finishing
    batches would otherwise each re-arm, and the chain would fan out.

    `renew` is for the caller that OWNS the chain — reconcile() passing the baton
    to its own successor. Without it the chain could not survive its own worker
    dying, and the order 06 kill test caught exactly that: worker A held the only
    pending watchdog message and was killed, so the message went with it, while
    the NX key it had set was still alive. Worker B's boot reconcile then found
    the job progressing (other batches were running), correctly declined to
    re-dispatch, and could not arm a successor because of that key — so when the
    healthy batches finished, the one orphaned batch had nobody left to notice
    it. The run sat at 53 of 60 symbols until the broker's 900-second visibility
    timeout, which is the delay this whole mechanism exists to avoid."""
    try:
        import cache
        r = cache._client()
        if r is None:                      # no Redis → Beat is the only net
            return False
        key = f"{cache.PREFIX}:watchdog:{job_id}"
        if renew:
            # This caller is the chain. Handing the baton on is not fan-out.
            r.delete(key)
        if not r.set(key, b"1", nx=True, ex=int(delay) + 30):
            return False
        reconcile.apply_async(kwargs={"job_id": job_id, "stale_seconds": delay},
                              countdown=delay, queue=MAINTENANCE_QUEUE)
        return True
    except Exception as e:
        log.error("job %s: could not re-arm the reconciler: %s", job_id, e)
        return False


# Backwards-compatible alias: reap_stale was this task's first name.
reap_stale = reconcile


@worker_ready.connect
def _reconcile_on_boot(sender=None, **kwargs):
    """A worker that has just started is, from the job's point of view, the
    replacement for one that died. Ask it to check for orphaned work.

    The countdown gives any surviving worker a moment to prove it is still
    making progress, so a routine scale-up does not trigger a re-dispatch."""
    try:
        reconcile.apply_async(kwargs={"stale_seconds": BOOT_STALE_AFTER},
                              countdown=BOOT_RECONCILE_DELAY,
                              queue=MAINTENANCE_QUEUE)
        log.info("worker ready — reconcile scheduled in %ss", BOOT_RECONCILE_DELAY)
    except Exception as e:
        log.error("could not schedule boot reconcile: %s", e)


def dispatch_job(job_id, kind, tickers, start, end, full=False):
    """Split a job's symbols into batches and put them on the queue.

    Called by market.start_job() from the web process, and again by
    market.resume_job_tasks() when a job needs re-dispatching."""
    from celery import group
    batches = [tickers[i:i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)]
    sig = group(fetch_batch.s(job_id, kind, batch, start, end, full)
                for batch in batches)
    result = sig.apply_async()
    log.info("job %s: dispatched %s batches (%s symbols)",
             job_id, len(batches), len(tickers))
    # Arm the watchdog HERE, at the only point where a job is known to have work
    # in flight. It used to be armed only by reconcile() re-arming itself and by
    # Beat's five-minute schedule — which means that on a laptop, where Beat is
    # not running (start_local.ps1 starts a worker and nothing else), a job whose
    # worker died had no recovery path at all: the boot-time reconcile had
    # already run and found nothing, so nothing was ever scheduled again.
    _rearm(job_id, STALE_AFTER)
    return result
