"""tests/test_redis_boot.py — the local Redis autostart

Pure tests: no database, no Flask app, no Redis, no network beyond a loopback
socket the test itself opens. Nothing here ever spawns redis-server — every case
stops at the decision `ensure_running()` makes, which is the part that can
silently go wrong.

Two of these guard properties that are easy to break by accident and expensive
to notice:

  * `test_does_not_touch_a_remote_host` — autostarting a server because a REMOTE
    Redis is down would bind a local port that then answers for a host the rest
    of the app is not talking to, turning a visible config error into a mystery.
  * `test_ping_rejects_a_socket_that_never_speaks` — this is the failure cache.py
    documents (a local VPN/proxy intercepting 127.0.0.1 accepts and then hangs).
    A liveness check written as a bare TCP connect passes there and the app would
    conclude Redis is up.
"""
import socket
import threading

import pytest

import redis_boot


@pytest.fixture
def clean_env(monkeypatch):
    """redis_boot reads the process environment, and .env is already loaded."""
    for name in ("REDIS_URL", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD",
                 "BN_AUTOSTART_REDIS", "REDIS_SERVER_EXE",
                 "BN_REDIS_BOOT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# endpoint() — must resolve exactly the way cache.py does
# ---------------------------------------------------------------------------
def test_endpoint_defaults_match_cache(clean_env):
    assert redis_boot.endpoint() == ("localhost", 6379, None)


def test_endpoint_reads_the_discrete_variables(clean_env):
    clean_env.setenv("REDIS_HOST", "127.0.0.1")
    clean_env.setenv("REDIS_PORT", "6380")
    clean_env.setenv("REDIS_PASSWORD", "s3cret")
    assert redis_boot.endpoint() == ("127.0.0.1", 6380, "s3cret")


def test_redis_url_wins_over_the_discrete_variables(clean_env):
    # cache.py gives REDIS_URL precedence; a bootstrap that ignored it would
    # start a server on the wrong port and report success.
    clean_env.setenv("REDIS_HOST", "localhost")
    clean_env.setenv("REDIS_PORT", "6379")
    clean_env.setenv("REDIS_URL", "redis://:pw@127.0.0.1:6390/2")
    assert redis_boot.endpoint() == ("127.0.0.1", 6390, "pw")


# ---------------------------------------------------------------------------
# ping() — liveness, not merely reachability
# ---------------------------------------------------------------------------
def test_ping_is_false_on_a_closed_port(clean_env):
    assert redis_boot.ping("127.0.0.1", _free_port(), timeout=0.2) is False


def test_ping_accepts_a_resp_reply():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        conn.recv(64)
        conn.sendall(b"+PONG\r\n")     # what an open Redis answers
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        assert redis_boot.ping("127.0.0.1", port, timeout=1.0) is True
    finally:
        server.close()
        t.join(timeout=2)


def test_ping_accepts_noauth_because_that_is_still_a_redis():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def serve():
        conn, _ = server.accept()
        conn.recv(64)
        conn.sendall(b"-NOAUTH Authentication required.\r\n")
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    try:
        # A requirepass'd server refuses the command but proves it is Redis, and
        # starting a second one on the same port would just fail to bind.
        assert redis_boot.ping("127.0.0.1", port, timeout=1.0) is True
    finally:
        server.close()
        t.join(timeout=2)


def test_ping_rejects_a_socket_that_never_speaks():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        # Accepts nothing and answers nothing — the intercepting-proxy case.
        assert redis_boot.ping("127.0.0.1", port, timeout=0.3) is False
    finally:
        server.close()


# ---------------------------------------------------------------------------
# ensure_running() — the decisions, with no server ever spawned
# ---------------------------------------------------------------------------
def test_opt_out_is_honoured(clean_env, monkeypatch):
    clean_env.setenv("BN_AUTOSTART_REDIS", "0")
    monkeypatch.setattr(redis_boot, "ping", _must_not_run)
    assert redis_boot.ensure_running() == "disabled"


def test_a_live_redis_is_left_alone(clean_env, monkeypatch):
    monkeypatch.setattr(redis_boot, "ping", lambda *a, **k: True)
    monkeypatch.setattr(redis_boot, "_launch", _must_not_run)
    assert redis_boot.ensure_running() == "already-running"


def test_does_not_touch_a_remote_host(clean_env, monkeypatch):
    clean_env.setenv("REDIS_HOST", "redis.internal")
    monkeypatch.setattr(redis_boot, "ping", lambda *a, **k: False)
    monkeypatch.setattr(redis_boot, "_launch", _must_not_run)
    assert redis_boot.ensure_running() == "remote"


def test_missing_binary_is_reported_not_raised(clean_env, monkeypatch):
    monkeypatch.setattr(redis_boot, "ping", lambda *a, **k: False)
    monkeypatch.setattr(redis_boot, "server_binary", lambda: None)
    monkeypatch.setattr(redis_boot, "_launch", _must_not_run)
    assert redis_boot.ensure_running() == "not-installed"


def test_a_launch_failure_never_propagates(clean_env, monkeypatch):
    # The app must start degraded rather than not at all.
    monkeypatch.setattr(redis_boot, "ping", lambda *a, **k: False)
    monkeypatch.setattr(redis_boot, "server_binary", lambda: "redis-server")
    monkeypatch.setattr(redis_boot, "_launch",
                        lambda *a: (_ for _ in ()).throw(OSError("no exec")))
    assert redis_boot.ensure_running() == "failed"


def test_a_server_that_dies_immediately_is_reported(clean_env, monkeypatch):
    class DeadProc:
        pid = 4242
        returncode = 1

        def poll(self):
            return 1

    monkeypatch.setattr(redis_boot, "ping", lambda *a, **k: False)
    monkeypatch.setattr(redis_boot, "server_binary", lambda: "redis-server")
    monkeypatch.setattr(redis_boot, "_launch", lambda *a: DeadProc())
    assert redis_boot.ensure_running(timeout=2) == "failed"


def test_explicit_exe_that_does_not_exist_is_not_used(clean_env):
    clean_env.setenv("REDIS_SERVER_EXE", "/nonexistent/redis-server")
    assert redis_boot.server_binary() is None


# ---------------------------------------------------------------------------
# app.py must not autostart anything when it is IMPORTED (Gunicorn, Celery).
# Reading the source is the only way to assert this without importing app.py,
# which needs a database.
# ---------------------------------------------------------------------------
def test_app_guards_the_autostart_behind_the_script_path():
    import os
    app_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "app.py")
    with open(app_py, encoding="utf-8") as fh:
        src = fh.read()
    call = src.index("dev_boot.start_services()")
    guard = src.rindex('if __name__ == "__main__":', 0, call)
    between = src[guard:call]
    assert "import dev_boot" in between
    # Nothing but the import may sit between the guard and the call, or the
    # guard has stopped covering it.
    assert between.count("\n") <= 3


def _must_not_run(*args, **kwargs):
    raise AssertionError("must not be called")
