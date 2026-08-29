"""
redis_boot.py — بالا آوردن خودکار Redis همراه با برنامه
Start a local Redis automatically when the app is run directly.

Redis is described everywhere in this codebase as optional, and in the strict
sense it is: cache.py degrades to an in-process fallback, logs one warning and
keeps serving. But on a developer laptop that description hides two costs that
are not optional at all:

  1. «به‌روزرسانی داده‌ها» cannot run. Redis is Celery's broker AND result
     backend (celery_app.py, logical DBs 1 and 2). With no Redis the update page
     sits on «در حال محاسبه… صبر کنید» forever, and the web process fills the
     log with `Connection to Redis lost: Retry (n/20)` from
     celery.backends.redis every few seconds — one line per poll of a task state
     that can never arrive.

  2. The fallback cache is per process and carries a much shorter TTL, so the
     expensive scans are recomputed far more often than they would be.

start_local.ps1 solves this by starting three processes in the right order, but
`python app.py` is what actually gets typed, and it started only one. This
module closes that gap: when app.py is run as a script it makes sure the
configured Redis endpoint is answering before the rest of startup touches it,
starting the bundled `.tools\\redis\\redis-server.exe` (or a `redis-server` on
PATH) if it is not.

Deliberate boundaries:

  * It only ever runs from the `__main__` path of app.py. Under Gunicorn or in
    compose the module is imported, not run, and Redis is a declared service
    with its own lifecycle — a web worker starting a datastore is a mistake
    there.
  * It refuses to act unless the endpoint is on loopback. A REDIS_HOST pointing
    at another machine is someone else's server; nothing local can fix it and
    pretending otherwise would mask a config error.
  * It never raises. Every failure path logs what went wrong and returns, so the
    app still starts degraded exactly as it did before.
  * The server it starts runs in its own process group with no console window,
    so Ctrl+C on the app leaves it — and therefore the Celery worker that
    depends on it — alive, and its BGSAVE children draw nothing on screen.
    `.\\start_local.ps1 -Stop` stops it.

Environment:
    BN_AUTOSTART_REDIS=0    turn this off entirely
    REDIS_SERVER_EXE        explicit path to redis-server, if it is elsewhere
    BN_REDIS_BOOT_TIMEOUT   seconds to wait for the new server (default 10)
"""
import logging
import os
import shutil
import socket
import subprocess
import time

try:                       # app.py loads .env before calling in here, but this
    from dotenv import load_dotenv   # module is also runnable on its own
    load_dotenv()                    # (`python redis_boot.py`), and then the
except ImportError:                  # port/password would come from nowhere.
    pass

log = logging.getLogger("boursenegar.redis")

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(HERE, ".tools")
BUNDLED_DIR = os.path.join(TOOLS_DIR, "redis")

# Addresses this machine can actually serve itself. 0.0.0.0 and :: are bind
# addresses that a locally started server would answer on, so they count too.
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}


def _flag(name, default=True):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def endpoint():
    """(host, port, password) resolved exactly the way cache.py resolves them.

    Read from the environment rather than by importing cache, so this can run
    before the first Redis client is built and cannot be affected by — or
    affect — cache.py's circuit-breaker state.
    """
    url = os.environ.get("REDIS_URL") or ""
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return (parsed.hostname or "localhost",
                int(parsed.port or 6379),
                parsed.password or None)
    return (os.environ.get("REDIS_HOST", "localhost"),
            int(os.environ.get("REDIS_PORT", "6379")),
            os.environ.get("REDIS_PASSWORD") or None)


def ping(host, port, timeout=0.5):
    """True only if a real Redis answers on host:port.

    A bare TCP connect is not enough, and cache.py explains why: a local
    VPN/proxy client that intercepts 127.0.0.1 will happily ACCEPT the
    connection and then never speak. So send an inline PING and require a RESP
    reply. `+PONG` when the server is open and `-NOAUTH …` when requirepass is
    set both prove the same thing — that Redis, not something else, is there.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(b"PING\r\n")
            reply = sock.recv(64)
    except OSError:
        return False
    return reply[:1] in (b"+", b"-")


def server_binary():
    """Path to redis-server, or None. Explicit override → bundled → PATH."""
    override = os.environ.get("REDIS_SERVER_EXE", "").strip()
    if override:
        if os.path.exists(override):
            return override
        log.warning("REDIS_SERVER_EXE points at a file that does not exist",
                    extra={"redis_exe": override})
        return None

    names = ("redis-server.exe", "redis-server") if os.name == "nt" else ("redis-server",)
    for name in names:
        bundled = os.path.join(BUNDLED_DIR, name)
        if os.path.exists(bundled):
            return bundled
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _launch(exe, port, password):
    """Start redis-server detached, on the port this app is configured for."""
    os.makedirs(TOOLS_DIR, exist_ok=True)
    # Keep dump.rdb beside the binary when it is the bundled build — that is
    # where the existing one already lives — otherwise in .tools.
    data_dir = BUNDLED_DIR if os.path.isdir(BUNDLED_DIR) else TOOLS_DIR

    argv = [exe]
    conf = os.path.join(BUNDLED_DIR, "redis.windows.conf")
    if os.name == "nt" and os.path.exists(conf):
        argv.append(conf)          # options after the file override the file
    argv += [
        "--port", str(port),
        "--dir", data_dir,
        "--logfile", os.path.join(TOOLS_DIR, "redis-server.log"),
    ]
    # Without this, a server we start would accept unauthenticated clients while
    # the app sends AUTH, and every command would fail with "Client sent AUTH,
    # but no password is set" — a harder failure than no Redis at all.
    if password:
        argv += ["--requirepass", password]

    kwargs = {
        "cwd": data_dir,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,   # --logfile above is the real log
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # CREATE_NO_WINDOW, not DETACHED_PROCESS, and the difference is visible
        # to the user. Redis has no fork() on Windows, so BGSAVE is emulated by
        # CreateProcess-ing a fresh redis-server.exe every time the save rules
        # fire (`* N changes in 300 seconds. Saving…` / `# fork operation
        # complete` in .tools/redis-server.log). That child is created with no
        # creation flags of its own, so it inherits whatever console its parent
        # has — and DETACHED_PROCESS leaves the parent with none at all, which
        # makes Windows hand the child a brand new console *with a visible
        # window*. The result was a console flashing open and shut on the
        # desktop every few minutes for the length of a background save.
        # CREATE_NO_WINDOW gives this server a console that has no window, the
        # forked children inherit that, and nothing is ever drawn.
        # CREATE_NEW_PROCESS_GROUP keeps the original promise of the detached
        # spawn: Ctrl+C in this terminal stops the app without taking Redis
        # (and the Celery worker that depends on it) down with it.
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def ensure_running(timeout=None):
    """Make sure the configured Redis answers; start one locally if it does not.

    Returns a short status string for the caller and for tests:
    "already-running", "started", "disabled", "remote", "not-installed",
    "failed" or "timeout". Never raises — a missing Redis must not be what stops
    the app from starting.
    """
    if not _flag("BN_AUTOSTART_REDIS", True):
        return "disabled"

    if timeout is None:
        timeout = float(os.environ.get("BN_REDIS_BOOT_TIMEOUT", "10"))

    try:
        host, port, password = endpoint()
    except ValueError:
        log.warning("REDIS_* settings are not a valid endpoint — skipping "
                    "autostart", exc_info=True)
        return "failed"

    if ping(host, port):
        return "already-running"

    if host not in _LOOPBACK:
        log.warning("redis at %s:%s is not answering and is not local — not "
                    "starting one here", host, port)
        return "remote"

    exe = server_binary()
    if not exe:
        log.warning("redis is not running and no redis-server was found "
                    "(looked in .tools/redis and on PATH) — the app will serve "
                    "from the in-process fallback cache and data updates cannot "
                    "run; set REDIS_SERVER_EXE or install Redis")
        return "not-installed"

    try:
        proc = _launch(exe, port, password)
    except OSError:
        log.warning("could not start redis-server", extra={"redis_exe": exe},
                    exc_info=True)
        return "failed"

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ping(host, port):
            log.info("redis started for this session",
                     extra={"redis_exe": exe, "redis_port": port,
                            "redis_pid": proc.pid})
            return "started"
        if proc.poll() is not None:
            log.warning("redis-server exited immediately (code %s) — see "
                        ".tools/redis-server.log", proc.returncode,
                        extra={"redis_exe": exe, "redis_port": port})
            return "failed"
        time.sleep(0.1)

    log.warning("redis-server was started but did not answer within %.0fs — "
                "continuing degraded; see .tools/redis-server.log", timeout,
                extra={"redis_exe": exe, "redis_port": port})
    return "timeout"


if __name__ == "__main__":       # `python redis_boot.py` — start it and report
    import observability
    observability.setup_logging()
    print(ensure_running())
