"""
verify_order05.py — verification for Order 05 (Gunicorn, Nginx, Docker Compose).

Docker is not installed on this machine, so the stack cannot be brought up here.
What CAN be verified without it is checked properly rather than assumed:

  A  docker-compose.yml — parses, has the four required services, named volumes
     for PostgreSQL and Redis, a healthcheck on every service, no published
     port except nginx's, and the order 01 PostgreSQL tuning present.
  B  Secrets — no credential has a default anywhere; db.py refuses to import
     without STOCK_DB_PASSWORD and app.py refuses to start in production
     without STOCK_SECRET.
  C  gunicorn.conf.py — imports, uses gthread, sizes itself sanely on a 4- and
     8-vCPU box, and keeps preload_app off.
  D  Dockerfile — slim base, non-root user, requirements.txt as its own cached
     layer ahead of the source copy.
  E  nginx — TLS, gzip, brotli, the order 00 static cache headers, WebSocket
     readiness and request limits are all present. (`nginx -t` over the real
     files is a separate script: .tools/check_nginx_conf.py.)
  F  The app — /healthz and /readyz answer without a login and the rest of the
     site is still gated.

Run:  python verify_order05.py
"""
import io
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


# ===========================================================================
print("=" * 74)
print("PART A — docker-compose.yml")
print("=" * 74)

import yaml

raw = read("docker-compose.yml")
compose = yaml.safe_load(raw)
services = compose.get("services", {})

REQUIRED_05 = {"web", "db", "redis", "nginx"}
REQUIRED_06 = {"worker", "beat"}          # the fetch moved to Celery
REQUIRED_07 = {"migrate", "backup"}       # migrations at startup, nightly dumps
check(REQUIRED_05 <= set(services),
      f"the four order-05 services are present: {', '.join(sorted(REQUIRED_05))}")
check(set(services) == REQUIRED_05 | REQUIRED_06 | REQUIRED_07,
      f"…plus the later orders', and nothing else: {', '.join(sorted(services))}")

vols = compose.get("volumes", {})
check("pgdata" in vols and "redisdata" in vols,
      f"named volumes so data survives recreation: {', '.join(sorted(vols))}")
check(services["db"]["volumes"][0].startswith("pgdata:"),
      "PostgreSQL's data directory is on the named volume")
check(any(str(v).startswith("redisdata:") for v in services["redis"]["volumes"]),
      "Redis' data directory is on the named volume")

# "A healthcheck for each service" means each LONG-RUNNING service. `migrate` is
# a one-shot that runs `alembic upgrade head` and exits; health for it is its
# exit code, which is exactly what the other services wait on via
# service_completed_successfully. A healthcheck there would be meaningless.
ONE_SHOT = {"migrate"}
for name in sorted(services):
    if name in ONE_SHOT:
        check(services[name].get("restart") == "no",
              f"{name}: one-shot (restart: no), so its exit code IS its health")
        continue
    check("healthcheck" in services[name], f"{name}: has a healthcheck")

published = {n: s.get("ports") for n, s in services.items() if s.get("ports")}
check(list(published) == ["nginx"],
      f"only nginx publishes ports — db/redis/web are not reachable from outside "
      f"({', '.join(published) or 'none'})")

# --- the order 01 tuning must actually be in the db service ----------------
db_cmd = " ".join(str(x) for x in services["db"]["command"])
TUNING = {
    "shared_buffers": "4GB",
    "work_mem": "64MB",
    "effective_cache_size": "12GB",
    "random_page_cost": "1.1",
    "max_parallel_workers_per_gather": "4",
}
for key, want in TUNING.items():
    m = re.search(rf"{key}=\$\{{[A-Z_]+:-([^}}]+)\}}", db_cmd)
    got = m.group(1) if m else None
    check(got == want, f"order 01 tuning baked in: {key} = {got}", f"target {want}")

check("shm_size" in services["db"],
      f"db gets a larger /dev/shm ({services['db'].get('shm_size')}) — "
      f"parallel workers need it")

# --- gunicorn is what runs the app -----------------------------------------
check("gunicorn" in read("Dockerfile"), "the image runs Gunicorn, not app.run()")
check(services["web"].get("init") is True,
      "web runs an init (tini) so run_update.py subprocesses are reaped")

# --- dependency ordering ----------------------------------------------------
web_dep = services["web"].get("depends_on", {})
check(all(web_dep.get(d, {}).get("condition") == "service_healthy" for d in ("db", "redis")),
      "web waits for db AND redis to be HEALTHY, not merely started")
check(services["nginx"]["depends_on"]["web"]["condition"] == "service_healthy",
      "nginx waits for web to be healthy")


# ===========================================================================
print()
print("=" * 74)
print("PART B — secrets live in the environment, with no defaults")
print("=" * 74)

dbsrc = read("db.py")
check('"stock93"' not in dbsrc and "'stock93'" not in dbsrc,
      "db.py no longer contains the hard-coded password default")
check("_required_env(\n        \"STOCK_DB_PASSWORD\"" in dbsrc or
      '_required_env("STOCK_DB_PASSWORD"' in dbsrc.replace("\n", " ").replace("  ", " "),
      "db.py requires STOCK_DB_PASSWORD explicitly")

for var in ("STOCK_SECRET", "STOCK_DB_PASSWORD", "REDIS_PASSWORD"):
    check(f"${{{var}:?" in raw,
          f"compose fails loudly when {var} is unset (${{{var}:?...}})")

check(not os.path.exists("deploy/.env"), "deploy/.env is not committed (only .env.example)")
check("deploy/.env" in read(".gitignore") and "*.pem" in read(".gitignore"),
      "deploy/.env and TLS material are gitignored")
check(".env" in read(".dockerignore") and "deploy/.env" in read(".dockerignore"),
      "no .env is ever copied into the image (.dockerignore)")


# --- EVERY module, not just db.py -----------------------------------------
# The order says "move ALL secrets to environment variables". db.py was the one
# it named, but stock_updater.py and etf_updater.py each carried their own
# DB_SETTINGS with the same literal password AND host="localhost" — which inside
# the web container is the container itself, not the `db` service, so every data
# update would have failed with connection-refused.
for mod in ("stock_updater.py", "etf_updater.py", "market.py", "app.py",
            "cache.py", "auth.py", "tv.py", "reports.py", "run_update.py"):
    body = read(mod)
    check("stock93" not in body, f"{mod}: contains no hard-coded credential")
    check('"host": "localhost"' not in body and "'host': 'localhost'" not in body,
          f"{mod}: no hard-coded database host")

# Order 06 moved the fetch itself into tse_fetch.py, so the updaters reach the
# connection settings one level deeper — still exactly one definition.
check("import tse_fetch" in read("stock_updater.py")
      and "import tse_fetch" in read("etf_updater.py"),
      "both updaters delegate to tse_fetch (one implementation, not two copies)")
check("import db" in read("tse_fetch.py") and "DB_SETTINGS" not in read("tse_fetch.py"),
      "tse_fetch uses db.py's pooled connections — no second settings dict anywhere")

# Order 05 asserted here that market.py's stop/pause FLAG FILES followed
# APP_STATE_DIR onto a writable volume, and that both updaters read them from
# the same place. Order 06 deleted those files: the job control plane is now the
# update_job / update_job_ticker tables, which every process can see regardless
# of filesystem. The check is therefore inverted — the files must be GONE — and
# the writable volume now exists for Celery Beat's schedule database.
for gone in ("update_stop.flag", "update_pause.flag", "update_job.meta.json"):
    check(not os.path.exists(gone),
          f"{gone} no longer exists (superseded by the job tables in order 06)")
check("APP_STATE_DIR" in read("Dockerfile"),
      "the image still defines a writable state directory")
_compose_beat = services["beat"]
check(any(str(v).startswith("appstate:") for v in _compose_beat["volumes"]),
      "…and beat mounts it for celerybeat-schedule")
check("celerybeat-schedule" in " ".join(str(x) for x in [_compose_beat["command"]]),
      "beat writes its schedule onto that volume, not into the read-only code tree")

# The container DB host still has to be honoured by the fetch path.
_probe = ("import json, tse_fetch, db;"
          "print(json.dumps({'host': db.DB_SETTINGS['host']}))")
_env = {**os.environ, "STOCK_DB_HOST": "db", "STOCK_DB_PASSWORD": "x",
        "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
_r = subprocess.run([sys.executable, "-c", _probe], capture_output=True, text=True,
                    encoding="utf-8", env=_env, cwd=HERE)
try:
    import json as _json
    check(_json.loads(_r.stdout.strip().splitlines()[-1])["host"] == "db",
          "the fetch path honours STOCK_DB_HOST, so it reaches the `db` service")
except Exception as exc:
    check(False, "fetch path honours STOCK_DB_HOST", f"{exc}: {_r.stderr[:120]}")


def subproc_check(label, env_extra, code, expect_fail):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", **env_extra}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                       text=True, encoding="utf-8", env=env, cwd=HERE)
    failed = r.returncode != 0
    check(failed == expect_fail, label,
          (r.stdout + r.stderr).strip().splitlines()[-1][:110] if (r.stdout + r.stderr).strip() else "")


BLOCK_DOTENV = (
    "import builtins,os,sys\n"
    "os.environ.pop('STOCK_DB_PASSWORD',None); os.environ.pop('STOCK_SECRET',None)\n"
    "_r=builtins.__import__\n"
    "def _f(n,*a,**k):\n"
    "    if n=='dotenv': raise ImportError('blocked for this test')\n"
    "    return _r(n,*a,**k)\n"
    "builtins.__import__=_f\n"
)
subproc_check("db.py refuses to import with STOCK_DB_PASSWORD unset",
              {}, BLOCK_DOTENV + "import db", expect_fail=True)
subproc_check("app.py refuses to start in production with STOCK_SECRET unset",
              {"APP_ENV": "production", "STOCK_DB_PASSWORD": "x"},
              BLOCK_DOTENV + "os.environ['STOCK_DB_PASSWORD']='x'\nimport app", expect_fail=True)


# ===========================================================================
print()
print("=" * 74)
print("PART C — gunicorn.conf.py")
print("=" * 74)


def load_gunicorn(cpus, env_extra=None):
    """Import gunicorn.conf.py with a faked CPU count and read back what it
    resolved to — the numbers are computed, so asserting the source text would
    prove nothing."""
    code = (
        "import multiprocessing, json, os, runpy\n"
        f"multiprocessing.cpu_count = lambda: {cpus}\n"
        "g = runpy.run_path('gunicorn.conf.py')\n"
        "print(json.dumps({k: g[k] for k in "
        "('worker_class','workers','threads','preload_app','timeout','keepalive',"
        "'max_requests','worker_tmp_dir')}))\n"
    )
    env = {**os.environ, **(env_extra or {})}
    for k in ("GUNICORN_WORKERS", "GUNICORN_THREADS"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=HERE, env=env)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    import json
    return json.loads(r.stdout.strip().splitlines()[-1])


g4 = load_gunicorn(4)
g8 = load_gunicorn(8)

check(g4["worker_class"] == "gthread",
      f"worker_class is gthread — not sync, not gevent ({g4['worker_class']})")
check(g4["preload_app"] is False,
      "preload_app is off (app.py opens DB/Redis sockets at import; fork would share them)")
print(f"\n  {'box':<10} {'workers':>8} {'threads':>8} {'concurrent':>11}")
print("  " + "-" * 40)
for label, g in (("4 vCPU", g4), ("8 vCPU", g8)):
    print(f"  {label:<10} {g['workers']:>8} {g['threads']:>8} "
          f"{g['workers'] * g['threads']:>11}")
print()
check(g4["workers"] == 3 and g8["workers"] == 4,
      f"workers = CPUs-1, floored at 2 and capped at 4 ({g4['workers']} on 4 vCPU, "
      f"{g8['workers']} on 8 vCPU)")
check(g4["threads"] == 4, f"threads = {g4['threads']} per worker")

pool = 8   # STOCK_DB_POOL_MAX in docker-compose.yml
peak = g8["workers"] * pool
check(peak <= 100 * 0.6,
      f"peak DB connections {g8['workers']} workers x pool {pool} = {peak}, "
      f"well inside max_connections=100")
check(g4["timeout"] > 30,
      f"timeout raised to {g4['timeout']}s — a cold analytics scan takes ~14s")
check(g4["worker_tmp_dir"] == "/dev/shm",
      "heartbeat file on tmpfs, so a slow disk cannot fake a dead worker")
check("GUNICORN_WORKERS" in read("gunicorn.conf.py"),
      "every value is overridable by environment variable")

src = read("gunicorn.conf.py")
check("NOT `sync`" in src and "NOT `gevent`" in src,
      "the worker-class choice is explained in a comment, as the order asks")


# ===========================================================================
print()
print("=" * 74)
print("PART D — Dockerfile")
print("=" * 74)

df = read("Dockerfile")
check("python:3.12-slim" in df, "slim Python base image")
check("useradd" in df and "USER app" in df, "runs as a non-root user")
i_req = df.index("COPY requirements.txt")
i_src = df.index("COPY --chown=root:root . /app")
i_pip = df.index("pip install")
check(i_req < i_pip < i_src,
      "requirements.txt is copied and installed BEFORE the source — "
      "so editing code reuses the dependency layer")
check("${REGISTRY}" in df, "base image is registry-prefixed for a domestic mirror")
check("HEALTHCHECK" in df, "image carries its own healthcheck")

ndf = read("deploy/nginx/Dockerfile")
check("libnginx-mod-http-brotli-filter" in ndf, "nginx image installs the brotli module")
check("ngx_http_brotli_filter_module.so" in ndf,
      "…and the build FAILS if the mirror served an nginx without it")
check("nginx -t" in ndf, "the config is validated at image build time")


# ===========================================================================
print()
print("=" * 74)
print("PART E — nginx configuration")
print("=" * 74)

ngx = read("deploy/nginx/nginx.conf")
site = read("deploy/nginx/conf.d/boursenegar.conf")
tls = read("deploy/nginx/snippets/tls.conf")
proxy = read("deploy/nginx/snippets/proxy.conf")

check("ssl_certificate" in tls and "TLSv1.2 TLSv1.3" in tls,
      "TLS termination, TLS 1.2+ only")
check("return 301 https://$host$request_uri;" in site, "plain HTTP redirects to HTTPS")
check("gzip              on;" in ngx or re.search(r"^\s*gzip\s+on;", ngx, re.M),
      "gzip enabled")
check(re.search(r"^\s*brotli\s+on;", ngx, re.M) is not None, "brotli enabled")
check("brotli_static" in ngx and "gzip_static" in site,
      "pre-compressed assets are served when present")

check("alias /srv/static/" in site, "/static/ served by nginx directly")
check('max-age=31536000, immutable' in site,
      "…with the order 00 long-cache headers (1 year, immutable)")

check("map $http_upgrade $connection_upgrade" in ngx,
      "WebSocket upgrade map present")
check("proxy_set_header Connection $connection_upgrade;" in proxy,
      "…and wired into every proxied location")
check("location /ws/" in site and "proxy_buffering off;" in site,
      "a WebSocket location exists, unbuffered and long-timeout")

check("client_max_body_size" in ngx, "request body size limited")
check("limit_req_zone" in ngx and "limit_conn_zone" in ngx, "rate and connection limits declared")
check("limit_req zone=login" in site, "…with a stricter limit on the credential endpoints")
check("server_tokens off;" in ngx, "version not advertised")
check("X-Forwarded-Proto $scheme" in proxy, "forwarded headers set for ProxyFix")

check("keepalive 32;" in ngx, "upstream keepalive to Gunicorn")


# ===========================================================================
print()
print("=" * 74)
print("PART F — the application still behaves")
print("=" * 74)

import app as webapp

client = webapp.app.test_client()
for path in ("/healthz", "/readyz"):
    r = client.get(path)
    check(r.status_code in (200, 503),
          f"{path} answers without a login (HTTP {r.status_code})",
          str(r.get_json())[:90])
check(client.get("/stocks").status_code == 302,
      "every other page is still gated behind the login")

appsrc = read("app.py")
check("ProxyFix" in appsrc, "ProxyFix wired in so TLS termination is visible to Flask")
check("app.run(debug=True" not in appsrc,
      "the Werkzeug interactive debugger is no longer hard-coded on")

mk = read("market.py")
# Order 05 needed market.py to redirect its job files off the read-only code
# tree. Order 06 removed the files altogether, so the stronger property now
# holds: market.py writes nothing to disk at all.
check("open(" not in mk and "makedirs" not in mk,
      "market.py writes no files — the code tree can be mounted read-only")
check("jobs" in mk and "tasks" in mk,
      "…because its state is in PostgreSQL and its work is on the Celery queue")


# ===========================================================================
print()
print("=" * 74)
if FAIL:
    print(f"{len(FAIL)} FAILURE(S):")
    for f in FAIL:
        print("   -", f)
else:
    print("ALL CHECKS PASSED")
print("=" * 74)
print("\nNot covered here (no Docker on this machine): an actual image build and "
      "`docker compose up`.\nRun .tools/check_nginx_conf.py for a real `nginx -t` "
      "over the committed config.")
sys.exit(1 if FAIL else 0)
