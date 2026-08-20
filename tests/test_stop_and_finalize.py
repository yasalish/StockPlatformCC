"""tests/test_stop_and_finalize.py — «توقف» must actually end the job

Pure tests: no database, no Redis, no broker, no processes started.

The bug: pressing «توقف» set update_job.status='stopping', the fetch loop broke
out of the batch as designed — and then claim_finalize()'s
`status IN ('running','queued')` matched nothing, so finalize_update() was never
enqueued and finished_at was never set. snapshot() counts 'stopping' as running,
so the page span on «در حال اجرا…» for ever with no worker doing anything, and
pressing the button again changed nothing because the row was already 'stopping'.
A job was found in exactly that state, stuck with 16 of 18 symbols abandoned and
nothing left in the queue.

The second half is the cost of the fix: a stopped run then finalized by running
a six-minute materialized-view rebuild over data it had not changed, so «توقف»
still would not have felt like stopping.
"""
import os
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# claim_finalize — the statement itself, since its whole job is one WHERE clause
# ---------------------------------------------------------------------------
@pytest.fixture
def jobs_mod():
    return pytest.importorskip("jobs")


def test_claim_finalize_covers_a_stopping_job(monkeypatch, jobs_mod):
    seen = {}

    def fake_write(sql, params=(), fetch=False):
        seen["sql"] = " ".join(sql.split())
        seen["params"] = params
        return {"id": 1}

    monkeypatch.setattr(jobs_mod, "_write", fake_write)
    assert jobs_mod.claim_finalize(1) is True

    sql = seen["sql"]
    assert "'stopping'" in sql, (
        "a stopped job must be allowed to finalize, or nothing ever sets "
        "finished_at and the page spins for ever")
    # After a stop the pending symbols are abandoned on purpose; the only thing
    # worth waiting for is the one still in flight.
    assert "stop_requested" in sql and "CASE" in sql


def test_claim_finalize_reports_when_it_did_not_win(monkeypatch, jobs_mod):
    monkeypatch.setattr(jobs_mod, "_write", lambda *a, **k: None)
    assert jobs_mod.claim_finalize(1) is False


# ---------------------------------------------------------------------------
# finalize_update — do not spend six minutes rebuilding unchanged views
# ---------------------------------------------------------------------------
@pytest.fixture
def tasks_mod(monkeypatch):
    tasks = pytest.importorskip("tasks")
    jobs = sys.modules["jobs"]
    finished = []
    monkeypatch.setattr(jobs, "finish_job", lambda jid, **kw: finished.append(jid))
    monkeypatch.setattr(jobs, "summary_counts", lambda jid: {
        "total": 3, "ok": 0, "failed": 3, "skipped": 0, "running": 0, "pending": 0})
    # The default: a job that is mid-finalize, i.e. not yet closed out.
    monkeypatch.setattr(jobs, "get_job", lambda jid: {
        "id": jid, "status": "finalizing", "finished_at": None})
    tasks._finished = finished
    return tasks


def test_a_redelivered_finalize_does_not_rebuild_twice(monkeypatch, tasks_mod):
    """acks_late redelivers this message, and kombu restores a dead consumer's
    unacked ones when the next worker connects. Repeating the pass produces
    byte-identical views at the cost of another six minutes."""
    db = pytest.importorskip("db")
    jobs = sys.modules["jobs"]
    monkeypatch.setattr(jobs, "get_job", lambda jid: {
        "id": jid, "status": "done", "finished_at": "2026-08-20T09:00:00+03:30"})

    def boom():
        raise AssertionError("rebuilt the views for a job that already finished")

    monkeypatch.setattr(db, "refresh_analytics", boom)

    out = tasks_mod.finalize_update(5)

    assert out["duplicate"] is True
    assert tasks_mod._finished == [], "an already-closed job must not be re-closed"


def test_reconciles_re_enqueue_of_a_stuck_tail_still_runs(monkeypatch, tasks_mod):
    """'finalizing' is not terminal, so the guard above must not block the one
    re-enqueue that exists to rescue a job whose worker died mid-rebuild."""
    db = pytest.importorskip("db")
    jobs = sys.modules["jobs"]
    monkeypatch.setattr(jobs, "rows_written", lambda jid: 5)
    called = []
    monkeypatch.setattr(db, "refresh_analytics", lambda: called.append(True) or {})
    monkeypatch.setattr(db, "clear_cache", lambda: None)

    tasks_mod.finalize_update(5)
    assert called == [True]


def test_a_stopped_run_that_wrote_nothing_skips_the_rebuild(monkeypatch, tasks_mod):
    db = pytest.importorskip("db")
    jobs = sys.modules["jobs"]
    monkeypatch.setattr(jobs, "rows_written", lambda jid: 0)

    def boom():
        raise AssertionError("rebuilt 20 materialized views over unchanged data")

    monkeypatch.setattr(db, "refresh_analytics", boom)

    out = tasks_mod.finalize_update(5)

    assert out["skipped_refresh"] is True
    assert tasks_mod._finished == [5], "the job must still be closed out"


def test_a_run_that_wrote_rows_does_rebuild(monkeypatch, tasks_mod):
    db = pytest.importorskip("db")
    jobs = sys.modules["jobs"]
    monkeypatch.setattr(jobs, "rows_written", lambda jid: 1)
    called = []
    monkeypatch.setattr(db, "refresh_analytics", lambda: called.append(True) or {})
    monkeypatch.setattr(db, "clear_cache", lambda: None)

    out = tasks_mod.finalize_update(5)

    assert called == [True]
    assert "skipped_refresh" not in out
    assert tasks_mod._finished == [5]


def test_an_unknown_row_count_still_rebuilds(monkeypatch, tasks_mod):
    """If the count itself fails, do the safe thing rather than the fast one."""
    db = pytest.importorskip("db")
    jobs = sys.modules["jobs"]

    def broken(jid):
        raise RuntimeError("database gone")

    monkeypatch.setattr(jobs, "rows_written", broken)
    called = []
    monkeypatch.setattr(db, "refresh_analytics", lambda: called.append(True) or {})
    monkeypatch.setattr(db, "clear_cache", lambda: None)

    tasks_mod.finalize_update(5)
    assert called == [True]


# ---------------------------------------------------------------------------
# reconcile — a job stuck in 'finalizing' has to be recoverable too
# ---------------------------------------------------------------------------
@pytest.fixture
def reconcile_env(monkeypatch):
    tasks = pytest.importorskip("tasks")
    jobs = sys.modules["jobs"]
    sent = []
    monkeypatch.setattr(tasks.finalize_update, "delay", lambda jid: sent.append(jid))
    monkeypatch.setattr(tasks, "_rearm", lambda *a, **k: True)
    monkeypatch.setattr(jobs, "active_job_id", lambda: 3)
    monkeypatch.setattr(jobs, "get_job", lambda jid: {"id": 3, "status": "finalizing",
                                                     "kind": "stock"})
    return tasks, jobs, sent


def test_a_job_stuck_finalizing_is_re_enqueued(monkeypatch, reconcile_env):
    """A worker killed during the rebuild leaves the job in 'finalizing', which
    claim_finalize() will not re-enter — so without this the only thing that
    would ever finish it is the broker's one-hour visibility timeout, with the
    page showing a running job and no new update possible the whole time."""
    tasks, jobs, sent = reconcile_env
    monkeypatch.setattr(jobs, "finalize_stalled", lambda jid, secs: True)

    out = tasks.reconcile(3)

    assert out["finalizing"] is True
    assert sent == [3]


def test_a_healthy_finalize_is_left_alone(monkeypatch, reconcile_env):
    """The rebuild reports no symbol activity for minutes at a time; treating
    that as stalled would start a second one beside the first."""
    tasks, jobs, sent = reconcile_env
    monkeypatch.setattr(jobs, "finalize_stalled", lambda jid, secs: False)

    tasks.reconcile(3)

    assert sent == []


def test_is_stalled_cannot_answer_for_a_finalizing_job(monkeypatch):
    """Why the two tests above stub finalize_stalled and not is_stalled. This is
    the bug they used to hide: is_stalled() returns False for any job with no
    outstanding symbols, and 'finalizing' means every symbol is terminal — so
    the recovery it guarded was unreachable, and jobs really did sit in
    'finalizing' for ever, blocking every later update."""
    jobs = sys.modules["jobs"]
    monkeypatch.setattr(jobs, "summary_counts", lambda jid: {
        "total": 8, "ok": 8, "failed": 0, "skipped": 0, "running": 0,
        "pending": 0})
    monkeypatch.setattr(jobs, "last_activity", lambda jid: 100_000.0)

    assert jobs.is_stalled(3, 900) is False, (
        "if this ever returns True, is_stalled has changed and the comment in "
        "tasks.reconcile can be simplified")
    assert jobs.finalize_stalled(3, 900) is True


def test_the_finalize_threshold_exceeds_a_real_rebuild(monkeypatch, reconcile_env):
    tasks, _jobs, _sent = reconcile_env
    # Measured on this database: 351s, and later 600s for a stopped etf run that
    # had written 13 symbols. The threshold has to clear the slow one with room
    # for a loaded machine, or reconcile starts a second rebuild beside a
    # healthy first.
    assert tasks.FINALIZE_STALE_AFTER > 2 * 600
    assert tasks.FINALIZE_STALE_AFTER > tasks.STALE_AFTER


# ---------------------------------------------------------------------------
# dev_boot — one command starts everything, and knows when not to
# ---------------------------------------------------------------------------
@pytest.fixture
def boot(monkeypatch):
    mod = pytest.importorskip("dev_boot")
    for name in ("BN_AUTOSTART_WORKER", "BN_OPEN_BROWSER", "BN_WORKER_POOL",
                 "WERKZEUG_RUN_MAIN"):
        monkeypatch.delenv(name, raising=False)
    # Nothing in this file may actually spawn a process.
    monkeypatch.setattr(mod.subprocess, "Popen", _must_not_run)
    return mod


def test_worker_autostart_can_be_turned_off(monkeypatch, boot):
    monkeypatch.setenv("BN_AUTOSTART_WORKER", "0")
    assert boot.ensure_worker() == "disabled"


def test_no_worker_is_started_without_a_broker(monkeypatch, boot):
    # A worker with no broker just loops on reconnect errors for ever.
    monkeypatch.setattr(boot.redis_boot, "ping", lambda *a, **k: False)
    assert boot.ensure_worker() == "no-broker"


def test_a_running_worker_is_not_duplicated(monkeypatch, boot):
    monkeypatch.setattr(boot.redis_boot, "ping", lambda *a, **k: True)
    monkeypatch.setattr(boot, "worker_running", lambda *a, **k: True)
    assert boot.ensure_worker() == "already-running"


def test_a_live_pid_file_means_a_worker(monkeypatch, boot):
    # This process is certainly alive. The pid file has to be enough on its own:
    # a busy --pool=solo worker cannot answer a control ping.
    monkeypatch.setattr(boot, "_recorded_worker_pid", lambda *a: os.getpid())
    assert boot.worker_running() is True


def test_a_dead_pid_file_does_not_count_as_a_worker(monkeypatch, boot):
    monkeypatch.setattr(boot, "_recorded_worker_pid", lambda *a: 999_999_999)
    monkeypatch.setattr(boot, "_pid_alive", lambda pid: False)
    assert boot.worker_running() is False


def test_no_pid_file_at_all_means_no_worker(monkeypatch, boot):
    monkeypatch.setattr(boot, "_recorded_worker_pid", lambda *a: 0)
    assert boot.worker_running() is False


def test_a_stale_broker_entry_is_not_taken_for_a_worker(monkeypatch, boot):
    """The regression: Redis restores the `unacked` hash of a killed worker from
    dump.rdb, market._worker_has_claimed_work() reads it as "a worker exists",
    and `python app.py` then started none at all — leaving every data update
    queued for ever, which is the failure this module exists to prevent."""
    monkeypatch.setattr(boot, "_recorded_worker_pid", lambda *a: 0)
    market = pytest.importorskip("market")
    monkeypatch.setattr(market, "_worker_has_claimed_work", lambda: True)

    assert boot.worker_running() is False, (
        "a leftover unacked entry must not stand in for a live worker here")


def test_the_pid_file_is_read_from_disk(tmp_path, monkeypatch, boot):
    """End of the loop start_local.ps1 and dev_boot share: one writes it, the
    other reads it, and a mismatch means two workers on the same queue."""
    pid_file = tmp_path / "celery-worker.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(boot, "WORKER_PID_FILE", str(pid_file))
    assert boot._recorded_worker_pid() == os.getpid()
    assert boot.worker_running() is True

    pid_file.write_text("not-a-pid")
    assert boot._recorded_worker_pid() == 0


def test_windows_gets_the_solo_pool(monkeypatch, boot):
    monkeypatch.setattr(boot.os, "name", "nt")
    assert "--pool=solo" in boot._worker_argv()


def test_the_pool_can_be_overridden(monkeypatch, boot):
    monkeypatch.setenv("BN_WORKER_POOL", "threads")
    assert "--pool=threads" in boot._worker_argv()


def test_the_browser_is_not_opened_twice_by_the_reloader(monkeypatch, boot):
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "true")
    assert boot.open_browser_when_ready("127.0.0.1", 5002) is False


def test_the_browser_can_be_turned_off(monkeypatch, boot):
    monkeypatch.setenv("BN_OPEN_BROWSER", "0")
    assert boot.open_browser_when_ready("127.0.0.1", 5002) is False


def _must_not_run(*args, **kwargs):
    raise AssertionError("must not be called")
