"""tests/test_update_never_blocks.py — the update must not be able to hang itself

Pure tests: no database, no Redis, no broker, no processes started. Every
function under test here makes a DECISION, and the decisions are what went
wrong; the SQL and the queue are stubbed out around them.

THE FAILURE THESE LOCK DOWN

Reported as three separate faults — «updating isn't working», «stop button
doesn't stop the process», «we can't see which stock is updating now» — and they
were one chain:

  1. A row delete on /update enqueued tasks.refresh_analytics_only. It carried
     acks_late, so the worker killed during its six-minute rebuild left the
     message unacknowledged; kombu handed it back on the NEXT worker boot, and
     the rebuild started again from the beginning. The unacked entry survives in
     dump.rdb, so this repeated on every restart.
  2. That rebuild ran on the same queue as the fetches. --pool=solo (the only
     pool Windows has) runs one task at a time, so all twelve batches of the
     etf job queued behind it, no symbol was ever claimed, and the page showed
     «در حال دریافت: …» with no name — symptom 3.
  3. «توقف» set update_job.status='stopping' and left the ENDING of the job to a
     worker. The only worker was busy for six minutes, so nothing set
     finished_at — symptom 2 — and active_job_id() went on returning that job,
     so every later «اجرای به‌روزرسانی» was refused with "one is already
     running" — symptom 1. It was found stuck in that state nine hours later.

The measurements that pin the numbers below come from the run itself: job 10 sat
in 'stopping' with 293 symbols pending and started_at NULL, and the redelivered
refresh was observed running REFRESH MATERIALIZED VIEW CONCURRENTLY for a sixth
minute while the queue held 14 messages.
"""
import os
import sys
import types

import pytest

# Captured before the autouse guard below replaces it. Only one test wants the
# real fan-out — the one whose subject IS the fan-out — and it starts nothing
# itself: it calls ensure_worker(), which that test stubs.
try:
    import dev_boot as _dev_boot
    _REAL_ENSURE_WORKERS = _dev_boot.ensure_workers
except Exception:                                    # dev_boot unavailable
    _REAL_ENSURE_WORKERS = None


@pytest.fixture(autouse=True)
def no_processes(monkeypatch):
    """Nothing here may spawn a Celery worker. Not a formality: start_job() now
    calls market.ensure_local_worker() before anything else, so a test that
    exercises the real start_job REALLY starts two workers — and two real
    workers consuming `updates` silently sabotaged verify_order06's kill test,
    fetching its scratch symbols from the live TSETMC and recording eight of
    them as failed. Autouse, so it covers tests written after this one too."""
    try:
        import dev_boot
    except Exception:
        dev_boot = None
    if dev_boot is not None:
        # Two layers. ensure_workers() is what market.ensure_local_worker()
        # calls, so stubbing it keeps the real code path under test while
        # starting nothing; blocking Popen underneath it means a future test
        # that reaches past that still cannot spawn anything.
        monkeypatch.setattr(dev_boot, "ensure_workers",
                            lambda: {r: "already-running"
                                     for r in dev_boot.WORKER_ROLES})
        monkeypatch.setattr(dev_boot.subprocess, "Popen", _must_not_start)


@pytest.fixture
def jobs_mod():
    return pytest.importorskip("jobs")


@pytest.fixture
def market_mod():
    return pytest.importorskip("market")


@pytest.fixture
def tasks_mod():
    return pytest.importorskip("tasks")


# ---------------------------------------------------------------------------
# 1. «توقف» ends the job in the request that asked for it
# ---------------------------------------------------------------------------
def _fake_job(**over):
    """One update_job row, shaped like the real one — job 10 as it was found."""
    job = {"id": 10, "kind": "etf", "status": "stopping", "stop_requested": True,
           "pause_requested": False, "start_date": "1405-05-25",
           "end_date": "1405-05-28", "full_rebuild": False, "subset": 0,
           "total": 293, "result": None, "source": "manual", "created_at": 0,
           "started_at": None, "finished_at": None, "heartbeat_at": None}
    job.update(over)
    return job


def _counts(**over):
    c = {"total": 293, "ok": 0, "failed": 0, "skipped": 0, "running": 0,
         "pending": 293}
    c.update(over)
    return c


def test_a_stop_with_nothing_in_flight_finishes_the_job(monkeypatch, jobs_mod):
    """The reported bug. No worker had claimed a single symbol, so there was
    nothing for a worker to notice the flag with — and the job stayed 'stopping'
    for nine hours, blocking every later update."""
    finished = {}
    monkeypatch.setattr(jobs_mod, "get_job", lambda jid: _fake_job())
    monkeypatch.setattr(jobs_mod, "summary_counts", lambda jid: _counts())
    monkeypatch.setattr(jobs_mod, "release_stale", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod, "finish_job",
                        lambda jid, status=None, result=None: finished.update(
                            {"id": jid, "status": status}))

    assert jobs_mod.close_stopped_job(10) is True
    assert finished == {"id": 10, "status": "stopped"}, (
        "a stop with nothing in flight must set finished_at here, not wait for "
        "a Celery worker that may never look")


def test_a_symbol_still_being_fetched_is_waited_for(monkeypatch, jobs_mod):
    """The other half: a worker mid-write owns that symbol. Ending the job under
    it would let a new run start against the same ticker."""
    monkeypatch.setattr(jobs_mod, "get_job", lambda jid: _fake_job())
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(running=1, pending=292))
    monkeypatch.setattr(jobs_mod, "release_stale", lambda *a, **k: None)
    monkeypatch.setattr(jobs_mod, "finish_job", _must_not_finish)

    assert jobs_mod.close_stopped_job(10) is False


def test_a_symbol_claimed_by_a_dead_worker_does_not_hold_the_stop(monkeypatch,
                                                                 jobs_mod):
    """release_stale() is called FIRST with the in-flight grace, so a 'running'
    row left behind by a killed worker is returned to the pending pool instead
    of standing in for a live fetch for ever."""
    released, state = {}, {"running": 1}

    def fake_release(job_id, older_than_seconds=None):
        released["grace"] = older_than_seconds
        state["running"] = 0           # the row was stale; it is pending again

    monkeypatch.setattr(jobs_mod, "get_job", lambda jid: _fake_job())
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(running=state["running"]))
    monkeypatch.setattr(jobs_mod, "release_stale", fake_release)
    monkeypatch.setattr(jobs_mod, "finish_job", lambda *a, **k: None)

    assert jobs_mod.close_stopped_job(10) is True
    assert released["grace"] == jobs_mod.STOP_INFLIGHT_GRACE


def test_the_inflight_grace_outlasts_one_symbol(jobs_mod):
    """It has to exceed the slowest single symbol — three attempts with a linear
    backoff plus TSETMC's own timeouts — or a stop would declare the job over
    while a worker was legitimately still fetching."""
    tasks = pytest.importorskip("tasks")
    worst_backoff = sum(tasks.TICKER_BACKOFF * n
                        for n in range(1, tasks.TICKER_RETRIES))
    assert jobs_mod.STOP_INFLIGHT_GRACE > worst_backoff + 30


def test_a_finalizing_job_is_not_torn_out_from_under_the_rebuild(monkeypatch,
                                                                 jobs_mod):
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="finalizing"))
    monkeypatch.setattr(jobs_mod, "finish_job", _must_not_finish)
    assert jobs_mod.close_stopped_job(10) is False


def test_an_already_finished_job_is_reported_finished(monkeypatch, jobs_mod):
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="stopped",
                                              finished_at="2026-08-20"))
    monkeypatch.setattr(jobs_mod, "finish_job", _must_not_finish)
    assert jobs_mod.close_stopped_job(10) is True


def test_a_job_nobody_asked_to_stop_is_not_closed(monkeypatch, jobs_mod):
    """close_stopped_job() ends a run. It may only ever do that because a stop
    was requested — the poll in market.job_status() calls it, and a general
    "end the job" reachable from a polled endpoint is a different bug."""
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="running",
                                              stop_requested=False))
    monkeypatch.setattr(jobs_mod, "finish_job", _must_not_finish)
    monkeypatch.setattr(jobs_mod, "release_stale", _must_not_finish)
    assert jobs_mod.close_stopped_job(10) is False


def test_stop_job_closes_the_job_and_does_not_only_flag_it(monkeypatch,
                                                          market_mod):
    """market.stop_job() used to be request_stop() and nothing else, which is
    why «توقف» recorded a wish rather than stopping anything."""
    calls = []
    fake = types.SimpleNamespace(
        active_job_id=lambda: 10,
        request_stop=lambda jid: calls.append(("request_stop", jid)),
        close_stopped_job=lambda jid: calls.append(("close", jid)) or True,
    )
    monkeypatch.setitem(sys.modules, "jobs", fake)
    monkeypatch.setattr(market_mod, "_refresh_after_stop", lambda jid: None)

    assert market_mod.stop_job() is True
    assert calls == [("request_stop", 10), ("close", 10)]


def test_a_poll_closes_a_stop_whose_worker_never_came_back(monkeypatch,
                                                          market_mod):
    """stop_job() leaves a job in 'stopping' when a symbol is still in flight,
    on the assumption that the worker holding it will finish and close the job.
    If that worker is gone, nothing else ever would — so the status poll, which
    happens every three seconds anyway, is where it gets noticed."""
    calls = []
    snaps = [{"status": "stopping", "job_id": 10, "running": True},
             {"status": "stopped", "job_id": 10, "running": False}]

    fake = types.SimpleNamespace(
        ensure_tables=lambda: None,
        snapshot=lambda jid=None: snaps[len(calls)],
        close_stopped_job=lambda jid: calls.append(jid) or True,
    )
    monkeypatch.setitem(sys.modules, "jobs", fake)

    assert market_mod.job_status()["status"] == "stopped"
    assert calls == [10]


def test_a_poll_of_a_healthy_run_writes_nothing(monkeypatch, market_mod):
    """It is a polled endpoint: the healing must not fire on any other state."""
    fake = types.SimpleNamespace(
        ensure_tables=lambda: None,
        snapshot=lambda jid=None: {"status": "running", "job_id": 10,
                                   "running": True},
        close_stopped_job=_must_not_finish,
    )
    monkeypatch.setitem(sys.modules, "jobs", fake)
    assert market_mod.job_status()["status"] == "running"


# ---------------------------------------------------------------------------
# 2. A ghost job must not block the next update for ever
# ---------------------------------------------------------------------------
def test_a_stopped_but_unclosed_job_is_reaped_before_refusing_a_new_run(
        monkeypatch, jobs_mod):
    seen = {}
    monkeypatch.setattr(jobs_mod, "get_job", lambda jid: _fake_job())
    monkeypatch.setattr(jobs_mod, "close_stopped_job",
                        lambda jid, grace=None: seen.setdefault("closed", jid))
    assert jobs_mod.reap_dead_job(10) == 10
    assert seen["closed"] == 10


def test_a_job_whose_symbols_all_finished_gets_its_lost_tail_re_queued(
        monkeypatch, jobs_mod):
    """Every symbol terminal, status still 'running': the finalize message went
    down with the worker that should have sent it. Nothing else in the system
    re-sends it, and active_job_id() keeps the job blocking new runs."""
    sent = {}
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="running",
                                              stop_requested=False))
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(ok=293, pending=0, running=0))
    monkeypatch.setattr(jobs_mod, "claim_finalize", lambda jid: True)
    fake_tasks = types.SimpleNamespace(
        finalize_update=types.SimpleNamespace(
            delay=lambda jid: sent.setdefault("job", jid)))
    monkeypatch.setitem(sys.modules, "tasks", fake_tasks)

    jobs_mod.reap_dead_job(10)
    assert sent["job"] == 10


def test_with_no_broker_a_finished_job_is_closed_rather_than_left_finalizing(
        monkeypatch, jobs_mod):
    """Leaving it in 'finalizing' would swap one ghost for another."""
    finished = {}
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="running",
                                              stop_requested=False))
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(ok=293, pending=0, running=0))
    monkeypatch.setattr(jobs_mod, "claim_finalize", lambda jid: True)
    monkeypatch.setattr(jobs_mod, "finish_job",
                        lambda jid, **k: finished.setdefault("job", jid))

    def boom(*a, **k):
        raise RuntimeError("broker unavailable")

    monkeypatch.setitem(sys.modules, "tasks", types.SimpleNamespace(
        finalize_update=types.SimpleNamespace(delay=boom)))

    assert jobs_mod.reap_dead_job(10) is True
    assert finished["job"] == 10


def test_a_dead_finalize_is_re_queued_so_the_next_run_can_start(monkeypatch,
                                                               jobs_mod):
    """The worst ghost of the three: claim_finalize() will not re-enter a job
    already in 'finalizing', so when the worker running the rebuild dies there
    is nothing left in the system that would ever finish it — and
    active_job_id() reports it as the active job for ever."""
    sent = {}
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="finalizing",
                                              stop_requested=False))
    monkeypatch.setattr(jobs_mod, "finalize_stalled", lambda jid, secs: True)
    monkeypatch.setattr(jobs_mod, "claim_finalize", _must_not_claim)
    monkeypatch.setitem(sys.modules, "tasks", types.SimpleNamespace(
        FINALIZE_STALE_AFTER=900,
        finalize_update=types.SimpleNamespace(
            delay=lambda jid: sent.setdefault("job", jid))))

    assert jobs_mod.reap_dead_job(10) is True
    assert sent["job"] == 10


def test_a_rebuild_that_is_merely_slow_is_not_restarted(monkeypatch, jobs_mod):
    """Twenty materialized views take minutes with nothing to report. Starting a
    second rebuild beside a healthy one is the failure this threshold prevents."""
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="finalizing",
                                              stop_requested=False))
    monkeypatch.setattr(jobs_mod, "finalize_stalled", lambda jid, secs: False)
    monkeypatch.setitem(sys.modules, "tasks", types.SimpleNamespace(
        FINALIZE_STALE_AFTER=900,
        finalize_update=types.SimpleNamespace(delay=_must_not_claim)))

    assert jobs_mod.reap_dead_job(10) is False


def test_a_genuinely_running_job_is_left_alone(monkeypatch, jobs_mod):
    monkeypatch.setattr(jobs_mod, "get_job",
                        lambda jid: _fake_job(status="running",
                                              stop_requested=False))
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(ok=40, running=1, pending=252))
    monkeypatch.setattr(jobs_mod, "claim_finalize", _must_not_claim)
    monkeypatch.setattr(jobs_mod, "finish_job", _must_not_finish)
    assert jobs_mod.reap_dead_job(10) is False


def test_the_worker_is_checked_before_the_active_job_is_judged(monkeypatch,
                                                              market_mod):
    """Order matters twice over. _busy_message() asks the reconciler to look at a
    silent job, and the reconciler is itself a queued task — so on a machine
    whose worker has died, nudging it before restarting the worker recovers
    nothing at all."""
    order = []
    fake_jobs = types.SimpleNamespace(
        ensure_tables=lambda: None,
        active_job_id=lambda: order.append("active") or 10,
        blocking_job_id=lambda: 10,
        reap_dead_job=lambda jid, grace=None: False,
        get_job=lambda jid: _fake_job(status="running"),
        last_activity=lambda jid: 4.0,
        create_job=_must_not_create,
    )
    monkeypatch.setitem(sys.modules, "jobs", fake_jobs)
    # Overrides the autouse no-op above, to observe WHEN it is called.
    monkeypatch.setattr(market_mod, "ensure_local_worker",
                        lambda: order.append("worker"))

    with pytest.raises(RuntimeError):
        market_mod.start_job("etf", "1405-05-25", "1405-05-28")
    assert order[0] == "worker", order


def test_a_background_rebuild_does_not_block_a_new_run(monkeypatch, market_mod):
    """The second report: «توقف» worked, and then the update form vanished and
    every new run was refused — for the six minutes the analytics rebuild took.
    A 'finalizing' job has no symbols left and touches nothing a new run
    touches, so it is not a collision."""
    order = []
    fake_jobs = types.SimpleNamespace(
        ensure_tables=lambda: None,
        active_job_id=lambda: 10,
        blocking_job_id=lambda: None,          # finalizing is not blocking
        reap_dead_job=lambda jid, grace=None: order.append("reap") or False,
        create_job=lambda *a, **k: order.append("create") or 11,
        tse_reference=lambda kind, tickers: [(1, "خودرو")],
        # start_job() dispatches what is OUTSTANDING, not the whole reference
        # list: create_job() may pre-mark the symbols an earlier run of the
        # same window already finished. See jobs.already_done_elsewhere().
        pending_tickers=lambda jid: ["خودرو"],
        finish_job=lambda *a, **k: None,
        get_job=lambda jid: _fake_job(status="finalizing"),
    )
    monkeypatch.setitem(sys.modules, "jobs", fake_jobs)
    monkeypatch.setitem(sys.modules, "tasks", types.SimpleNamespace(
        dispatch_job=lambda *a, **k: order.append("dispatch")))

    assert market_mod.start_job("etf", "1405-05-25", "1405-05-28") == 11
    assert order == ["reap", "create", "dispatch"]


def test_the_two_questions_are_asked_of_different_states(jobs_mod):
    """active_job_id() follows the job for the UI and the reconciler;
    blocking_job_id() answers "would a new run collide?". Only the second may
    ignore 'finalizing', and mixing them up is the bug above."""
    import inspect
    active = inspect.getsource(jobs_mod.active_job_id)
    blocking = inspect.getsource(jobs_mod.blocking_job_id)
    assert "finalizing" in active
    assert "finalizing" not in blocking.split('"""')[-1], (
        "a rebuild in the background must not count as a collision")
    assert set(jobs_mod.BUSY_STATES) == {"queued", "running", "stopping"}


def test_start_job_reaps_before_it_refuses(monkeypatch, market_mod):
    """The order matters: reap, THEN look again. Checking once and refusing is
    what turned one abandoned job into a permanently disabled feature."""
    order = []
    state = {"active": 10}

    def fake_reap(jid, grace=None):
        order.append("reap")
        state["active"] = None
        return True

    fake_jobs = types.SimpleNamespace(
        ensure_tables=lambda: None,
        active_job_id=lambda: state["active"],
        blocking_job_id=lambda: state["active"],
        reap_dead_job=fake_reap,
        create_job=lambda *a, **k: order.append("create") or 11,
        tse_reference=lambda kind, tickers: [(1, "خودرو")],
        # start_job() dispatches what is OUTSTANDING, not the whole reference
        # list: create_job() may pre-mark the symbols an earlier run of the
        # same window already finished. See jobs.already_done_elsewhere().
        pending_tickers=lambda jid: ["خودرو"],
        finish_job=lambda *a, **k: None,
    )
    fake_tasks = types.SimpleNamespace(
        dispatch_job=lambda *a, **k: order.append("dispatch"))
    monkeypatch.setitem(sys.modules, "jobs", fake_jobs)
    monkeypatch.setitem(sys.modules, "tasks", fake_tasks)
    monkeypatch.setattr(market_mod, "ensure_local_worker", lambda: None)

    assert market_mod.start_job("etf", "1405-05-25", "1405-05-28") == 11
    assert order == ["reap", "create", "dispatch"]


def test_a_run_is_still_refused_while_one_is_really_active(monkeypatch,
                                                          market_mod):
    fake_jobs = types.SimpleNamespace(
        ensure_tables=lambda: None,
        active_job_id=lambda: 10,
        blocking_job_id=lambda: 10,
        reap_dead_job=lambda jid, grace=None: False,
        get_job=lambda jid: _fake_job(status="running"),
        last_activity=lambda jid: 4.0,
        create_job=_must_not_create,
    )
    monkeypatch.setitem(sys.modules, "jobs", fake_jobs)
    with pytest.raises(RuntimeError) as e:
        market_mod.start_job("etf", "1405-05-25", "1405-05-28")
    assert "#10" in str(e.value), "say WHICH job is in the way"


def test_a_silent_job_asks_for_a_reconcile_on_the_way_out(monkeypatch,
                                                          market_mod):
    """Refusing is not enough when the blocking job has not moved in hours: the
    message says so, and the watchdog is nudged so the next attempt succeeds."""
    asked = {}
    monkeypatch.setitem(sys.modules, "jobs", types.SimpleNamespace(
        get_job=lambda jid: _fake_job(status="running"),
        last_activity=lambda jid: None))
    monkeypatch.setitem(sys.modules, "tasks", types.SimpleNamespace(
        reconcile=types.SimpleNamespace(
            delay=lambda job_id=None: asked.setdefault("job", job_id))))

    msg = market_mod._busy_message(10)
    assert asked["job"] == 10
    assert "بازیابی" in msg


# ---------------------------------------------------------------------------
# 3. The analytics rebuild must not be able to block the fetch queue
# ---------------------------------------------------------------------------
def test_the_refresh_task_is_not_redelivered(tasks_mod):
    """acks_late on this task is what made every worker boot begin with a
    six-minute rebuild nobody asked for, blocking the whole update behind it."""
    assert tasks_mod.refresh_analytics_only.acks_late is False


def test_the_refresh_task_expires(tasks_mod):
    """A rebuild asked for at 09:38 is not worth six minutes at 18:48 — by then
    any update has rebuilt the views anyway."""
    assert tasks_mod.REFRESH_EXPIRES > 0
    assert tasks_mod.refresh_analytics_only.expires == tasks_mod.REFRESH_EXPIRES


def test_a_second_refresh_skips_while_one_is_running(monkeypatch, tasks_mod):
    ran = {"count": 0}
    fake_cache = types.SimpleNamespace(claim_refresh=lambda owner="": False,
                                       release_refresh=lambda: None)
    fake_db = types.SimpleNamespace(
        refresh_analytics=lambda: ran.__setitem__("count", ran["count"] + 1),
        clear_cache=lambda: None)
    monkeypatch.setitem(sys.modules, "cache", fake_cache)
    monkeypatch.setitem(sys.modules, "db", fake_db)

    out = tasks_mod.refresh_analytics_only("rows deleted")
    assert out["skipped"] is True
    assert ran["count"] == 0, "a duplicate rebuild produces identical rows"


def test_the_lock_is_released_even_when_the_rebuild_raises(monkeypatch,
                                                          tasks_mod):
    """A lock left behind by a failed rebuild would silence the NEXT one for its
    whole TTL, which is the one that matters."""
    released = {"n": 0}

    def boom():
        raise RuntimeError("refresh failed")

    monkeypatch.setitem(sys.modules, "cache", types.SimpleNamespace(
        claim_refresh=lambda owner="": True,
        release_refresh=lambda: released.__setitem__("n", released["n"] + 1)))
    monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(
        refresh_analytics=boom, clear_cache=lambda: None))

    with pytest.raises(RuntimeError):
        tasks_mod.refresh_analytics_only("boom")
    assert released["n"] == 1


def test_maintenance_and_fetching_are_on_separate_queues():
    celery_app = pytest.importorskip("celery_app")
    routes = celery_app.app.conf.task_routes
    assert routes["tasks.fetch_batch"]["queue"] == celery_app.FETCH_QUEUE
    for name in ("tasks.finalize_update", "tasks.refresh_analytics_only",
                 "tasks.reconcile"):
        assert routes[name]["queue"] == celery_app.MAINTENANCE_QUEUE, (
            f"{name} on the fetch queue means a rebuild blocks every fetch "
            "behind it on a --pool=solo worker")
    assert celery_app.FETCH_QUEUE != celery_app.MAINTENANCE_QUEUE


def test_the_reconciler_never_queues_behind_the_work_it_rescues(tasks_mod):
    """reconcile()'s entire purpose is to notice that the fetch queue has
    stopped moving. Scheduled onto that same queue it inherits the block."""
    import inspect
    src = inspect.getsource(tasks_mod._rearm) + inspect.getsource(
        tasks_mod._reconcile_on_boot)
    assert 'queue="updates"' not in src
    assert "MAINTENANCE_QUEUE" in src


def test_the_two_local_workers_consume_different_queues():
    boot = pytest.importorskip("dev_boot")
    celery_app = pytest.importorskip("celery_app")
    fetch = boot._worker_argv(boot.FETCH)
    maint = boot._worker_argv(boot.MAINTENANCE)
    assert f"--queues={celery_app.FETCH_QUEUE}" in fetch
    assert f"--queues={celery_app.MAINTENANCE_QUEUE}" in maint
    # Distinct node names, or Celery treats them as one worker sharing a mailbox.
    assert fetch[fetch.index("-n") + 1] != maint[maint.index("-n") + 1]
    # Distinct pid files, or each start would think the other's worker was its own.
    assert boot._pid_file(boot.FETCH) != boot._pid_file(boot.MAINTENANCE)


def test_both_workers_are_started_by_one_command(monkeypatch):
    boot = pytest.importorskip("dev_boot")
    started = []
    monkeypatch.setattr(boot, "ensure_worker",
                        lambda role=boot.FETCH: started.append(role) or "started")
    monkeypatch.setattr(boot, "ensure_workers", _REAL_ENSURE_WORKERS)
    monkeypatch.setattr(boot.redis_boot, "ensure_running", lambda: "started")
    boot.start_services()
    assert set(started) == set(boot.WORKER_ROLES)


# ---------------------------------------------------------------------------
# 3b. The watchdog has to survive the worker that was holding it
# ---------------------------------------------------------------------------
class _FakeRedis:
    """Just enough Redis for _rearm: SET NX EX and DELETE."""

    def __init__(self, keys=()):
        self.keys = set(keys)
        self.deleted = []

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True

    def delete(self, key):
        self.deleted.append(key)
        self.keys.discard(key)


def _watchdog_env(monkeypatch, tasks_mod, existing_key=True):
    key = "bn:watchdog:10"
    r = _FakeRedis({key} if existing_key else ())
    scheduled = []
    monkeypatch.setitem(sys.modules, "cache", types.SimpleNamespace(
        _client=lambda: r, PREFIX="bn"))
    monkeypatch.setattr(tasks_mod.reconcile, "apply_async",
                        lambda **kw: scheduled.append(kw))
    return r, scheduled


def test_a_second_watchdog_is_not_armed_beside_an_existing_one(monkeypatch,
                                                              tasks_mod):
    """Six batches finishing at once must not become six watchdog chains."""
    r, scheduled = _watchdog_env(monkeypatch, tasks_mod)
    assert tasks_mod._rearm(10, 90) is False
    assert scheduled == []


def test_the_reconciler_may_always_hand_the_baton_to_its_successor(monkeypatch,
                                                                  tasks_mod):
    """The order 06 kill test caught this. Worker A held the only pending
    watchdog message and was killed, so the message died with it — while the NX
    key it had set was still alive. Worker B's boot reconcile found the job
    progressing (other batches were running), correctly declined to
    re-dispatch, and then could NOT arm a successor because of that key. When
    the healthy batches finished, the one orphaned batch had nobody left to
    notice it: the run sat at 53 of 60 symbols until the broker's 900-second
    visibility timeout."""
    r, scheduled = _watchdog_env(monkeypatch, tasks_mod)
    assert tasks_mod._rearm(10, 20, renew=True) is True
    assert r.deleted == ["bn:watchdog:10"]
    assert len(scheduled) == 1
    assert scheduled[0]["countdown"] == 20


def test_reconcile_renews_rather_than_competing_with_itself(tasks_mod):
    import inspect
    src = inspect.getsource(tasks_mod.reconcile)
    assert "_rearm(jid, stale_seconds)" not in src, (
        "a reconcile that cannot re-arm is a watchdog that dies with the worker "
        "that scheduled it")
    assert src.count("renew=True") >= 1


def test_a_finished_batch_leaves_a_watchdog_behind(tasks_mod):
    """The one place guaranteed to run repeatedly while a job is alive. The NX
    key makes it a no-op whenever a chain already exists."""
    import inspect
    src = inspect.getsource(tasks_mod.fetch_batch)
    assert "_rearm(job_id, STALE_AFTER)" in src


# ---------------------------------------------------------------------------
# 4. The page must never claim to be fetching when nothing is
# ---------------------------------------------------------------------------
def test_a_silent_run_is_reported_as_stalled(monkeypatch, jobs_mod):
    """«we can't see which stock is updating now» was the page holding the last
    symbol name it had ever seen while the job made no progress at all. The
    server now says how long it has been silent, so the page can too."""
    monkeypatch.setattr(jobs_mod, "get_job", lambda jid=None: _fake_job(
        status="running", stop_requested=False))
    monkeypatch.setattr(jobs_mod, "current_job", lambda: jobs_mod.get_job(10))
    monkeypatch.setattr(jobs_mod, "summary_counts", lambda jid: _counts())
    monkeypatch.setattr(jobs_mod, "last_activity", lambda jid: 3600.0)
    monkeypatch.setattr(jobs_mod, "_now", lambda job: 0)
    monkeypatch.setattr(jobs_mod.db, "_rows", lambda *a, **k: [])
    monkeypatch.setattr(jobs_mod.db, "_one", lambda *a, **k: {"n": 0})

    snap = jobs_mod.snapshot()
    assert snap["idle"] == 3600
    assert snap["stalled"] is True
    assert snap["current"] is None


def test_a_healthy_run_is_not_reported_as_stalled(monkeypatch, jobs_mod):
    monkeypatch.setattr(jobs_mod, "get_job", lambda jid=None: _fake_job(
        status="running", stop_requested=False))
    monkeypatch.setattr(jobs_mod, "current_job", lambda: jobs_mod.get_job(10))
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(ok=40, running=1, pending=252))
    monkeypatch.setattr(jobs_mod, "last_activity", lambda jid: 2.0)
    monkeypatch.setattr(jobs_mod, "_now", lambda job: 0)
    monkeypatch.setattr(jobs_mod.db, "_rows", lambda *a, **k: [])
    monkeypatch.setattr(jobs_mod.db, "_one", lambda *a, **k: {"n": 0})

    snap = jobs_mod.snapshot()
    assert snap["stalled"] is False


def test_a_finalizing_run_is_not_reported_as_stalled(monkeypatch, jobs_mod):
    """A rebuild has no symbol activity to report for minutes at a time. Calling
    that stalled would put a warning on the healthiest state there is."""
    monkeypatch.setattr(jobs_mod, "get_job", lambda jid=None: _fake_job(
        status="finalizing", stop_requested=False))
    monkeypatch.setattr(jobs_mod, "current_job", lambda: jobs_mod.get_job(10))
    monkeypatch.setattr(jobs_mod, "summary_counts",
                        lambda jid: _counts(ok=293, pending=0))
    monkeypatch.setattr(jobs_mod, "last_activity", lambda jid: 400.0)
    monkeypatch.setattr(jobs_mod, "_now", lambda job: 0)
    monkeypatch.setattr(jobs_mod.db, "_rows", lambda *a, **k: [])
    monkeypatch.setattr(jobs_mod.db, "_one", lambda *a, **k: {"n": 0})

    assert jobs_mod.snapshot()["stalled"] is False


def test_the_stall_hint_outlasts_one_slow_symbol(jobs_mod):
    tasks = pytest.importorskip("tasks")
    worst = sum(tasks.TICKER_BACKOFF * n for n in range(1, tasks.TICKER_RETRIES))
    assert jobs_mod.STALLED_HINT_AFTER > worst


def test_the_page_is_told_which_worker_is_missing(monkeypatch, market_mod):
    """"Nothing is happening" is not actionable; "the fetch worker is not
    running" is."""
    monkeypatch.delenv("APP_ENV", raising=False)
    fake_boot = types.SimpleNamespace(
        WORKER_ROLES=("fetch", "maintenance"),
        worker_running=lambda role: role == "maintenance")
    monkeypatch.setitem(sys.modules, "dev_boot", fake_boot)
    assert market_mod.local_worker_states() == {"fetch": False,
                                                "maintenance": True}


def test_production_does_not_guess_at_worker_liveness(monkeypatch, market_mod):
    """There is no pid file for another container, and reporting False would put
    a permanent false alarm on a healthy deployment's page."""
    monkeypatch.setenv("APP_ENV", "production")
    assert market_mod.local_worker_states() is None


def test_production_web_workers_do_not_start_celery_workers(monkeypatch,
                                                            market_mod):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setitem(sys.modules, "dev_boot", types.SimpleNamespace(
        ensure_workers=_must_not_start))
    assert market_mod.ensure_local_worker() is None


def test_a_dead_local_worker_is_restarted_before_a_job_is_queued(monkeypatch,
                                                                market_mod):
    """Queueing at a queue nobody consumes is the other way this feature dies
    silently: the job row appears and no symbol is ever claimed."""
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setitem(sys.modules, "dev_boot", types.SimpleNamespace(
        ensure_workers=lambda: {"fetch": "started",
                                "maintenance": "already-running"}))
    assert market_mod.ensure_local_worker() == {"fetch": "started",
                                                "maintenance": "already-running"}


# ---------------------------------------------------------------------------
# helpers that fail loudly
# ---------------------------------------------------------------------------
def _must_not_finish(*a, **k):
    raise AssertionError("finish_job must not be called here")


def _must_not_claim(*a, **k):
    raise AssertionError("claim_finalize must not be called here")


def _must_not_create(*a, **k):
    raise AssertionError("a new job must not be created here")


def _must_not_start(*a, **k):
    raise AssertionError("no process may be started here")
