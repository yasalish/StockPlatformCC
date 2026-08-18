"""
gunicorn.conf.py — production WSGI configuration for «بورس‌نگار».

Replaces app.run(debug=True, port=5002): the Werkzeug development server, single
process, with the interactive debugger (a remote-code-execution hole) exposed.

Loaded automatically by `gunicorn -c gunicorn.conf.py app:app`.
Every value can be overridden with an environment variable so the same image
runs on a 4-vCPU VPS and a 16-vCPU one without a rebuild.
"""
import multiprocessing
import os


def _int(name, default):
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")

# ---------------------------------------------------------------------------
# Worker class: gthread
#
# NOT `sync`: a sync worker serves exactly one request at a time, and this
# application spends most of each request blocked on PostgreSQL — waiting on a
# socket, holding a whole process idle. Serving 16 concurrent readers would need
# 16 processes, each with its own Python heap and its own connection pool.
#
# NOT `gevent`: greenlets only help if the blocking calls yield, and psycopg2 is
# a C extension whose libpq socket does not — it would need psycopg2's wait
# callback wired through psycogreen, and even then market.py spawns subprocesses
# and refresh_analytics_async() runs a real thread, both of which sit awkwardly
# with a monkey-patched world. The cost is real complexity for no gain here.
#
# gthread gives OS threads that release the GIL for the entire duration of a
# libpq call, so N threads genuinely overlap N database waits, while multiple
# processes still give real parallelism for the part that IS CPU-bound: Jinja
# rendering ~780 rows of Persian markup into a 2.5 MB page.
# ---------------------------------------------------------------------------
worker_class = "gthread"

_cpus = multiprocessing.cpu_count()

# ---------------------------------------------------------------------------
# Worker and thread counts — sized for the stated 4–8 vCPU box.
#
# The usual (2 × CPU + 1) rule is for sync workers, where you need surplus
# processes to cover blocking. With gthread the threads cover the blocking, so
# processes only need to match the CPU actually available for rendering — and
# on this box the app does NOT get the whole machine: PostgreSQL is a container
# alongside it, with 4 GB of shared_buffers and up to 4 parallel workers per
# gather (order 01). Oversubscribing would just make the two fight.
#
#   workers = CPUs − 1, floor 2, ceiling 4
#     → 4 vCPU: 3 workers   |   8 vCPU: 4 workers
#     One core is left to PostgreSQL. The ceiling of 4 exists because each
#     worker holds its own psycopg2 pool and its own ~150 MB Python heap; past
#     four the database connections, not the CPU, become the limit.
#
#   threads = 4 per worker
#     → 12–16 concurrent requests. Each in-flight request holds at most one
#     pooled connection, so peak connections = workers × STOCK_DB_POOL_MAX.
#     With the compose defaults that is 4 × 8 = 32, comfortably inside
#     PostgreSQL's max_connections of 100 with room for psql, backups and the
#     Celery workers order 06 adds.
#
# The analytics cache is in Redis since order 04, so extra workers no longer
# each keep a private copy — which is what made running more than one safe.
# ---------------------------------------------------------------------------
workers = _int("GUNICORN_WORKERS", max(2, min(4, _cpus - 1)))
threads = _int("GUNICORN_THREADS", 4)

# ---------------------------------------------------------------------------
# preload_app MUST stay False.
#
# Preloading imports app.py once in the master and forks the workers from it,
# which normally saves memory. Here it would be a correctness bug: importing
# app.py opens PostgreSQL connections (db.init_db(), db.ensure_indexes()) and a
# Redis socket. fork() duplicates those file descriptors into every child, so
# several workers would share one libpq connection and interleave bytes on it —
# producing corrupted result sets and "message contents do not agree with
# length" errors that only appear under concurrency.
# ---------------------------------------------------------------------------
preload_app = False

# A cold analytics scan against a database whose materialized views are missing
# takes ~14 s (order 02 measured it), so the default 30 s timeout would kill the
# worker mid-request on a cold start. 120 s is generous but still bounded; the
# minutes-long data update runs in a subprocess and returns immediately.
timeout = _int("GUNICORN_TIMEOUT", 120)
graceful_timeout = _int("GUNICORN_GRACEFUL_TIMEOUT", 30)

# nginx keeps upstream connections alive (keepalive 32); this must exceed the
# time between reuses or nginx races a socket gunicorn is closing and logs 502s.
keepalive = _int("GUNICORN_KEEPALIVE", 15)

# Recycle workers periodically. Nothing here is known to leak, but a long-lived
# process holding Persian string caches and psycopg2 buffers drifts upward; the
# jitter stops all workers recycling in lockstep and causing a latency spike.
max_requests = _int("GUNICORN_MAX_REQUESTS", 1000)
max_requests_jitter = _int("GUNICORN_MAX_REQUESTS_JITTER", 100)

# Gunicorn touches a heartbeat file every second per worker. On a host where
# /tmp is disk-backed (or on an overlay filesystem) that can stall long enough
# for the master to declare a healthy worker dead and restart it. /dev/shm is
# tmpfs and always present in a Linux container.
worker_tmp_dir = "/dev/shm"

# Bound the request line and headers — the app has no legitimate use for large
# ones, and nginx already caps them, but defence in depth costs nothing.
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# ---------------------------------------------------------------------------
# Logging — to stdout/stderr so `docker compose logs` is the single place to
# look. Order 07 replaces this with structured JSON and a request id.
# ---------------------------------------------------------------------------
accesslog = os.environ.get("GUNICORN_ACCESSLOG", "-")
errorlog = os.environ.get("GUNICORN_ERRORLOG", "-")
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
# %({x-forwarded-for}i)s is the real client: nginx is the peer, so %(h)s would
# always be the proxy's address. %(L)s is the request duration in seconds, which
# pairs with the app's own [perf] warning above 200 ms.
access_log_format = ('%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" '
                     '%(s)s %(b)s "%(f)s" "%(a)s" %(L)ss')

# Trust X-Forwarded-Proto / X-Forwarded-For only from the reverse proxy. The web
# service publishes no port — nginx on the compose network is the only thing
# that can reach it — so "*" here means "the proxy", not "the internet". Set
# FORWARDED_ALLOW_IPS to nginx's address if the web port is ever published.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "*")
proxy_allow_ips = forwarded_allow_ips

proc_name = "boursenegar"


def on_starting(server):
    """One line at boot recording what was actually resolved — the numbers above
    are computed from the host's CPU count, so they must be visible in the log
    rather than inferred from the source."""
    server.log.info(
        "boursenegar: %s workers x %s threads (%s vCPU detected) = %s concurrent "
        "requests, worker_class=%s, timeout=%ss, preload=%s",
        workers, threads, _cpus, workers * threads, worker_class, timeout, preload_app)


def worker_int(worker):
    worker.log.info("worker %s interrupted", worker.pid)
