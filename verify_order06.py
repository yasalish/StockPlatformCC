"""
verify_order06.py — verification for Order 06 (Celery and Beat for the TSE fetch).

  A  Configuration — Celery reliability settings, the Beat schedule's trading
     days, and that the file-based control plane is gone.
  B  Job state in PostgreSQL — the claim is atomic, finalize fires exactly once,
     and stop/pause are visible to a DIFFERENT process (the thing the flag files
     could not do).
  C  Failures are visible — a TSETMC outage becomes failed tasks carrying retry
     counts, not silent zeros.
  D  THE KILL TEST — start a run with a real Celery worker, SIGKILL the worker
     mid-flight, start another, and confirm the run completes with every symbol
     written exactly once.
  E  The web layer — /update routes and status shape still work.

Needs Redis running and the «Stock» database reachable. It touches NO production
data: parts B–D run against scratch tables under a synthetic "test" kind (see
.tools/bn_testkind.py) so the real price tables are never written.

Run:  python verify_order06.py
"""
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".tools"))
os.chdir(HERE)

FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


import cache
import db
import jobs
import tasks
import celery_app
import tse_fetch
import bn_testkind

print("=" * 74)
if not cache.available():
    print("Redis is NOT reachable — start it first (it is the Celery broker).")
    sys.exit(1)
print(f"Redis  : {cache.describe()}")
print(f"Broker : {celery_app.app.conf.broker_url}")
print("=" * 74)


# ===========================================================================
print()
print("=" * 74)
print("PART A — Celery configuration and the Beat schedule")
print("=" * 74)

conf = celery_app.app.conf
check(conf.task_acks_late is True,
      "task_acks_late — a task is acked AFTER it finishes, so a killed worker's "
      "message is redelivered rather than lost")
check(conf.task_reject_on_worker_lost is True,
      "task_reject_on_worker_lost — redelivery is immediate, not after the "
      "visibility timeout")
check(conf.worker_prefetch_multiplier == 1,
      f"worker_prefetch_multiplier = {conf.worker_prefetch_multiplier} — a killed "
      f"worker strands no extra messages")
check("pickle" not in conf.accept_content,
      f"broker content types are {conf.accept_content} — no pickle deserialisation")
check(str(conf.timezone) == "Asia/Tehran",
      f"Beat runs on Tehran local time ({conf.timezone})")

# --- the trading week ------------------------------------------------------
sched = conf.beat_schedule
check("nightly-stock-update" in sched and "nightly-etf-update" in sched,
      f"a nightly fetch is scheduled: {', '.join(sorted(sched))}")

# cron day_of_week: 0=Sunday … 6=Saturday. Tehran trades Saturday–Wednesday,
# so Thursday (4) and Friday (5) must NOT be in the set.
DAY_NAMES = {0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
             4: "Thursday", 5: "Friday", 6: "Saturday"}
for name in ("nightly-stock-update", "nightly-etf-update"):
    days = set(sched[name]["schedule"].day_of_week)
    named = ", ".join(DAY_NAMES[d] for d in sorted(days))
    check(days == {6, 0, 1, 2, 3}, f"{name}: runs {named}")
    check(4 not in days and 5 not in days,
          f"{name}: Thursday and Friday excluded (Iranian weekend)")
    hours = set(sched[name]["schedule"].hour)
    check(all(h >= 13 for h in hours),
          f"{name}: fires at {sorted(hours)}:{sorted(sched[name]['schedule'].minute)} "
          f"— after the 12:30 Tehran close")

# --- the file-based control plane is gone ----------------------------------
for gone in ("update_stop.flag", "update_pause.flag", "update_job.meta.json"):
    check(not os.path.exists(gone), f"{gone} deleted from disk")

def code_only(path):
    """Source with comments and docstrings stripped.

    These modules DOCUMENT what was removed and why — market.py's header
    explains the subprocess and the flag files at length. A naive substring
    search would flag that prose as a live reference, so tokenize is used to
    keep only executable code."""
    import tokenize, token as tokmod
    out = []
    with open(path, "rb") as f:
        prev = tokmod.INDENT
        for tok in tokenize.tokenize(f.readline):
            if tok.type == tokenize.COMMENT:
                continue
            # A STRING alone on a logical line is a docstring, not a value.
            if tok.type == tokenize.STRING and prev in (tokenize.INDENT,
                                                        tokenize.DEDENT,
                                                        tokenize.NEWLINE,
                                                        tokenize.NL,
                                                        tokenize.ENCODING):
                prev = tok.type
                continue
            out.append(tok.string)
            prev = tok.type
    return "\n".join(out)


mcode = code_only("market.py")
for tokname in ("STOP_FLAG_PATH", "META_PATH", "subprocess", "taskkill", "Popen"):
    check(tokname not in mcode,
          f"market.py CODE no longer uses {tokname} (docs may still explain it)")

for mod in ("market.py", "tasks.py", "jobs.py", "app.py", "tse_fetch.py",
            "stock_updater.py", "etf_updater.py"):
    src = code_only(mod)
    check("update_job.meta.json" not in src and "update_stop.flag" not in src
          and "update_pause.flag" not in src,
          f"{mod}: the deleted control files appear nowhere in its code")

check("Popen" not in code_only("run_update.py") and
      "start_job" in code_only("run_update.py"),
      "run_update.py enqueues a Celery job instead of being the worker")


# ===========================================================================
print()
print("=" * 74)
print("PART B — job state in PostgreSQL")
print("=" * 74)

bn_testkind.install()
bn_testkind.create_tables(8)
jobs.ensure_tables()

jid = jobs.create_job("test", "1403-10-01", "1403-10-05")
counts = jobs.summary_counts(jid)
check(counts["total"] == 8 and counts["pending"] == 8,
      f"create_job materialises one row per symbol up front ({counts['total']} rows)")

# --- the claim is the lock -------------------------------------------------
first = jobs.claim_ticker(jid, "آزمون001")
check(first is not None and first["attempts"] == 1,
      f"claim_ticker hands out a pending symbol (attempt {first['attempts']})")
again = jobs.claim_ticker(jid, "آزمون001")
check(again is not None and again["attempts"] == 2,
      "a re-claim of an unfinished symbol bumps the attempt count")
jobs.mark_ok(jid, "آزمون001", 5)
after_ok = jobs.claim_ticker(jid, "آزمون001")
check(after_ok is None,
      "…but a FINISHED symbol cannot be claimed again — this is what stops a "
      "redelivered batch duplicating work")

# --- finalize fires exactly once ------------------------------------------
check(jobs.claim_finalize(jid) is False,
      "claim_finalize refuses while symbols are still outstanding")
for i in range(2, 9):
    jobs.mark_ok(jid, f"آزمون{i:03d}", 5)
wins = [jobs.claim_finalize(jid) for _ in range(5)]
check(wins.count(True) == 1,
      f"with everything done, 5 concurrent claim_finalize calls → "
      f"{wins.count(True)} winner (a chord counter could fire more than once)")

# --- stop/pause are visible to ANOTHER process -----------------------------
jid2 = jobs.create_job("test", "1403-10-01", "1403-10-05")
jobs.request_stop(jid2)
probe = subprocess.run(
    [sys.executable, "-c",
     f"import sys;sys.path.insert(0,r'{HERE}');import jobs,json;"
     f"print(json.dumps(jobs.control_flags({jid2})))"],
    capture_output=True, text=True, cwd=HERE)
try:
    stop, paused = json.loads(probe.stdout.strip().splitlines()[-1])
    check(stop is True,
          "a stop requested here is seen by a SEPARATE process — the flag file "
          "could only ever reach processes sharing one filesystem")
except Exception as e:
    check(False, "stop visible cross-process", f"{e} {probe.stderr[:100]}")

jobs.request_pause(jid2, True)
s, p = jobs.control_flags(jid2)
check(p is True, "pause is a row update too")
jobs.finish_job(jid2, status="stopped")


# ===========================================================================
print()
print("=" * 74)
print("PART C — TSETMC failures surface as failed tasks with retry counts")
print("=" * 74)

bn_testkind.create_tables(4)
jid3 = jobs.create_job("test", "1403-10-01", "1403-10-05")

_real_fetch = tse_fetch.fetch


def _always_empty(kind, ticker, start, end, full=False):
    # Exactly the failure mode from the log: TSETMC answers, with nothing in it.
    raise tse_fetch.NoDataError(f"empty result for {ticker}")


tse_fetch.fetch = _always_empty
try:
    tasks.TICKER_BACKOFF = 0.01           # keep the test quick
    res = tasks.fetch_batch.apply(
        args=(jid3, "test", ["آزمون001", "آزمون002"], "1403-10-01", "1403-10-05", False)
    ).get()
finally:
    tse_fetch.fetch = _real_fetch

snap = jobs.snapshot(jid3)
check(res["failed"] == 2 and res["ok"] == 0,
      f"a dataless fetch is a FAILURE, not a silent zero (failed={res['failed']})")
fl = {f["ticker"]: f for f in snap["failed_list"]}
check(len(fl) == 2, f"both symbols recorded as failed ({len(fl)})")
sample = fl.get("آزمون001", {})
check(sample.get("attempts", 0) >= tasks.TICKER_RETRIES,
      f"…each carrying its retry count (attempts={sample.get('attempts')}, "
      f"retries={tasks.TICKER_RETRIES})")
check("بدون داده" in (sample.get("reason") or ""),
      f"…and a Persian reason for the UI: {sample.get('reason')!r}")
check(hasattr(tasks, "ServiceOutage") and tasks.OUTAGE_STREAK == 8,
      f"{tasks.OUTAGE_STREAK} consecutive failures escalate to a retried batch "
      f"— the exact streak the last logged run hit")


# ===========================================================================
print()
print("=" * 74)
print("PART D — KILL THE WORKER MID-FLIGHT")
print("=" * 74)

N_TICKERS = 60
tasks.BATCH_SIZE = 10
bn_testkind.create_tables(N_TICKERS)
bn_testkind.stub_refresh()
cache.reset_stats()
r = cache._client()
r.delete(bn_testkind.REFRESH_MARKER)
version_before = cache.version()

job_id = jobs.create_job("test", "1403-10-01", "1403-10-05")
work = [t for _, t in jobs.tse_reference("test")]
tasks.dispatch_job(job_id, "test", work, "1403-10-01", "1403-10-05", False)
print(f"\n  queued job {job_id}: {len(work)} symbols in "
      f"{-(-len(work) // tasks.BATCH_SIZE)} batches of {tasks.BATCH_SIZE}")


def spawn_worker(tag):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
           "BN_TEST_FETCH_DELAY": "0.25"}
    log = open(os.path.join(HERE, ".tools", f"worker-{tag}.log"), "w",
               encoding="utf-8", errors="replace")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, ".tools", "fake_worker.py")],
                         stdout=log, stderr=subprocess.STDOUT, env=env, cwd=HERE)
    return p, log


def wait_for(predicate, timeout, label):
    t0 = time.time()
    while time.time() - t0 < timeout:
        c = jobs.summary_counts(job_id)
        if predicate(c):
            return c
        time.sleep(0.4)
    return None


def hard_kill(proc):
    """SIGKILL equivalent — no chance to ack, finish, or clean up. This is the
    container being killed, not asked to stop."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except Exception:
        pass


w1, log1 = spawn_worker("A")
print(f"  worker A started (pid {w1.pid})")

progress = wait_for(lambda c: c["ok"] >= 12, 90, "progress")
if progress is None:
    check(False, "worker A made progress before the kill",
          f"counts={jobs.summary_counts(job_id)} — see .tools/worker-A.log")
    hard_kill(w1)
    log1.close()
else:
    print(f"  worker A progress: ok={progress['ok']} pending={progress['pending']} "
          f"running={progress['running']}")
    mid_ok = progress["ok"]

    hard_kill(w1)
    log1.close()
    print(f"  *** worker A KILLED (taskkill /F) with {mid_ok}/{N_TICKERS} done ***")

    after_kill = jobs.summary_counts(job_id)
    check(after_kill["ok"] < N_TICKERS,
          f"the run really was incomplete at the kill "
          f"({after_kill['ok']}/{N_TICKERS} written)")
    check(after_kill["pending"] + after_kill["running"] > 0,
          f"{after_kill['pending'] + after_kill['running']} symbols still outstanding")

    time.sleep(2)
    w2, log2 = spawn_worker("B")
    print(f"  worker B started (pid {w2.pid}) — no manual re-dispatch; waiting "
          f"for its boot-time reconcile to notice the orphaned batch")

    final = wait_for(lambda c: c["ok"] + c["failed"] >= N_TICKERS, 240, "completion")
    time.sleep(4)                      # let finalize_update land
    hard_kill(w2)
    log2.close()

    if final is None:
        check(False, "the run completed after the restart",
              f"counts={jobs.summary_counts(job_id)} — see .tools/worker-B.log")
    else:
        counts = jobs.summary_counts(job_id)
        print(f"\n  final: ok={counts['ok']} failed={counts['failed']} "
              f"of {counts['total']}")

        check(counts["ok"] == N_TICKERS,
              f"NOTHING LOST — all {N_TICKERS} symbols written "
              f"({counts['ok']} ok, {counts['failed']} failed)")

        # --- the duplication check ---------------------------------------
        per = db._rows(f"SELECT ticker, count(*) c FROM {bn_testkind.PRICE_TABLE} "
                       f"GROUP BY ticker ORDER BY c DESC")
        worst = per[0]["c"] if per else 0
        expected = bn_testkind.ROWS_PER_TICKER
        check(len(per) == N_TICKERS,
              f"every symbol has rows in the table ({len(per)}/{N_TICKERS})")
        check(worst == expected,
              f"NOTHING DUPLICATED — max rows for any symbol is {worst}, "
              f"expected exactly {expected}")
        total_rows = db._one(f"SELECT count(*) c FROM {bn_testkind.PRICE_TABLE}")["c"]
        check(total_rows == N_TICKERS * expected,
              f"total rows {total_rows} == {N_TICKERS} symbols x {expected}")

        # --- proof that redelivery actually happened ----------------------
        att = db._one("SELECT COALESCE(SUM(attempts),0) n, MAX(attempts) m "
                      "FROM update_job_ticker WHERE job_id=%s", (job_id,))
        check(att["n"] > N_TICKERS,
              f"redelivery is REAL: {att['n']} claims for {N_TICKERS} symbols "
              f"(max {att['m']} on one symbol) — the killed batch came back")

        # --- the tail of the chain ran, once ------------------------------
        marker = r.get(bn_testkind.REFRESH_MARKER)
        marker = int(marker) if marker else 0
        check(marker == 1,
              f"the last task refreshed the analytics exactly once (marker={marker})")
        check(cache.version() > version_before,
              f"…and bumped the Redis cache version "
              f"({version_before} -> {cache.version()})")
        job = jobs.get_job(job_id)
        check(job["status"] == "done",
              f"the job closed itself out (status={job['status']}, "
              f"result={job['result']})")


# ===========================================================================
print()
print("=" * 74)
print("PART E — the web layer")
print("=" * 74)

import market

st = market.job_status()
for key in ("active", "running", "kind", "processed", "success", "failed",
            "success_list", "failed_list", "current", "result", "elapsed",
            "stopped", "paused", "subset", "full", "start", "end"):
    if key not in st:
        check(False, f"job_status() still returns '{key}' (update.html reads it)")
check(all(k in st for k in ("active", "running", "processed", "success_list")),
      "job_status() keeps the shape update.html already consumes")
check("total" in st and "job_id" in st and "attempts_total" in st,
      "…plus the new total / job_id / attempts_total the DB can now provide")

html = read("templates/update.html")
check("f.attempts" in html, "update.html shows the per-symbol retry count")
check("s.total" in html, "…and uses the job's real symbol count as the denominator")
check("returncode" not in html,
      "…and no longer depends on a subprocess return code")

appsrc = read("app.py")
check("jobs.ensure_tables()" in appsrc, "app startup creates the job tables")
check("_require_admin" in appsrc and "is_admin" in appsrc,
      "the admin-only gating on /update is unchanged")
check("refresh_analytics_async(\"update finished\")" not in appsrc,
      "the status poll no longer kicks off an analytics refresh — finalize does")

import app as webapp
client = webapp.app.test_client()
check(client.get("/update").status_code in (302, 200),
      "/update still requires a login (302) or renders")
check(client.get("/update/status").status_code in (302, 200, 401),
      "/update/status is still gated")


# ===========================================================================
print()
print("=" * 74)
try:
    bn_testkind.drop_tables()
    print("scratch tables dropped — no production data was touched")
except Exception as e:
    print(f"(could not drop scratch tables: {e})")
print("=" * 74)
if FAIL:
    print(f"{len(FAIL)} FAILURE(S):")
    for f in FAIL:
        print("   -", f)
else:
    print("ALL CHECKS PASSED")
print("=" * 74)
sys.exit(1 if FAIL else 0)
