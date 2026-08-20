"""verify_update_flow.py — the update runs, reports, stops, and lets the next one start

Run it against the real local stack (PostgreSQL + Redis + both Celery workers):

    python verify_update_flow.py

It is an END-TO-END check, unlike tests/test_update_never_blocks.py, which
stubs the database and the broker out to assert the decisions. Here a real job
is queued at a real worker and really fetched from TSETMC, because all three
faults it covers were invisible to unit tests: each one needed a message on a
queue that nobody was consuming fast enough.

WHAT IT ASSERTS, AND WHY EACH ONE IS HERE

  1. Both workers are consuming, on SEPARATE queues. One worker on one queue is
     how a six-minute analytics rebuild came to block every fetch behind it:
     --pool=solo runs one task at a time.
  2. A running job names the symbol it is fetching. «we can't see which stock is
     updating now» was this: the job never started, and the page had nothing to
     show but the spinner.
  3. «توقف» reaches a terminal state within seconds, without a worker's help.
     The stop used to set status='stopping' and leave the ending to a Celery
     worker, so a busy or absent worker left the job stuck there — nine hours,
     in the case that was reported.
  4. A NEW job can start immediately afterwards. This is the one the user
     actually feels: a job stuck in 'stopping' stays "active", and every later
     «اجرای به‌روزرسانی» is refused with "one is already running".
  5. A refresh_analytics_only message cannot be redelivered, and a second one
     skips while the first is running.

Exit code 0 means every assertion passed. It writes prices for the two symbols
it picks, over a four-day window — the same work the update page does.
"""
import os
import sys
import time

os.environ.setdefault("APP_ENV", "development")

# Every symbol this prints is Persian, and a Windows console defaults to cp1252,
# which raises rather than printing it. Reconfigure rather than asking the caller
# to remember PYTHONIOENCODING.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import observability                                        # noqa: E402
observability.setup_logging()

import cache                                                # noqa: E402
import dev_boot                                             # noqa: E402
import jobs                                                 # noqa: E402
import market                                               # noqa: E402
import tasks                                                # noqa: E402
from celery_app import FETCH_QUEUE, MAINTENANCE_QUEUE, app   # noqa: E402

FAILED = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)
    return ok


def section(title):
    print(f"\n=== {title} ===")


# ---------------------------------------------------------------------------
# 1. the two workers
# ---------------------------------------------------------------------------
def check_workers():
    section("1. workers")
    states = dev_boot.ensure_workers()
    print(f"  ensure_workers() -> {states}")
    # A worker that has just been started needs a moment to connect before it
    # answers a broadcast; without the wait this reports a false negative.
    deadline = time.time() + 40
    queues = {}
    while time.time() < deadline:
        got = app.control.inspect(timeout=4).active_queues() or {}
        queues = {node: [q["name"] for q in qs] for node, qs in got.items()}
        if len(queues) >= 2:
            break
        time.sleep(2)
    for node, names in queues.items():
        print(f"  {node} -> {names}")
    consumed = {q for names in queues.values() for q in names}
    check("a worker consumes the fetch queue", FETCH_QUEUE in consumed)
    check("a worker consumes the maintenance queue", MAINTENANCE_QUEUE in consumed)
    check("they are two separate workers", len(queues) >= 2,
          "one worker cannot rebuild the analytics and fetch at the same time "
          "under --pool=solo")
    check("no worker consumes both", not any(
        FETCH_QUEUE in n and MAINTENANCE_QUEUE in n for n in queues.values()),
        "sharing a solo worker between them is the head-of-line block itself")


# ---------------------------------------------------------------------------
# 2 + 3 + 4. a real run, a real stop, and a run straight after it
# ---------------------------------------------------------------------------
def check_run_stop_restart(kind="etf", symbols=2):
    section("2. a run reports the symbol it is fetching")

    leftover = jobs.active_job_id()
    if leftover:
        print(f"  closing leftover job {leftover} first")
        jobs.reap_dead_job(leftover)
        if jobs.active_job_id():
            market.stop_job()

    ref = jobs.tse_reference(kind)[:symbols]
    tickers = [t for _, t in ref]
    # A short window, and a real one: yesterday back three days. Jalali dates are
    # strings here, so the arithmetic goes through jdatetime rather than through
    # next_day() three times, which would walk the wrong way.
    import jdatetime
    y, m, d = map(int, market.yesterday_jalali().split("-"))
    end_j = jdatetime.date(y, m, d)
    start, end = str(end_j - jdatetime.timedelta(days=3)), str(end_j)
    print(f"  {kind}: {tickers}  {start}..{end}")

    job_id = market.start_job(kind, start, end, tickers=tickers,
                             created_by="verify")
    print(f"  job {job_id} queued")

    # The symbol name has to appear, and it has to appear promptly: a fetch queue
    # with nothing in front of it claims its first symbol in a second or two.
    seen_current, snap = None, {}
    deadline = time.time() + 90
    while time.time() < deadline:
        snap = jobs.snapshot(job_id)
        if snap.get("current"):
            seen_current = snap["current"]
            break
        if not snap.get("running"):
            break
        time.sleep(1)
    check("a symbol is named while the job runs", bool(seen_current),
          f"current={seen_current!r} status={snap.get('status')!r} "
          f"idle={snap.get('idle')!r}")
    check("a job that is moving is not reported as stalled",
          snap.get("stalled") is False, f"stalled={snap.get('stalled')!r}")

    section("3. «توقف» reaches a terminal state without a worker's help")
    t0 = time.time()
    stopped = market.stop_job()
    took = time.time() - t0
    check("stop_job() reports it acted", stopped is True)
    # The in-flight symbol is the one thing worth waiting for, so allow it to
    # finish — but the WAIT has to be bounded by that symbol, not by a queue.
    deadline = time.time() + jobs.STOP_INFLIGHT_GRACE + 60
    while time.time() < deadline:
        job = jobs.get_job(job_id)
        if job["status"] in ("stopped", "done", "failed"):
            break
        # Exactly what a /update/status poll does — market.job_status() closes a
        # 'stopping' job once its in-flight symbol is past the grace, so a stop
        # survives the worker that was holding it dying.
        jobs.close_stopped_job(job_id)
        time.sleep(2)
    job = jobs.get_job(job_id)
    total = time.time() - t0
    check("the job is terminal", job["status"] in ("stopped", "done", "failed"),
          f"status={job['status']} after {total:.1f}s")
    check("finished_at is set", job["finished_at"] is not None,
          "an unset finished_at is what kept the page on «در حال اجرا…»")
    check("the stop request itself returns immediately", took < 5.0,
          f"{took:.2f}s")

    section("4. the next update is not blocked by the one just stopped")
    active = jobs.active_job_id()
    check("no job is left active", active is None, f"active={active}")
    try:
        second = market.start_job(kind, start, end, tickers=tickers[:1],
                                 created_by="verify")
        check("a new job starts right after a stop", True, f"job {second}")
        market.stop_job()
        for _ in range(30):
            if jobs.get_job(second)["status"] in ("stopped", "done", "failed"):
                break
            jobs.close_stopped_job(second)
            time.sleep(2)
    except Exception as e:
        check("a new job starts right after a stop", False, str(e))


# ---------------------------------------------------------------------------
# 5. the analytics refresh cannot block the fetch queue any more
# ---------------------------------------------------------------------------
def check_refresh_task():
    section("5. the analytics refresh")
    check("refresh_analytics_only is acknowledged on receipt",
          tasks.refresh_analytics_only.acks_late is False,
          "acks_late is what made every worker boot redeliver it")
    check("it expires", bool(tasks.refresh_analytics_only.expires),
          f"expires={tasks.refresh_analytics_only.expires}s")
    check("it routes to the maintenance queue",
          app.conf.task_routes["tasks.refresh_analytics_only"]["queue"]
          == MAINTENANCE_QUEUE)

    # The cross-process lock, against the real Redis.
    cache.release_refresh()
    first = cache.claim_refresh(owner="verify")
    second = cache.claim_refresh(owner="verify-2")
    cache.release_refresh()
    check("only one process may rebuild at a time", first and not second,
          f"first={first} second={second}")
    check("the lock is released", cache.refresh_in_progress() is False)

    # No REBUILD may be sitting unacknowledged on the broker. That specific
    # entry is the one that re-ran a six-minute rebuild on every restart, and
    # with acks_late gone it can no longer be created.
    #
    # Deliberately not `unacked == 0`: an unacknowledged entry is the NORMAL
    # state of any acks_late task while it runs, and verify_order06 leaves a
    # handful behind on purpose — hard-killing a worker mid-batch is what it
    # tests. Those are harmless (their job rows are deleted, so a redelivered
    # batch stops on its first control-flag read); a redelivered rebuild was
    # not.
    try:
        import json

        import redis
        r = redis.Redis.from_url(app.conf.broker_url, socket_timeout=2)
        stuck = []
        for raw in r.hvals("unacked"):
            try:
                stuck.append(json.loads(raw)[0]["headers"]["task"])
            except Exception:
                pass
        rebuilds = [t for t in stuck if t == "tasks.refresh_analytics_only"]
        check("no analytics rebuild is stuck unacknowledged", not rebuilds,
              f"unacked={len(stuck)} of which {len(rebuilds)} rebuilds"
              + (f" ({', '.join(sorted(set(stuck)))})" if stuck else ""))
    except Exception as e:
        print(f"  SKIP  broker inspection — {e}")


if __name__ == "__main__":
    print(__doc__.splitlines()[0])
    check_workers()
    check_refresh_task()
    check_run_stop_restart()
    print()
    if FAILED:
        print(f"FAILED ({len(FAILED)}):")
        for f in FAILED:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")
