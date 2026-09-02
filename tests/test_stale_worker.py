"""tests/test_stale_worker.py — a worker running old code must SAY so

The web process and the Celery workers are separate long-running processes. Add
a data type, restart the web app, and the workers are still holding the module
they imported yesterday — so the web side happily enqueues `etf_ri` batches at a
worker whose `tse_fetch.KINDS` has never heard of it.

What that looked like before this guard, measured on a real run:

    وضعیت به‌روزرسانی
    حقیقی/حقوقی صندوق‌ها · ۱۴۰۵-۰۵-۰۱ تا ۱۴۰۵-۰۶-۰۹
    در حال دریافت: ابتکار
    پردازش‌شده  ۱ / ۲۹۳      موفق ۰      ناموفق ۱      زمان سپری‌شده ۹۰۹ث

Fifteen minutes, one symbol touched, and nothing on the page saying why. Under
the hood every ticker was dying on `KeyError('etf_ri')` deep inside the fetch;
because `acks_late` redelivers the batch, each one was claimed again and again
and climbed to six attempts while `update_job_ticker` filled with rows stuck in
'running'. The failed list stayed empty, because a KeyError is not a FetchError
and never reached `mark_failed`.

The fix is not a retry — no amount of waiting teaches a running process a new
module. It is to fail the batch ONCE, mark every ticker with a message naming
the actual remedy, and let the page show it.

Pure: `jobs` is stubbed, so this needs no database and no broker.
"""
import pytest


@pytest.fixture
def batch(monkeypatch):
    """Run tasks.fetch_batch against a stubbed jobs module.

    Returns (call, state) where `state` records everything the task did to the
    job — which is the only thing worth asserting here."""
    tasks = pytest.importorskip("tasks")
    state = {"started": 0, "claimed": [], "failed": [], "ok": [],
             "heartbeats": 0, "finalized": 0}

    monkeypatch.setattr(tasks.jobs, "mark_started",
                        lambda jid: state.__setitem__("started", state["started"] + 1))
    monkeypatch.setattr(tasks.jobs, "claim_ticker",
                        lambda jid, t: (state["claimed"].append(t),
                                        {"entity_id": 1, "attempts": 1})[1])
    monkeypatch.setattr(tasks.jobs, "mark_failed",
                        lambda jid, t, reason, err, **kw:
                            state["failed"].append((t, reason, err)))
    monkeypatch.setattr(tasks.jobs, "mark_ok",
                        lambda jid, t, n: state["ok"].append(t))
    monkeypatch.setattr(tasks.jobs, "heartbeat",
                        lambda jid: state.__setitem__("heartbeats",
                                                      state["heartbeats"] + 1))
    monkeypatch.setattr(tasks.jobs, "control_flags", lambda jid: (False, False))
    monkeypatch.setattr(tasks, "_maybe_finalize",
                        lambda jid: state.__setitem__("finalized",
                                                      state["finalized"] + 1))
    monkeypatch.setattr(tasks, "_rearm", lambda jid, after: None)

    def call(kind, tickers=("الف", "ب", "ج")):
        # .run() rather than .delay(): the task's body is what is under test,
        # not Celery's dispatch.
        return tasks.fetch_batch.run(7, kind, list(tickers), "1405-05-01",
                                     "1405-06-09", False)

    return call, state


def test_an_unknown_kind_fails_the_batch_once_instead_of_raising(batch):
    """The regression. A KeyError here is redelivered for ever; a returned
    result is acknowledged and the batch stops."""
    call, state = batch
    out = call("a_kind_this_worker_has_never_heard_of")
    assert out["stale_worker"] is True
    assert out["failed"] == 3 and out["ok"] == 0


def test_every_ticker_is_marked_failed_rather_than_left_running(batch):
    """Left as 'running', a ticker is re-claimed by the next redelivery and its
    attempt count climbs while nothing ever completes — which is exactly what
    filled the table with twelve stuck rows."""
    call, state = batch
    call("nonsense_kind")
    assert [t for t, _r, _e in state["failed"]] == ["الف", "ب", "ج"]


def test_the_message_names_the_actual_fix(batch):
    """«خطا در دریافت» would be true and useless. The operator needs to be told
    that the WORKER is stale, because restarting it is the whole remedy."""
    call, state = batch
    call("nonsense_kind")
    _ticker, reason, err = state["failed"][0]
    assert "کارگر" in reason
    assert "نوع داده" in err and "راه‌اندازی" in err
    assert "nonsense_kind" in err, "the message must name the kind it refused"


def test_the_job_is_still_finalized_so_the_page_stops_waiting(batch):
    """Without this the run sits at «در حال اجرا» for ever: every batch has
    returned, but nothing closed the job."""
    call, state = batch
    call("nonsense_kind")
    assert state["finalized"] == 1
    assert state["heartbeats"] >= 1


def test_a_known_kind_is_untouched_by_the_guard(batch, monkeypatch):
    """The guard must not stand between a healthy worker and its work."""
    tasks = pytest.importorskip("tasks")
    call, state = batch
    monkeypatch.setattr(tasks, "_fetch_one_with_retries",
                        lambda kind, eid, t, s, e, f: 42)
    out = call("stock")
    assert out["ok"] == 3 and out["failed"] == 0
    assert state["ok"] == ["الف", "ب", "ج"]
    assert "stale_worker" not in out


# ---------------------------------------------------------------------------
# dev_boot — noticing the stale worker before it eats a run
# ---------------------------------------------------------------------------
def test_an_unknown_start_time_never_triggers_a_restart(monkeypatch):
    """`_worker_start_time` returns None on anything it does not understand, and
    "I don't know when it started" must mean "leave it alone". Restarting a
    healthy worker on a guess is worse than the problem."""
    dev_boot = pytest.importorskip("dev_boot")
    monkeypatch.setattr(dev_boot, "_worker_start_time", lambda role: None)
    assert dev_boot._worker_is_stale("fetch") is False


def test_a_worker_older_than_its_code_is_stale(monkeypatch):
    dev_boot = pytest.importorskip("dev_boot")
    monkeypatch.setattr(dev_boot, "_newest_source_mtime", lambda: 2_000.0)
    monkeypatch.setattr(dev_boot, "_worker_start_time", lambda role: 1_000.0)
    monkeypatch.setattr(dev_boot, "_flag", lambda name, default=True: True)
    # No job in flight — jobs.active_job_id is reached through a local import,
    # so it is patched on the module the function imports.
    jobs = pytest.importorskip("jobs")
    monkeypatch.setattr(jobs, "active_job_id", lambda: None)
    assert dev_boot._worker_is_stale("fetch") is True


def test_a_worker_newer_than_its_code_is_not(monkeypatch):
    dev_boot = pytest.importorskip("dev_boot")
    monkeypatch.setattr(dev_boot, "_newest_source_mtime", lambda: 1_000.0)
    monkeypatch.setattr(dev_boot, "_worker_start_time", lambda role: 2_000.0)
    assert dev_boot._worker_is_stale("fetch") is False


def test_a_job_in_flight_defers_the_restart(monkeypatch):
    """Restarting mid-batch abandons symbols that are already claimed. The
    stale worker still refuses unknown kinds loudly in the meantime, so nothing
    is silently lost by waiting."""
    dev_boot = pytest.importorskip("dev_boot")
    jobs = pytest.importorskip("jobs")
    monkeypatch.setattr(dev_boot, "_newest_source_mtime", lambda: 2_000.0)
    monkeypatch.setattr(dev_boot, "_worker_start_time", lambda role: 1_000.0)
    monkeypatch.setattr(dev_boot, "_flag", lambda name, default=True: True)
    monkeypatch.setattr(jobs, "active_job_id", lambda: 42)
    assert dev_boot._worker_is_stale("fetch") is False


def test_the_restart_can_be_switched_off(monkeypatch):
    dev_boot = pytest.importorskip("dev_boot")
    monkeypatch.setattr(dev_boot, "_flag",
                        lambda name, default=True:
                            False if name == "BN_RESTART_STALE_WORKER" else default)
    monkeypatch.setattr(dev_boot, "_newest_source_mtime", lambda: 2_000.0)
    monkeypatch.setattr(dev_boot, "_worker_start_time", lambda role: 1_000.0)
    assert dev_boot._worker_is_stale("fetch") is False


def test_the_watched_sources_are_what_a_worker_actually_runs():
    """Templates and CSS must NOT be in the list: a worker restarted every time
    a page is edited would interrupt fetches for changes it cannot even see."""
    dev_boot = pytest.importorskip("dev_boot")
    assert "tasks.py" in dev_boot._WORKER_SOURCES
    assert "tse_fetch.py" in dev_boot._WORKER_SOURCES
    assert not any(f.endswith((".html", ".css", ".js"))
                   for f in dev_boot._WORKER_SOURCES)


def test_every_kind_the_web_side_can_enqueue_is_known_to_the_fetch_layer():
    """The static half of the same problem: a kind offered on the update form
    but missing from tse_fetch.KINDS would fail this way on a CURRENT worker
    too, which no restart would fix."""
    market = pytest.importorskip("market")
    tse_fetch = pytest.importorskip("tse_fetch")
    unknown = [k for k in market.RUNNABLE_KINDS if k not in tse_fetch.KINDS]
    assert not unknown, f"offered but unfetchable: {unknown}"


# ---------------------------------------------------------------------------
# A snapshot dataset is never "already fetched"
# ---------------------------------------------------------------------------
def test_a_snapshot_job_never_carries_resume_marks(monkeypatch):
    """«دیده‌بان» and «فهرست نمادها» have no real window — update_run() fills in
    yesterday's date purely so the job row has bounds. Two snapshots therefore
    share that filler, and already_done_elsewhere() skipped the second one as a
    duplicate: «رد‌شده ۱», no photograph taken. Running a snapshot again giving
    a different answer IS the point of it."""
    market = pytest.importorskip("market")
    jobs = pytest.importorskip("jobs")
    seen = {}

    monkeypatch.setattr(market, "ensure_local_worker", lambda: None)
    monkeypatch.setattr(jobs, "ensure_tables", lambda: None)
    monkeypatch.setattr(jobs, "active_job_id", lambda: None)
    monkeypatch.setattr(jobs, "blocking_job_id", lambda: None)
    monkeypatch.setattr(
        jobs, "create_job",
        lambda kind, start, end, **kw: (seen.update(kw, kind=kind), 1)[1])
    tasks = pytest.importorskip("tasks")
    monkeypatch.setattr(market, "_dispatch", lambda *a, **k: None, raising=False)
    import types
    monkeypatch.setattr(tasks, "fetch_batch",
                        types.SimpleNamespace(apply_async=lambda *a, **k: None,
                                              delay=lambda *a, **k: None))
    monkeypatch.setattr(jobs, "pending_tickers", lambda jid: [])

    market.start_job("watch", "1405-06-09", "1405-06-09", resume=True)
    assert seen.get("resume") is False, "a snapshot must not resume"

    seen.clear()
    market.start_job("stock_ri", "1405-06-01", "1405-06-09", resume=True)
    assert seen.get("resume") is True, "a dated fetch still resumes"


def test_every_dated_flag_matches_the_fetch_layer():
    """DATASET_DATED drives both the form and the resume decision above, so a
    dataset that takes a date range must be marked as taking one."""
    market = pytest.importorskip("market")
    for kind, dated in market.DATASET_DATED.items():
        if kind in ("watch", "symbols", "shareholders"):
            assert dated is False, f"{kind} has no meaningful date range"
        else:
            assert dated is True, f"{kind} fetches a window"
