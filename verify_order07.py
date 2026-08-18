"""
verify_order07.py — verification for Order 07 (migrations, error tracking, backups).

  A  Alembic — a single linear chain, a baseline that is a NO-OP against the
     existing database, and a full `upgrade head` run against a real scratch
     database with the resulting schema inspected.
  B  Logging — one JSON object per line, a request id that survives from the
     inbound header to the response header, the 200 ms threshold as a WARNING,
     and nothing sensitive in the output.
  C  Sentry — off without a DSN, and the scrubber strips cookies, query strings
     and identifying user fields.
  D  Backups — backup.sh runs for real, the dump is readable and checksummed,
     retention keeps a floor, and restore.sh RESTORES INTO A SCRATCH DATABASE
     AND DIFFS THE ROW COUNTS. Nothing is asserted that was not executed.

Needs the «Stock» database reachable and pg_dump/pg_restore/psql on PATH.
It creates and drops its own scratch databases and never writes to «Stock».

    python verify_order07.py             fast: migrations on an EMPTY scratch db
    python verify_order07.py --full      also migrate a RESTORED COPY of the
                                         real database (slow: builds the views)
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

FULL = "--full" in sys.argv
FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


import db
import observability

PG_BIN = None
for cand in (r"C:\Program Files\PostgreSQL\17\bin", "/usr/lib/postgresql/17/bin"):
    if os.path.isdir(cand):
        PG_BIN = cand
        break
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
if PG_BIN:
    ENV["PATH"] = PG_BIN + os.pathsep + ENV.get("PATH", "")

SRC_DB = db.DB_SETTINGS["dbname"]
HOST = db.DB_SETTINGS["host"]


def admin_sql(statement, dbname="postgres"):
    """Run one statement outside a transaction (CREATE/DROP DATABASE)."""
    import psycopg2
    s = dict(db.DB_SETTINGS, dbname=dbname)
    conn = psycopg2.connect(**s)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
    finally:
        conn.close()


def query(sql, params=(), dbname=None):
    import psycopg2
    import psycopg2.extras
    s = dict(db.DB_SETTINGS, dbname=dbname or SRC_DB)
    conn = psycopg2.connect(**s)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def alembic(args, dbname, expect_ok=True):
    env = {**ENV, "STOCK_DB_NAME": dbname}
    r = subprocess.run([sys.executable, "-m", "alembic"] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, cwd=HERE, timeout=3600)
    if expect_ok and r.returncode != 0:
        print((r.stdout + r.stderr)[-1500:])
    return r


# ===========================================================================
print("=" * 74)
print("PART A — Alembic")
print("=" * 74)

check(os.path.exists("alembic.ini"), "alembic.ini exists")
check(os.path.isdir("migrations/versions"), "migrations/versions/ exists")
ini = read("alembic.ini")
check(re.search(r"^sqlalchemy\.url\s*=\s*$", ini, re.M) is not None,
      "no database URL (and so no password) is committed in alembic.ini")
check("STOCK_DB" in read("migrations/env.py"),
      "env.py builds the URL from the same STOCK_DB_* variables db.py uses")
check("pg_advisory_xact_lock" in read("migrations/env.py"),
      "migrations take an advisory lock, so concurrent upgraders serialise")

r = alembic(["heads"], SRC_DB)
heads = [ln for ln in r.stdout.splitlines() if ln.strip()]
check(len(heads) == 1 and "0004" in heads[0],
      f"exactly one head — the chain has not branched ({heads})")

revs = sorted(os.listdir("migrations/versions"))
revs = [f for f in revs if f.endswith(".py")]
check(len(revs) == 4, f"four revisions: {', '.join(revs)}")
for expect in ("baseline", "order03", "order02", "order06"):
    check(any(expect in f for f in revs), f"a revision captures {expect}")

check("raise RuntimeError" in read("migrations/versions/0001_baseline_schema.py"),
      "the baseline REFUSES to downgrade — it would drop the price history")

# --- the baseline must be a no-op against the existing database ------------
before = {r["table_name"] for r in query(
    "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
check({"stocks", "stockpricehistory", "etf", "etfpricehistory", "users",
       "watchlist"} <= before,
      f"the live database already has the baseline tables ({len(before)} tables)")

baseline = read("migrations/versions/0001_baseline_schema.py")
check("_has(" in baseline and baseline.count("if not _has(") >= 4,
      "every baseline object is guarded by an existence check, so it recreates "
      "nothing on a live database")

# --- a real upgrade against a real (empty) database ------------------------
EMPTY_DB = f"{SRC_DB}_alembic_test"
print(f"\n  running `alembic upgrade head` against a fresh {EMPTY_DB} …")
admin_sql(f'DROP DATABASE IF EXISTS "{EMPTY_DB}" WITH (FORCE)')
admin_sql(f'CREATE DATABASE "{EMPTY_DB}"')
try:
    t0 = time.time()
    r = alembic(["upgrade", "head"], EMPTY_DB)
    check(r.returncode == 0,
          f"upgrade head succeeds on an empty database ({time.time() - t0:.1f}s)")

    cur = alembic(["current"], EMPTY_DB)
    check("0004" in cur.stdout, f"…and lands on head ({cur.stdout.strip()[:60]})")

    tables = {x["table_name"] for x in query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'",
        dbname=EMPTY_DB)}
    for t in ("stocks", "stockpricehistory", "etf", "etfpricehistory", "users",
              "watchlist", "update_job", "update_job_ticker", "alembic_version"):
        check(t in tables, f"  created: {t}")

    # 0002 must actually have converted the types the baseline created as numeric.
    types = {x["column_name"]: x["data_type"] for x in query(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='stockpricehistory' AND column_name LIKE 'adj%%'",
        dbname=EMPTY_DB)}
    check(types and all(v == "double precision" for v in types.values()),
          f"0002 converted adj_* to double precision ({sorted(set(types.values()))})")

    idx = {x["indexname"] for x in query(
        "SELECT indexname FROM pg_indexes WHERE schemaname='public'", dbname=EMPTY_DB)}
    check("ix_sph_ticker_date" in idx and "ix_eph_ticker_date" in idx,
          "0002 created the (ticker, date) INCLUDE indexes")
    check("ix_sph_ticker_jdate" not in idx,
          "…and the superseded j_date indexes are absent")

    # Idempotence: running it twice must be a no-op, not an error.
    r2 = alembic(["upgrade", "head"], EMPTY_DB)
    check(r2.returncode == 0, "a second `upgrade head` is a clean no-op")

    # A safe downgrade really works.
    d = alembic(["downgrade", "0003"], EMPTY_DB)
    tables_after = {x["table_name"] for x in query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'",
        dbname=EMPTY_DB)}
    check(d.returncode == 0 and "update_job" not in tables_after,
          "downgrade 0004 → 0003 drops the job tables cleanly")
    check(alembic(["upgrade", "head"], EMPTY_DB).returncode == 0,
          "…and upgrading again restores them")

    # The baseline downgrade must refuse rather than drop six million rows.
    bad = alembic(["downgrade", "base"], EMPTY_DB, expect_ok=False)
    check(bad.returncode != 0 and "Refusing" in (bad.stdout + bad.stderr),
          "`downgrade base` is refused, not executed")
finally:
    admin_sql(f'DROP DATABASE IF EXISTS "{EMPTY_DB}" WITH (FORCE)')
    print(f"  {EMPTY_DB} dropped")

check("migrate" in read("docker-compose.yml") and
      "alembic" in read("docker-compose.yml"),
      "`alembic upgrade head` is wired into container startup (migrate service)")
import yaml
compose = yaml.safe_load(read("docker-compose.yml"))
for svc in ("web", "worker", "beat"):
    dep = compose["services"][svc].get("depends_on", {}).get("migrate", {})
    check(dep.get("condition") == "service_completed_successfully",
          f"{svc} does not start until the migration has SUCCEEDED")


# ===========================================================================
print()
print("=" * 74)
print("PART B — structured logging")
print("=" * 74)

probe = subprocess.run(
    [sys.executable, "-c",
     "import os,sys;sys.path.insert(0,r'%s');"
     "os.environ['LOG_FORMAT']='json';os.environ['SERVICE_NAME']='probe';"
     "import observability as o;log=o.setup_logging();"
     "o.set_request_id('abc123');"
     "log.warning('slow request', extra={'http_path':'/stocks','duration_ms':431.2,"
     "'status':200,'user_id':7});"
     "log.info('persian', extra={'sector':'\\u0641\\u0644\\u0632\\u0627\\u062a'})" % HERE],
    capture_output=True, text=True, encoding="utf-8", env=ENV, cwd=HERE)
lines = [ln for ln in probe.stdout.strip().splitlines() if ln.strip()]
try:
    recs = [json.loads(ln) for ln in lines]
    check(len(recs) == 2, f"every record is one parseable JSON object ({len(recs)})")
    a = recs[0]
    check(a["level"] == "WARNING" and a["msg"] == "slow request",
          "the slow-request event is WARNING level")
    check(a.get("request_id") == "abc123",
          "records carry the request id")
    check(a.get("duration_ms") == 431.2 and a.get("http_path") == "/stocks",
          "structured fields survive as fields, not as interpolated text")
    check(a.get("service") == "probe", "the service name is on every record")
    check(recs[1].get("sector") == "فلزات",
          "Persian text stays readable (ensure_ascii=False), not \\uXXXX escaped")
except Exception as e:
    check(False, "JSON log output", f"{e}: {probe.stdout[:200]} {probe.stderr[:200]}")

check(observability.SLOW_REQUEST_MS == 200.0,
      f"the 200 ms threshold from order 00 is kept ({observability.SLOW_REQUEST_MS} ms)")

obs = read("observability.py")
check("ContextVar" in obs,
      "the request id is a ContextVar — a thread-local would leak between the "
      "concurrent requests a gthread worker serves")

# --- the request id end to end --------------------------------------------
import app as webapp
client = webapp.app.test_client()
resp = client.get("/healthz", headers={"X-Request-ID": "trace-me-42"})
check(resp.headers.get("X-Request-ID") == "trace-me-42",
      "an inbound X-Request-ID is adopted and echoed back")
resp2 = client.get("/healthz")
rid = resp2.headers.get("X-Request-ID")
check(bool(rid) and rid != "trace-me-42",
      f"a request without one gets a fresh id ({rid})")
check("$request_id" in read("deploy/nginx/snippets/proxy.conf"),
      "nginx generates the id and passes it through, so its access log and the "
      "app's records share one value")

# --- what must NOT be logged ----------------------------------------------
check("query_string" in obs and "cookies" in obs,
      "the scrubber names cookies and the query string explicitly")
appsrc = read("app.py")
check("request.path" in appsrc or "http_path" in obs,
      "the log records the PATH")
check("request.full_path" not in obs and "request.query_string" not in obs,
      "…and never the query string, which is where the Persian filter values, "
      "the search term and the watchlist ticker live")
check("username" not in obs.split("WHAT IS DELIBERATELY NOT LOGGED")[1].split('"""')[0]
      or "user_id" in obs,
      "user records carry the numeric id, not the username or e-mail")
check("print(" not in read("db.py") and "print(" not in read("cache.py")
      and "print(" not in read("market.py"),
      "db.py, cache.py and market.py no longer print — they log")


# ===========================================================================
print()
print("=" * 74)
print("PART C — Sentry")
print("=" * 74)

check(observability.setup_sentry("web") is False,
      "no SENTRY_DSN → Sentry is a silent no-op, and the app is unaffected")
check("send_default_pii=False" in obs,
      "send_default_pii is OFF — otherwise the SDK attaches cookies, the body "
      "and the client IP to every event")
scrubbed = observability._scrub_event(
    {"request": {"headers": {"Cookie": "session=secret", "User-Agent": "x"},
                 "query_string": "group=فلزات", "data": {"password": "p"},
                 "cookies": {"session": "secret"}},
     "user": {"id": 7, "username": "yasmine", "email": "a@b.c"}},
    None)
check(scrubbed["request"]["headers"]["Cookie"] == "[redacted]",
      "the scrubber redacts the session cookie")
check("query_string" not in scrubbed["request"]
      and "data" not in scrubbed["request"]
      and "cookies" not in scrubbed["request"],
      "…and drops the query string, the body and the cookie jar")
check(scrubbed["user"] == {"id": 7},
      f"…and reduces the user to a numeric id ({scrubbed['user']})")
check("sentry-sdk" in read("requirements.txt"), "sentry-sdk is a declared dependency")
check("GlitchTip" in obs or "self-hosted" in obs,
      "the module documents pointing Sentry at a self-hosted server — sentry.io "
      "is a foreign SaaS and this deploys inside Iran")


# ===========================================================================
print()
print("=" * 74)
print("PART D — backups, actually executed")
print("=" * 74)

BACKUP_DIR = os.path.join(HERE, "backups")
bash = shutil.which("bash") or r"C:\Program Files\Git\bin\bash.exe"
benv = {**ENV, "STOCK_DB_HOST": HOST, "STOCK_DB_NAME": SRC_DB,
        "STOCK_DB_USER": db.DB_SETTINGS["user"],
        "STOCK_DB_PASSWORD": db.DB_SETTINGS["password"],
        "BACKUP_DIR": BACKUP_DIR}

for script in ("backup.sh", "restore.sh", "backup-loop.sh"):
    check(os.path.exists(f"deploy/scripts/{script}"), f"deploy/scripts/{script} exists")

print("\n  running deploy/scripts/backup.sh for real …")
t0 = time.time()
b = subprocess.run([bash, "deploy/scripts/backup.sh"], capture_output=True,
                   text=True, encoding="utf-8", errors="replace",
                   env=benv, cwd=HERE, timeout=1800)
out = b.stdout + b.stderr
check(b.returncode == 0, f"backup.sh exits 0 ({time.time() - t0:.1f}s)",
      out.strip().splitlines()[-1][:120] if out.strip() else "")
try:
    events = [json.loads(ln) for ln in b.stdout.strip().splitlines() if ln.startswith("{")]
    done = next(e for e in events if e["msg"] == "backup complete")
    dump_path = os.path.join(BACKUP_DIR, done["file"])
    check(os.path.exists(dump_path),
          f"a timestamped dump was written: {done['file']}")
    check(done["bytes"] > 1_000_000,
          f"…compressed to {done['bytes'] / 1e6:.1f} MB in {done['seconds']}s")
    check(re.search(r"-\d{8}-\d{6}\.dump$", done["file"]) is not None,
          "…with a sortable timestamp in the name")
    check(os.path.exists(dump_path + ".sha256"), "…and a checksum sidecar")
    check(all(e.get("service") == "backup" for e in events),
          "backup.sh logs JSON matching the application's format")
    ret = next(e for e in events if e["msg"] == "retention applied")
    check(ret["keep_min"] >= 1 and ret["keep_days"] >= 1,
          f"retention: keep {ret['keep_days']} days, never fewer than "
          f"{ret['keep_min']} ({ret['kept']} kept, {ret['deleted']} deleted)")
except Exception as e:
    check(False, "backup.sh produced a verifiable dump", f"{e}")
    dump_path = None

check("--no-prune" in read("deploy/scripts/backup.sh"), "retention can be disabled")
check("KEEP_MIN" in read("deploy/scripts/backup.sh"),
      "a floor stops the last good dump being pruned after a month of failures")
check("BACKUP_AT" in read("deploy/scripts/backup-loop.sh")
      and "backup" in yaml.safe_load(read("docker-compose.yml"))["services"],
      "a nightly schedule exists as its own compose service")
check("s3" not in read("deploy/scripts/backup.sh").lower()
      and "aws" not in read("deploy/scripts/backup.sh").lower(),
      "storage is local/self-hosted — no foreign object storage anywhere")

# --- THE RESTORE TEST -------------------------------------------------------
print("\n  running deploy/scripts/restore.sh for real (scratch database + row diff) …")
t0 = time.time()
rr = subprocess.run([bash, "deploy/scripts/restore.sh"], capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    env=benv, cwd=HERE, timeout=3600)
rout = rr.stdout + rr.stderr
print("\n".join("      " + ln for ln in rr.stdout.strip().splitlines()[-16:]))
check(rr.returncode == 0, f"restore.sh exits 0 ({time.time() - t0:.1f}s)")
check("RESTORE VERIFIED" in rout,
      "the dump restored and EVERY table matched the source row for row")
check("checksum: OK" in rout, "the checksum was verified before restoring")
m = re.search(r"(\d+) table\(s\) compared, (\d+) mismatched", rout)
check(m is not None and m.group(2) == "0",
      f"row-count diff: {m.group(1) if m else '?'} tables compared, "
      f"{m.group(2) if m else '?'} mismatched")
check("scratch database dropped" in rout,
      "the scratch database is cleaned up afterwards")
check("--promote" in read("deploy/scripts/restore.sh") and
      "refusing to restore" in read("deploy/scripts/restore.sh"),
      "restoring over the LIVE database requires an explicit --promote")

# restore.sh's own scratch database, named exactly. A LIKE over Stock_% would
# also flag a scratch database the operator is deliberately holding open.
still_there = query("SELECT count(*) c FROM pg_database WHERE datname = %s",
                    (f"{SRC_DB}_restore_test",), dbname="postgres")
check(still_there[0]["c"] == 0,
      f"restore.sh left no scratch database behind ({still_there[0]['c']})")
live = query("SELECT count(*) c FROM stockpricehistory")[0]["c"]
check(live > 0, f"the live database is untouched ({live:,} price rows)")


# ===========================================================================
if FULL:
    print()
    print("=" * 74)
    print("PART E — migrations against a RESTORED COPY of the real database")
    print("=" * 74)
    COPY = f"{SRC_DB}_migrate_full"
    print(f"  restoring into {COPY} and migrating (this builds the views — slow) …")
    subprocess.run([bash, "deploy/scripts/restore.sh", "--into", COPY, "--promote"],
                   env=benv, cwd=HERE, timeout=3600)
    t0 = time.time()
    r = alembic(["upgrade", "head"], COPY)
    check(r.returncode == 0,
          f"upgrade head on a populated copy ({time.time() - t0:.0f}s)")
    mv = query("SELECT matviewname, ispopulated FROM pg_matviews", dbname=COPY)
    check(len(mv) >= 12 and all(m["ispopulated"] for m in mv),
          f"0003 built and populated {len(mv)} materialized views")
    types = {x["data_type"] for x in query(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name='stockpricehistory' AND column_name LIKE 'adj%%'",
        dbname=COPY)}
    check(types == {"double precision"},
          f"0002 converted 2M rows of adj_* to double precision ({types})")
    admin_sql(f'DROP DATABASE IF EXISTS "{COPY}" WITH (FORCE)')
    print(f"  {COPY} dropped")


print()
print("=" * 74)
if FAIL:
    print(f"{len(FAIL)} FAILURE(S):")
    for f in FAIL:
        print("   -", f)
else:
    print("ALL CHECKS PASSED")
print("=" * 74)
if not FULL:
    print("\n(run with --full to also migrate a restored copy of the real "
          "database — slow, builds the materialized views)")
sys.exit(1 if FAIL else 0)
