"""
jobs.py — وضعیت کار به‌روزرسانی در PostgreSQL
The job control plane for the market-data update.

WHAT THIS REPLACES

The updater used to keep its state in three files on local disk beside the code:
update_stop.flag, update_pause.flag and update_job.meta.json, plus the run's
stdout log, which job_status() re-parsed line by line on every poll. That worked
for exactly one process. Under Gunicorn it is silently broken in both directions:

  * a «توقف» click that lands on worker 2 writes a flag file worker 1 never sees,
    so the run keeps going while the UI reports it stopped;
  * progress is whatever the polling worker can scrape from a log file that only
    the worker running the job is writing.

With the job in Celery the process running it is not even a web worker, so files
beside the code stop being a control plane at all. Everything therefore lives in
two PostgreSQL tables that every web worker, every Celery worker and Beat all
read and write:

    update_job          one row per run: parameters, status, stop/pause requests
    update_job_ticker   one row per SYMBOL in that run: status, attempts, error

The per-symbol table is what makes the run resumable. A Celery worker killed
mid-flight has its task redelivered; claim_ticker() hands out only symbols that
are not already finished, so the redelivered batch re-does the interrupted symbol
and skips the ones already written. That, together with the idempotent write in
tse_fetch.store(), is what "resumes without losing or duplicating tickers" means
in practice.
"""
import time

import db

# Terminal states — a symbol in one of these is never handed out again.
DONE_STATES = ("ok", "failed", "skipped")

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS update_job (
        id              BIGSERIAL PRIMARY KEY,
        kind            TEXT        NOT NULL,
        start_date      TEXT,
        end_date        TEXT,
        full_rebuild    BOOLEAN     NOT NULL DEFAULT FALSE,
        -- queued → running → (finalizing) → done | stopped | failed
        status          TEXT        NOT NULL DEFAULT 'queued',
        stop_requested  BOOLEAN     NOT NULL DEFAULT FALSE,
        pause_requested BOOLEAN     NOT NULL DEFAULT FALSE,
        total           INTEGER     NOT NULL DEFAULT 0,
        subset          INTEGER     NOT NULL DEFAULT 0,
        result          TEXT,
        created_by      TEXT,
        source          TEXT        NOT NULL DEFAULT 'manual',   -- manual | beat
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at      TIMESTAMPTZ,
        finished_at     TIMESTAMPTZ,
        heartbeat_at    TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS update_job_ticker (
        job_id       BIGINT      NOT NULL REFERENCES update_job(id) ON DELETE CASCADE,
        ticker       TEXT        NOT NULL,
        entity_id    INTEGER,
        -- pending → running → ok | failed | skipped
        status       TEXT        NOT NULL DEFAULT 'pending',
        attempts     INTEGER     NOT NULL DEFAULT 0,
        rows_written INTEGER,
        error        TEXT,
        started_at   TIMESTAMPTZ,
        finished_at  TIMESTAMPTZ,
        PRIMARY KEY (job_id, ticker)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ujt_job_status ON update_job_ticker (job_id, status)",
    "CREATE INDEX IF NOT EXISTS ix_uj_created ON update_job (created_at DESC)",
]


def ensure_tables():
    """Idempotent, and called from app startup and from the Celery worker, so
    whichever comes up first creates them."""
    conn = db.get_db()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for stmt in SCHEMA:
                cur.execute(stmt)
    finally:
        conn.autocommit = False
        db.release(conn)


# ---------------------------------------------------------------------------
# Small helpers over db's pool. These need their own commit handling because
# db._rows() rolls back on release (it is a read helper).
# ---------------------------------------------------------------------------
def _write(sql, params=(), fetch=False):
    conn = db.get_db()
    try:
        with conn.cursor(cursor_factory=db.psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone() if (fetch and cur.description) else None
        conn.commit()
        return dict(row) if row else None
    except Exception:
        conn.rollback()
        raise
    finally:
        db.release(conn)


# ---------------------------------------------------------------------------
# Creating a job
# ---------------------------------------------------------------------------
def create_job(kind, start, end, full=False, tickers=None, created_by=None,
               source="manual"):
    """Insert the job row and one row per symbol it will touch.

    The work list is materialised UP FRONT rather than discovered as the run
    goes. That is what lets progress be reported as "142 of 782" instead of the
    old "142 so far", and what lets a resumed run know exactly what is left."""
    work = tse_reference(kind, tickers)
    if not work:
        raise RuntimeError("هیچ نمادی برای به‌روزرسانی یافت نشد.")

    job = _write(
        """INSERT INTO update_job
             (kind, start_date, end_date, full_rebuild, status, total, subset,
              created_by, source)
           VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s)
           RETURNING id""",
        (kind, start, end, full, len(work),
         len(tickers) if tickers else 0, created_by, source),
        fetch=True)
    job_id = job["id"]

    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            db.psycopg2.extras.execute_values(
                cur,
                "INSERT INTO update_job_ticker (job_id, ticker, entity_id) VALUES %s",
                [(job_id, t, eid) for eid, t in work], page_size=500)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        db.release(conn)
    return job_id


def tse_reference(kind, tickers=None):
    import tse_fetch
    work = tse_fetch.reference_tickers(kind)
    if tickers:
        wanted = {str(t).strip() for t in tickers if str(t).strip()}
        work = [(eid, t) for eid, t in work if t in wanted]
    return work


# ---------------------------------------------------------------------------
# The claim — the heart of resume-safety
# ---------------------------------------------------------------------------
def claim_ticker(job_id, ticker):
    """Take ownership of one symbol, returning its (entity_id, attempts) or None
    if it is already finished.

    A single UPDATE ... RETURNING is what makes this safe: the row is the lock.
    Two workers handed the same redelivered batch cannot both claim the same
    symbol, and a symbol already 'ok' matches nothing and comes back None, so a
    re-run skips it instead of fetching and writing it a second time."""
    row = _write(
        """UPDATE update_job_ticker
              SET status = 'running', attempts = attempts + 1, started_at = now()
            WHERE job_id = %s AND ticker = %s
              AND status NOT IN %s
        RETURNING entity_id, attempts""",
        (job_id, ticker, DONE_STATES), fetch=True)
    return row


def mark_ok(job_id, ticker, rows_written):
    _write("""UPDATE update_job_ticker
                 SET status='ok', rows_written=%s, error=NULL, finished_at=now()
               WHERE job_id=%s AND ticker=%s""",
           (rows_written, job_id, ticker))


def mark_failed(job_id, ticker, reason, error, extra_attempts=0):
    """Record a terminal failure for one symbol.

    `extra_attempts` carries the retries the task made INSIDE the claim.
    claim_ticker() adds 1 when the symbol is taken; without this the column
    would say "1 attempt" for a symbol the worker actually fetched three times,
    which is exactly the under-reporting order 06 exists to fix — a TSETMC
    outage has to be visibly different from a symbol that simply has no data."""
    _write("""UPDATE update_job_ticker
                 SET status='failed', error=%s, attempts=attempts+%s,
                     finished_at=now()
               WHERE job_id=%s AND ticker=%s""",
           (f"{reason}|{error}"[:500], extra_attempts, job_id, ticker))


def mark_retrying(job_id, ticker, error):
    """Between attempts the symbol goes back to pending so a redelivery or a
    later batch can pick it up, but the attempt count and the last error stay —
    which is what turns a TSETMC outage into a visible "failed 3 times" rather
    than a silent zero."""
    _write("""UPDATE update_job_ticker
                 SET status='pending', error=%s
               WHERE job_id=%s AND ticker=%s""",
           (str(error)[:500], job_id, ticker))


def last_activity(job_id):
    """When this job last actually moved, from the per-symbol timestamps rather
    than the job's own heartbeat — the heartbeat is only written between batches,
    so a long batch would look stalled while it was working fine."""
    r = db._one(
        """SELECT MAX(GREATEST(COALESCE(started_at,  '-infinity'::timestamptz),
                               COALESCE(finished_at, '-infinity'::timestamptz))) AS t,
                  now() AS now
             FROM update_job_ticker WHERE job_id=%s""", (job_id,))
    if not r or r["t"] is None:
        return None
    return (r["now"] - r["t"]).total_seconds()


def is_stalled(job_id, seconds):
    """True when the job has outstanding symbols but nothing has moved for
    `seconds`. This is the signal that a worker died holding a message.

    It has to be measured, not assumed: re-dispatching a job that is simply
    working would put duplicate messages on the queue. They would be harmless —
    claim_ticker() makes a re-run a no-op — but pointless."""
    counts = summary_counts(job_id)
    if counts["pending"] + counts["running"] == 0:
        return False
    idle = last_activity(job_id)
    return idle is None or idle >= seconds


def release_stale(job_id, older_than_seconds=900):
    """Return symbols left 'running' by a worker that died to the pending pool.

    Belt and braces: Celery redelivers the task itself, and the redelivered task
    re-claims its own symbols regardless of state. This covers the case where the
    task is NOT redelivered — a worker killed with its queue lost, or a symbol
    orphaned by a crash inside the batch."""
    _write("""UPDATE update_job_ticker
                 SET status='pending'
               WHERE job_id=%s AND status='running'
                 AND started_at < now() - (%s * interval '1 second')""",
           (job_id, older_than_seconds))


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------
def mark_started(job_id):
    _write("""UPDATE update_job
                 SET status='running',
                     started_at=COALESCE(started_at, now()), heartbeat_at=now()
               WHERE id=%s AND status IN ('queued','running')""", (job_id,))


def heartbeat(job_id):
    _write("UPDATE update_job SET heartbeat_at=now() WHERE id=%s", (job_id,))


def claim_finalize(job_id):
    """Exactly one caller gets True — the one that should run the final step.

    Deliberately NOT a Celery chord. A chord fires its callback when a counter
    of completed header tasks reaches the group size, and with acks_late a worker
    killed between finishing a task and acknowledging it causes that task to run
    twice, incrementing the counter twice and firing the callback while symbols
    are still outstanding. Since the verification for this order is precisely
    "kill the worker mid-flight", that window is not hypothetical.

    This asks the database instead: flip the job to 'finalizing' only if nothing
    is left outstanding. The WHERE clause and the NOT EXISTS are evaluated
    atomically in one statement, so whichever worker finishes last wins and every
    other caller gets None."""
    row = _write(
        """UPDATE update_job SET status='finalizing'
            WHERE id = %s
              AND status IN ('running', 'queued')
              AND NOT EXISTS (
                    SELECT 1 FROM update_job_ticker
                     WHERE job_id = %s AND status NOT IN %s)
        RETURNING id""",
        (job_id, job_id, DONE_STATES), fetch=True)
    return row is not None


def finish_job(job_id, status=None, result=None):
    """Close the job out. `status` defaults to 'stopped' when a stop was asked
    for, otherwise 'done'."""
    counts = summary_counts(job_id)
    if status is None:
        j = get_job(job_id)
        status = "stopped" if (j and j["stop_requested"]) else "done"
    if result is None:
        result = (f"RESULT ok={counts['ok']} fail={counts['failed']} "
                  f"total={counts['total']}")
    _write("""UPDATE update_job
                 SET status=%s, result=%s, finished_at=now()
               WHERE id=%s""", (status, result, job_id))


# ---------------------------------------------------------------------------
# Stop / pause — a row update, so it reaches every process
# ---------------------------------------------------------------------------
def request_stop(job_id):
    _write("""UPDATE update_job
                 SET stop_requested=TRUE, pause_requested=FALSE,
                     status=CASE WHEN status IN ('queued','running')
                                 THEN 'stopping' ELSE status END
               WHERE id=%s""", (job_id,))


def request_pause(job_id, paused=True):
    _write("UPDATE update_job SET pause_requested=%s WHERE id=%s", (paused, job_id))


def control_flags(job_id):
    """(stop_requested, pause_requested) — polled by the worker between symbols.
    One tiny indexed read; this is the replacement for os.path.exists(flag)."""
    r = db._one("SELECT stop_requested, pause_requested FROM update_job WHERE id=%s",
                (job_id,))
    if not r:
        return True, False           # job vanished → stop
    return bool(r["stop_requested"]), bool(r["pause_requested"])


def wait_while_paused(job_id, poll=1.0, limit=3600):
    """Idle between symbols while «مکث» is held. A stop still wins, so «توقف»
    ends a paused run without needing to resume it first. Returns True if the
    caller should stop."""
    waited = 0.0
    while waited < limit:
        stop, paused = control_flags(job_id)
        if stop:
            return True
        if not paused:
            return False
        time.sleep(poll)
        waited += poll
    return False


# ---------------------------------------------------------------------------
# Reading state — what /update/status serves
# ---------------------------------------------------------------------------
def get_job(job_id):
    return db._one("SELECT * FROM update_job WHERE id=%s", (job_id,))


def current_job():
    """The job the UI should be showing: the newest unfinished one, else the
    newest of all so a finished run stays on screen after a restart."""
    return db._one(
        """SELECT * FROM update_job
            ORDER BY (status IN ('queued','running','stopping','finalizing')) DESC,
                     created_at DESC
            LIMIT 1""")


def summary_counts(job_id):
    r = db._one(
        """SELECT COUNT(*) AS total,
                  COUNT(*) FILTER (WHERE status='ok')      AS ok,
                  COUNT(*) FILTER (WHERE status='failed')  AS failed,
                  COUNT(*) FILTER (WHERE status='skipped') AS skipped,
                  COUNT(*) FILTER (WHERE status='running') AS running,
                  COUNT(*) FILTER (WHERE status='pending') AS pending
             FROM update_job_ticker WHERE job_id=%s""", (job_id,))
    return r or {"total": 0, "ok": 0, "failed": 0, "skipped": 0,
                 "running": 0, "pending": 0}


def snapshot(job_id=None, list_limit=800):
    """The whole progress picture in three queries, in the exact shape the
    existing update.html already consumes — so the page keeps working — plus the
    attempt counts the old log-scraping版 could not produce."""
    job = get_job(job_id) if job_id else current_job()
    if not job:
        return {"active": False, "running": False, "kind": None, "start": None,
                "end": None, "full": False, "subset": 0, "stopped": False,
                "paused": False, "processed": 0, "success": 0, "failed": 0,
                "success_list": [], "failed_list": [], "current": None,
                "result": None, "elapsed": 0, "job_id": None, "total": 0,
                "queued": False, "attempts_total": 0}

    jid = job["id"]
    counts = summary_counts(jid)
    running = job["status"] in ("queued", "running", "stopping", "finalizing")

    ok_rows = db._rows(
        """SELECT ticker FROM update_job_ticker
            WHERE job_id=%s AND status='ok' ORDER BY finished_at NULLS LAST LIMIT %s""",
        (jid, list_limit))
    bad_rows = db._rows(
        """SELECT ticker, attempts, error FROM update_job_ticker
            WHERE job_id=%s AND status='failed'
            ORDER BY finished_at NULLS LAST LIMIT %s""", (jid, list_limit))
    live = db._rows(
        """SELECT ticker FROM update_job_ticker
            WHERE job_id=%s AND status='running' ORDER BY started_at LIMIT 1""", (jid,))

    failed_list = []
    for r in bad_rows:
        err = r["error"] or ""
        reason = err.split("|", 1)[0] if "|" in err else (err[:60] or "خطا در دریافت")
        failed_list.append({"ticker": r["ticker"], "reason": reason,
                            "attempts": r["attempts"],
                            "detail": err.split("|", 1)[-1][:200] if "|" in err else ""})

    started = job["started_at"] or job["created_at"]
    ended = job["finished_at"]
    ref = ended or _now(job)
    elapsed = int((ref - started).total_seconds()) if started else 0

    attempts = db._one(
        "SELECT COALESCE(SUM(attempts),0) AS n FROM update_job_ticker WHERE job_id=%s",
        (jid,))

    return {
        "active": True,
        "running": running,
        "queued": job["status"] == "queued",
        "job_id": jid,
        "kind": job["kind"],
        "start": job["start_date"],
        "end": job["end_date"],
        "full": job["full_rebuild"],
        "subset": job["subset"],
        "total": counts["total"],
        "stopped": job["status"] == "stopped" or bool(job["stop_requested"]),
        "paused": bool(job["pause_requested"]) and running,
        "processed": counts["ok"] + counts["failed"] + counts["skipped"],
        "success": counts["ok"],
        "failed": counts["failed"],
        "success_list": [r["ticker"] for r in ok_rows],
        "failed_list": failed_list,
        "current": live[0]["ticker"] if (live and running) else None,
        "result": job["result"],
        "status": job["status"],
        "source": job["source"],
        "attempts_total": int(attempts["n"]) if attempts else 0,
        "elapsed": elapsed,
    }


def _now(job):
    r = db._one("SELECT now() AS n")
    return r["n"]


def last_job_params():
    """Parameters of the most recent run, so «تلاش دوباره» reuses the same
    kind / range / mode."""
    j = current_job()
    if not j:
        return None
    return {"kind": j["kind"], "start": j["start_date"], "end": j["end_date"],
            "full": j["full_rebuild"]}


def active_job_id():
    r = db._one(
        """SELECT id FROM update_job
            WHERE status IN ('queued','running','stopping','finalizing')
            ORDER BY created_at DESC LIMIT 1""")
    return r["id"] if r else None


def pending_tickers(job_id):
    """Symbols still to do — used when re-dispatching a job whose worker died
    without its tasks being redelivered."""
    return [r["ticker"] for r in db._rows(
        "SELECT ticker FROM update_job_ticker WHERE job_id=%s AND status NOT IN %s "
        "ORDER BY ticker", (job_id, DONE_STATES))]
