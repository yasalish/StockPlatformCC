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
    """Start a Celery worker for `role` if none is consuming its queue.

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
