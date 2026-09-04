"""
dev_boot.py — راه‌اندازی کامل محلی با یک فرمان
Bring the whole local stack up from `python app.py`.

«به‌روزرسانی داده‌ها» needs four processes, not one — Redis, two Celery workers
and the web app — and until now only start_local.ps1 knew that. Typing `python
app.py`, which is what the README and every habit says, produced an app that
served every read screen perfectly and could not update anything, with symptoms
that pointed nowhere near the cause: the update page spinning on «در حال
محاسبه…» because the job row was created and no worker existed to claim it.

So this module makes the ordinary command do the whole job:

    Redis        — redis_boot.ensure_running(); broker, result backend and the
                   shared analytics cache.
    fetch        — the worker that actually fetches from TSETMC, on
                   --queues=updates. Without it a job is queued and stays
                   queued for ever.
    maintenance  — --queues=maintenance: the analytics rebuild, the finalize
                   step and the reconciler. Separate from the fetch worker
                   because --pool=solo runs one task at a time and the rebuild
                   takes minutes; see the queue comment in celery_app.py.
    browser      — opened once the port is accepting, so the app is on screen
                   rather than a URL to copy.

Boundaries, same as redis_boot: only from the `__main__` path of app.py, never
under Gunicorn or Compose where these are declared services; never raises, so a
machine that cannot start a worker still gets a working read-only app; and every
process it starts is detached, so Ctrl+C stops the app and leaves the background
services (and any update running on them) alone. `.\\start_local.ps1 -Stop`
stops them.

Environment:
    BN_AUTOSTART_WORKER=0     do not start any Celery worker
    BN_OPEN_BROWSER=0         do not open a browser
    BN_WORKER_POOL            celery pool override (default: solo on Windows)
    CELERY_FETCH_QUEUE        queue the fetch worker consumes (default: updates)
    CELERY_MAINTENANCE_QUEUE  queue the maintenance worker consumes
"""
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import redis_boot

log = logging.getLogger("boursenegar.devboot")

HERE = redis_boot.HERE
TOOLS_DIR = redis_boot.TOOLS_DIR

# TSETMC is a domestic host, and this machine's HTTP_PROXY points at a tunnel
# client for reaching sites abroad. Sending the fetch through it makes every
# symbol fail with a ProxyError; direct, the same request answers in seconds.
# start_local.ps1 sets this too — it is repeated here because the fetch happens
# in the worker, and this is the code that starts the worker.
NO_PROXY = ("tsetmc.com,.tsetmc.com,old.tsetmc.com,cdn.tsetmc.com,"
            "www.tsetmc.com,127.0.0.1,localhost")


def _flag(name, default=True):
    return redis_boot._flag(name, default)


# ---------------------------------------------------------------------------
# Celery worker
# ---------------------------------------------------------------------------
# Two workers, because one is not enough on a machine limited to --pool=solo:
# the analytics rebuild takes minutes and would hold the only slot there is,
# starving the fetches, the stop, and the reconciler that recovers from both.
# See the queue comment in celery_app.py.
#
#   fetch        --queues=updates       one symbol at a time, the slow useful work
#   maintenance  --queues=maintenance   finalize / analytics rebuild / reconcile
#
# The fetch worker keeps the original pid-file name, because start_local.ps1
# -Stop and every existing note about .tools/celery-worker.pid refer to it.
FETCH, MAINTENANCE = "fetch", "maintenance"
WORKER_ROLES = (FETCH, MAINTENANCE)

WORKER_PID_FILE = os.path.join(TOOLS_DIR, "celery-worker.pid")
MAINTENANCE_PID_FILE = os.path.join(TOOLS_DIR, "celery-maintenance.pid")


def _pid_file(role=FETCH):
    # Read through the module globals rather than a dict built at import time,
    # so a test (or a caller) that repoints WORKER_PID_FILE is honoured.
    return WORKER_PID_FILE if role == FETCH else MAINTENANCE_PID_FILE


def _log_base(role=FETCH):
    return "celery-worker" if role == FETCH else "celery-maintenance"


def _queues(role=FETCH):
    if role == FETCH:
        return os.environ.get("CELERY_FETCH_QUEUE", "updates")
    return os.environ.get("CELERY_MAINTENANCE_QUEUE", "maintenance")


def _pid_alive(pid):
    """Is that process id still running? No psutil dependency."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE, WAIT_OBJECT_0 = 0x00100000, 0
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            # A signalled handle means the process has exited.
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) != WAIT_OBJECT_0
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _recorded_worker_pid(role=FETCH):
    try:
        with open(_pid_file(role)) as fh:
            return int((fh.read() or "0").strip() or 0)
    except (OSError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# A WORKER RUNNING YESTERDAY'S CODE
#
# The web process and the Celery workers are separate long-running processes.
# `python app.py` restarts the web half every time; the workers are started once
# and then simply keep running, so after a change to tasks.py / tse_fetch.py the
# worker is still executing the module it imported days ago.
#
# That is not a subtle problem. Adding the «حقیقی/حقوقی صندوق‌ها» data type and
# restarting only the web app produced this, measured:
#
#     پردازش‌شده ۱ / ۲۹۳    موفق ۰    ناموفق ۱    زمان سپری‌شده ۹۰۹ث
#
# Every ticker died on `KeyError('etf_ri')` inside a worker whose
# tse_fetch.KINDS predated the release, and because acks_late redelivers the
# batch it was claimed again and again — six attempts per symbol, twelve rows
# stuck in 'running', fifteen minutes, and nothing on the page to explain it.
# tasks.fetch_batch now refuses an unknown kind with a message naming the fix;
# this makes the fix unnecessary in the normal case, which is better.
#
# The test is deliberately coarse — "is any tracked source file newer than the
# process?" — because the cost of being wrong is asymmetric: a needless restart
# costs three seconds, a missed one costs the run.
# ---------------------------------------------------------------------------
#: The modules a worker actually executes. Templates and CSS are irrelevant to
#: it, and including them would restart the worker every time a page is edited.
_WORKER_SOURCES = ("tasks.py", "tse_fetch.py", "celery_app.py", "jobs.py",
                   "market_data.py", "db.py", "market.py", "filter_engine.py",
                   "backtest.py", "cache.py", "analytics_views.py")


def _newest_source_mtime():
    newest = 0.0
    for name in _WORKER_SOURCES:
        try:
            newest = max(newest, os.path.getmtime(os.path.join(HERE, name)))
        except OSError:
            continue                     # a module this install does not have
    return newest


def _worker_start_time(role):
    """When the running worker process started, as a POSIX timestamp, or None.

    psutil is not a dependency of this project, so this asks the OS directly and
    returns None on anything unexpected — an unknown start time means "do not
    restart", which is the safe answer."""
    pid = _recorded_worker_pid(role)
    if not pid:
        return None
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -ErrorAction Stop).StartTime.ToUniversalTime()"
                 ".Subtract([datetime]'1970-01-01').TotalSeconds"],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                                 capture_output=True, text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                import datetime
                started = datetime.datetime.strptime(
                    out.stdout.strip(), "%a %b %d %H:%M:%S %Y")
                return started.timestamp()
            return None
        if out.returncode != 0:
            return None
        return float(out.stdout.strip().replace(",", "."))
    except Exception:                    # noqa: BLE001 — diagnostics, never fatal
        return None


def _worker_is_stale(role):
    """True when the worker started before the newest change to its own code.

    A job in flight is left alone: restarting mid-batch would abandon symbols
    that are already claimed, and the operator can restart it themselves once it
    finishes. The stale worker will still refuse unknown kinds loudly in the
    meantime (tasks.fetch_batch)."""
    if not _flag("BN_RESTART_STALE_WORKER", True):
        return False
    started = _worker_start_time(role)
    if started is None:
        return False
    if started >= _newest_source_mtime():
        return False
    try:
        import jobs
        if jobs.active_job_id():
            log.warning("the %s worker is stale but a job is in flight — "
                        "not restarting it now", role)
            return False
    except Exception:                    # noqa: BLE001 — no DB yet, or no tables
        pass
    return True


def _stop_worker(role):
    """Ask the tracked worker to exit. True when it is gone afterwards."""
    pid = _recorded_worker_pid(role)
    if not pid:
        return False
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:                    # noqa: BLE001
        return False
    for _ in range(20):
        if not _pid_alive(pid):
            return True
        time.sleep(0.25)
    return not _pid_alive(pid)


def worker_running(role=FETCH):
    """True if a worker started by this module or by start_local.ps1 is alive.

    A pid file, and only a pid file. The two obvious alternatives were both
    tried and both are worse here:

      * A Celery control ping is authoritative when it replies, but the FIRST
        one in a process costs ~9 seconds of broker/mailbox setup (measured on
        this machine) — paid on every `python app.py`, to answer a question a
        one-line file answers instantly. Worse, it cannot answer it correctly
        anyway: --pool=solo, the only pool Windows supports, runs tasks on the
        very thread that replies to broadcasts, so a worker in the middle of a
        six-minute analytics rebuild is indistinguishable from no worker.

      * market._worker_has_claimed_work() reads the broker's `unacked` hash,
        which keeps the entries of a worker that was KILLED — and Redis restores
        them from dump.rdb across a restart. It reported "a worker exists" on a
        machine with none, and this function then skipped starting one, leaving
        every data update queued for ever. It is the right signal in
        refresh_analytics_async(), where a false negative merely costs a
        duplicate refresh; here a false positive costs the user the feature.

    The cost of this choice: a worker started by hand, by neither supported
    path, is not seen and a second one is started beside it. That is untidy
    rather than harmful — both consume the same queue and Celery distributes
    between them."""
    pid = _recorded_worker_pid(role)
    return bool(pid and _pid_alive(pid))


def _worker_argv(role=FETCH):
    pool = os.environ.get("BN_WORKER_POOL", "").strip()
    if not pool:
        # Not a preference: Celery's default prefork pool needs fork(), which
        # Windows does not have, and a worker started without --pool=solo
        # accepts tasks and then fails them. It costs concurrency — one symbol
        # at a time — which is why a full market rebuild belongs on the Compose
        # stack, where the worker runs prefork with concurrency 4.
        #
        # It costs one more thing, and this one is silent: celery_app.py sets
        # task_time_limit / task_soft_time_limit, and BOTH ARE INERT UNDER SOLO.
        # Time limits are implemented by the prefork pool killing the child that
        # runs the task; solo runs it inline in the consumer thread and has no
        # child to kill. A task that blocks therefore blocks the whole worker for
        # ever, and because the consumer is the same thread, the queue stops
        # being drained at the same instant — which is what a stalled TSETMC
        # socket did once for 73 minutes while /update reported «بازیابی خودکار
        # در جریان است» at a worker that could no longer receive anything.
        # tse_fetch._install_http_timeout() is the protection that actually
        # applies here; ensure_worker() logs the gap below.
        pool = "solo" if os.name == "nt" else "prefork"
    # -n gives the two workers distinct node names. Without it both are
    # celery@HOSTNAME, and Celery treats a duplicate node name as the same
    # worker: the second one shares the first one's control mailbox, so
    # `celery inspect active_queues` reports one set of queues and a broadcast
    # meant for one of them can be answered by the other.
    return [sys.executable, "-m", "celery", "-A", "celery_app", "worker",
            "--loglevel=info", f"--queues={_queues(role)}", f"--pool={pool}",
            "-n", role + "@%h"]


def ensure_worker(role=FETCH, timeout=None):
    """Start a Celery worker for `role` if none is consuming its queue — or if
    the one that is, is running code older than what is on disk.

    Returns "already-running", "started", "disabled", "no-broker" or "failed".
    """
    if not _flag("BN_AUTOSTART_WORKER", True):
        return "disabled"

    host, port, _pw = redis_boot.endpoint()
    if not redis_boot.ping(host, port):
        # A worker with no broker would sit in a reconnect loop printing errors
        # for ever. redis_boot has already tried and reported why.
        log.warning("not starting a celery worker — no broker is reachable")
        return "no-broker"

    if worker_running(role):
        # …unless it is running code older than what is on disk. See
        # _worker_is_stale() for why that is worth a restart rather than a
        # shrug.
        if _worker_is_stale(role):
            log.warning("the %s celery worker is older than the code — "
                        "restarting it", role)
            if _stop_worker(role):
                time.sleep(1.0)
            else:
                return "already-running"       # could not stop it; leave it be
        else:
            return "already-running"

    os.makedirs(TOOLS_DIR, exist_ok=True)
    env = dict(os.environ)
    env["NO_PROXY"] = env["no_proxy"] = NO_PROXY
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    kwargs = {
        "cwd": HERE,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # Same flags, and for the same reason, as redis_boot._launch(): a
        # DETACHED_PROCESS parent owns no console, so any console program it
        # starts in turn is given a new one *with a visible window*. Celery is
        # a process that starts other processes. CREATE_NO_WINDOW gives the
        # worker a windowless console for its children to inherit, and
        # CREATE_NEW_PROCESS_GROUP keeps Ctrl+C on the app from reaching it.
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    else:
        kwargs["start_new_session"] = True

    try:
        # Append rather than truncate: the log of the run that just failed is
        # usually the thing you want to read.
        out = open(os.path.join(TOOLS_DIR, _log_base(role) + ".log"), "ab")
        err = open(os.path.join(TOOLS_DIR, _log_base(role) + ".err.log"), "ab")
        try:
            proc = subprocess.Popen(_worker_argv(role), stdout=out, stderr=err,
                                    **kwargs)
        finally:
            out.close()
            err.close()
    except OSError:
        log.warning("could not start the %s celery worker", role, exc_info=True)
        return "failed"

    # Don't wait for it to be ready — a worker takes several seconds to connect
    # and the app has no reason to block on it. Just make sure it did not die on
    # the spot, which is what a bad import or a missing dependency looks like.
    time.sleep(0.5)
    if proc.poll() is not None:
        log.warning("the %s celery worker exited immediately (code %s) — see "
                    ".tools/%s.err.log", role, proc.returncode, _log_base(role))
        return "failed"
    try:
        with open(_pid_file(role), "w") as fh:
            fh.write(str(proc.pid))
    except OSError:
        pass                 # the ping is still there as a second opinion
    if "--pool=solo" in _worker_argv(role):
        log.warning(
            "celery worker uses the solo pool — task_time_limit does NOT apply; "
            "a blocking call would wedge this worker until it is restarted. The "
            "fetch path is protected by TSE_HTTP_TIMEOUT (tse_fetch.py) instead.",
            extra={"role": role, "tse_http_timeout":
                   os.environ.get("TSE_HTTP_TIMEOUT", "10,45")})
    log.info("celery worker starting",
             extra={"role": role, "queues": _queues(role),
                    "worker_pid": proc.pid,
                    "log": ".tools/" + _log_base(role) + ".log"})
    return "started"


def ensure_workers():
    """{role: state} for every worker this machine is supposed to run.

    market.ensure_local_worker() calls this before queueing a job, so a worker
    that died mid-session is replaced instead of leaving the next update queued
    at nobody at all."""
    return {role: ensure_worker(role) for role in WORKER_ROLES}


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
def port_already_serving(host, port, timeout=0.4):
    """True when something is ALREADY listening on this host and port.

    `python app.py` calls this before binding, and refuses to start if it is
    true. On Linux a second bind would simply fail with EADDRINUSE — but
    Werkzeug's dev server sets SO_REUSEADDR, and on Windows that flag means
    something different: it lets a second process bind a port another process
    is already listening on. Both stay bound, and connections are handed to one
    of them with no rule you can rely on.

    That matters here because each process caches its own Jinja templates. Two
    instances of different vintages on one port means template edits appear on
    some requests and not others, while CSS and JS edits appear every time —
    asset_version() re-reads those from disk per request in any process. It is
    the least debuggable symptom this app can produce; three copies of it were
    once found listening on 5002, started hours apart.

    A plain TCP connect is the right test, unlike the Redis liveness check in
    redis_boot which has to speak the protocol: here anything that accepts on
    the port will take the browser's request, whatever it is.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(timeout)
    try:
        return probe.connect_ex((host, port)) == 0
    except OSError:
        # An unresolvable host cannot be occupied by anything we could collide
        # with — let the real bind produce the real error.
        return False
    finally:
        probe.close()


def open_browser_when_ready(host, port, timeout=20.0):
    """Open the app in a browser once the port actually accepts connections.

    On a background thread and gated on a real connect, because opening the URL
    the instant before Werkzeug binds gives the user a browser error page for
    their trouble."""
    if not _flag("BN_OPEN_BROWSER", True):
        return False
    # The reloader runs the script twice; only the child serves, and both would
    # otherwise open a tab.
    if os.environ.get("WERKZEUG_RUN_MAIN"):
        return False

    target = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{target}:{port}/"

    def run():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((target, port), timeout=0.5):
                    pass
            except OSError:
                time.sleep(0.2)
                continue
            try:
                import webbrowser
                webbrowser.open(url)
            except Exception:
                log.warning("could not open a browser", exc_info=True)
            return
        log.warning("the app did not start listening within %.0fs — not opening "
                    "a browser", timeout)

    threading.Thread(target=run, name="open-browser", daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# The whole stack
# ---------------------------------------------------------------------------
def start_services():
    """Redis, then the workers. Called before the app touches either."""
    status = {"redis": redis_boot.ensure_running()}
    status.update(ensure_workers())
    log.info("local services ready", extra=status)
    return status
