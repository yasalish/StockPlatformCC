"""tests/test_stale_after_delete.py — deleting rows must not leave stale numbers

Pure tests: no database, no Redis, no broker. Everything that would talk to one
is replaced with a stand-in, because what is being asserted is the *decision*
each function makes, and that is where this went wrong.

The bug these lock down: days were deleted from `stockpricehistory` /
`etfpricehistory` through /update, and «آخرین تاریخ سهام در پایگاه داده» went on
showing the deleted date. `db_summary()` is cached in Redis for six hours and
only `clear_cache()` invalidates it, and each of the three paths below could
skip that INCR while reporting success:

  1. `delete_price_history()` deleted the rows and left invalidation entirely to
     an asynchronous refresh.
  2. `refresh_analytics_async()` treated a successful `.delay()` as "it will be
     refreshed", when it only means the broker accepted the message — with no
     worker consuming the queue, nothing ever ran it.
  3. `bump_version()` threw the invalidation away when Redis was unreachable, so
     entries written before the outage came back to life when it returned.
"""
import sys
import types

import pytest

import cache


# ---------------------------------------------------------------------------
# 3. An invalidation issued while Redis is down must not be lost
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self):
        self.value = 7

    def incr(self, key):
        self.value += 1
        return self.value


@pytest.fixture
def degraded(monkeypatch):
    monkeypatch.setattr(cache, "_bump_owed", False, raising=False)
    monkeypatch.setattr(cache, "_client_obj", None, raising=False)
    monkeypatch.setattr(cache, "_warned", False, raising=False)
    monkeypatch.setattr(cache, "_client", lambda: None)
    return monkeypatch


def test_a_bump_with_no_redis_is_remembered(degraded):
    assert cache.bump_version() == 0          # nothing shared to bump
    assert cache._bump_owed is True


def test_the_owed_bump_is_applied_when_redis_returns(degraded):
    cache.bump_version()
    fake = FakeRedis()
    degraded.setattr(cache, "_client_obj", fake)

    cache._up()                                # what the reconnect probe calls

    assert fake.value == 8, "the version key was never INCRed on recovery"
    assert cache._bump_owed is False


def test_a_failed_recovery_incr_stays_owed(degraded):
    cache.bump_version()

    class Broken(FakeRedis):
        def incr(self, key):
            raise OSError("connection reset")

    degraded.setattr(cache, "_client_obj", Broken())
    cache._up()
    assert cache._bump_owed is True, "a failed INCR must be retried, not dropped"


def test_a_successful_bump_owes_nothing(degraded):
    fake = FakeRedis()
    degraded.setattr(cache, "_client", lambda: fake)
    assert cache.bump_version() == 8
    assert cache._bump_owed is False


# ---------------------------------------------------------------------------
# 2. A queued task with nobody listening is not a refresh
# ---------------------------------------------------------------------------
def _fake_tasks(ping_result, raises=None):
    """A stand-in for `tasks`, so this never imports Celery or touches a broker."""
    control = types.SimpleNamespace()

    def ping(timeout=1.0):
        if raises:
            raise raises
        return ping_result

    control.ping = ping
    task = types.SimpleNamespace(app=types.SimpleNamespace(control=control),
                                 delay=lambda *a, **k: None)
    return types.SimpleNamespace(refresh_analytics_only=task)


@pytest.fixture
def market_mod(monkeypatch):
    m = pytest.importorskip("market")
    # The broker fallback is exercised by its own tests below; neutralise it here
    # so these assert the ping branch and never touch a live Redis.
    monkeypatch.setattr(m, "_worker_has_claimed_work", lambda: False)
    return m


def test_no_worker_answering_means_not_listening(monkeypatch, market_mod):
    monkeypatch.setitem(sys.modules, "tasks", _fake_tasks(None))
    assert market_mod._worker_listening(timeout=0.01) is False


def test_empty_ping_reply_means_not_listening(monkeypatch, market_mod):
    # Celery answers [] when the broker is reachable but no worker is consuming
    # — the exact state of a laptop running only `python app.py`.
    monkeypatch.setitem(sys.modules, "tasks", _fake_tasks([]))
    assert market_mod._worker_listening(timeout=0.01) is False


def test_a_silent_but_busy_solo_worker_still_counts_as_present(monkeypatch, market_mod):
    """--pool=solo cannot answer a ping mid-task, and these refreshes take
    minutes. Concluding "no worker" there would start a duplicate rebuild in the
    web process every time a delete landed during one."""
    monkeypatch.setitem(sys.modules, "tasks", _fake_tasks([]))
    monkeypatch.setattr(market_mod, "_worker_has_claimed_work", lambda: True)
    assert market_mod._worker_listening(timeout=0.01) is True


def test_a_replying_worker_means_listening(monkeypatch, market_mod):
    monkeypatch.setitem(sys.modules, "tasks",
                        _fake_tasks([{"celery@host": {"ok": "pong"}}]))
    assert market_mod._worker_listening(timeout=0.01) is True


def test_an_unreachable_broker_means_not_listening(monkeypatch, market_mod):
    monkeypatch.setitem(sys.modules, "tasks",
                        _fake_tasks(None, raises=OSError("no broker")))
    assert market_mod._worker_listening(timeout=0.01) is False


def test_refresh_falls_back_in_process_when_nobody_is_listening(monkeypatch, market_mod):
    """The regression itself: with no worker, the refresh must happen HERE."""
    monkeypatch.setitem(sys.modules, "tasks", _fake_tasks([]))
    monkeypatch.setattr(market_mod, "_worker_listening", lambda *a, **k: False)

    published = []
    monkeypatch.setattr(sys.modules["tasks"].refresh_analytics_only, "delay",
                        lambda *a, **k: published.append(a))

    started = []
    monkeypatch.setattr(market_mod.threading, "Thread",
                        lambda target=None, name=None, daemon=None:
                            types.SimpleNamespace(start=lambda: started.append(name)))

    assert market_mod.refresh_analytics_async("rows deleted") is True
    assert published == [], "must not queue work nothing will consume"
    assert started == ["refresh-analytics"]


# ---------------------------------------------------------------------------
# 1. The delete itself must invalidate, without waiting for the refresh
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, rowcount):
        self.rowcount = rowcount

    def execute(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, rowcount):
        self._rowcount = rowcount
        self.committed = False

    def cursor(self, *a, **k):
        return FakeCursor(self._rowcount)

    def commit(self):
        self.committed = True


@pytest.fixture
def db_mod(monkeypatch):
    db = pytest.importorskip("db")
    monkeypatch.setattr(db, "release", lambda conn: None)
    monkeypatch.setattr(db, "_date_for", lambda kind, d, bound: "2026-08-15")
    return db


def test_deleting_rows_invalidates_the_cache_immediately(monkeypatch, db_mod):
    monkeypatch.setattr(db_mod, "get_db", lambda: FakeConn(rowcount=42))
    cleared = []
    monkeypatch.setattr(db_mod, "clear_cache", lambda: cleared.append(True))

    n = db_mod.delete_price_history("stock", start="1405-05-25", end="1405-05-27")

    assert n == 42
    assert cleared == [True], (
        "db_summary() caches stock_latest/etf_latest for six hours; without this "
        "INCR the update page keeps showing the date that was just deleted")


def test_a_delete_that_matched_nothing_does_not_churn_the_version(monkeypatch, db_mod):
    monkeypatch.setattr(db_mod, "get_db", lambda: FakeConn(rowcount=0))
    cleared = []
    monkeypatch.setattr(db_mod, "clear_cache", lambda: cleared.append(True))

    assert db_mod.delete_price_history("stock", start="1405-01-01", end="1405-01-02") == 0
    assert cleared == [], "nothing changed, so nothing needs invalidating"
