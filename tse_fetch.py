"""
tse_fetch.py — دریافت و درج قیمت یک نماد
The per-ticker fetch-and-store core, shared by stock_updater, etf_updater and the
Celery tasks so there is exactly ONE implementation of it.

Two things here are new relative to the loop this replaced, and both are
prerequisites for running the update under Celery:

1. THE WRITE IS IDEMPOTENT. The old insert was a bare INSERT with no conflict
   handling, so running the same date range twice silently doubled the rows —
   and under a retrying task queue "twice" stops being hypothetical: any worker
   killed between finishing a ticker and acknowledging it will have that ticker
   redelivered. store() therefore deletes the rows it is about to replace and
   inserts inside ONE transaction, so a ticker written twice is indistinguishable
   from a ticker written once.

   Delete-then-insert rather than ON CONFLICT because the price tables have no
   unique constraint to conflict on, and adding one to a 6-million-row table with
   unknown existing duplicates is a schema migration, not a task-queue change.

2. FAILURES RAISE. The old code printed "No data returned for ticker X" and
   counted a zero, which is how eight consecutive TSETMC outages in the last
   logged run looked exactly like eight symbols that legitimately had no data.
   Every failure mode now raises a typed exception the task layer can retry and
   record with an attempt count.
"""
import os

import psycopg2.extras

import db

# The two price tables differ only in their foreign-key column and the reference
# table the id comes from. Everything else about the fetch is identical, which is
# why the two updater modules were near-duplicates of each other.
KINDS = {
    "stock": {
        "table": "stockpricehistory",
        "id_col": "stock_id",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "سهام",
    },
    "etf": {
        "table": "etfpricehistory",
        "id_col": "etf_id",
        "ref_sql": "SELECT id, ticker FROM etf ORDER BY ticker",
        "label": "صندوق",
    },
}

COLUMNS = ("j_date", "date", "weekday", "open", "high", "low", "close", "final",
           "volume", "value", "no", "name", "adj_open", "adj_high", "adj_low",
           "adj_close", "adj_final")

# The finpy_tse DataFrame column for each of the above, in the same order.
_SOURCE = ("J-Date", "Date", "Weekday", "Open", "High", "Low", "Close", "Final",
           "Volume", "Value", "No", "Name", "Adj Open", "Adj High", "Adj Low",
           "Adj Close", "Adj Final")


class FetchError(Exception):
    """Base for anything that stopped one ticker from being updated."""
    #: Persian text shown against the symbol on the /update page.
    reason = "خطا در دریافت"


class TransientFetchError(FetchError):
    """TSETMC was unreachable, slow, or returned something unparseable.

    Retryable: the task layer backs off and tries again, and the attempt count
    is recorded so a symbol that failed four times is visibly different from one
    that failed once."""
    reason = "خطای شبکه/سرویس — تلاش مجدد"


class NoDataError(FetchError):
    """The fetch succeeded but returned an empty frame.

    Also treated as retryable. TSETMC returns an empty result both for a symbol
    that genuinely has no rows in the window AND when it is failing — the two are
    indistinguishable from here, and the last logged run showed eight of these in
    a row, which was an outage rather than eight dataless symbols. Retrying costs
    little and stops an outage being recorded as legitimate emptiness; if it is
    still empty after the retries, THAT is the honest signal."""
    reason = "بدون داده در بازه"


class StoreError(FetchError):
    """The rows came back but could not be written."""
    reason = "خطا در درج"


def reference_tickers(kind):
    """[(entity_id, ticker)] for every symbol of this kind, from the reference
    table. This is the work list a job is built from."""
    cfg = KINDS[kind]
    return [(r["id"], r["ticker"]) for r in db._rows(cfg["ref_sql"])]


# TSETMC is an Iranian DOMESTIC host, and an Iranian desktop very often has
# HTTP_PROXY/HTTPS_PROXY pointing at a local tunnel client (V2Ray, Xray, …) for
# reaching sites abroad. `requests` — which finpy_tse uses — honours those
# variables for every host, so the fetch gets tunnelled out of the country and
# back, which is exactly the traffic TSETMC and the tunnel are worst at:
# measured on this machine, the same request timed out after 25 s through the
# proxy and returned HTTP 200 in 3.8 s direct.
#
# So the proxy is bypassed for TSETMC only. Everything else keeps whatever proxy
# the environment says — this appends to `no_proxy`, it does not clear it — and
# setting TSE_USE_PROXY=1 turns the bypass off for anyone who genuinely reaches
# TSETMC through one.
TSE_HOSTS = "tsetmc.com,.tsetmc.com,old.tsetmc.com,cdn.tsetmc.com,www.tsetmc.com"


def _bypass_proxy_for_tsetmc():
    """Add the TSETMC hosts to no_proxy unless TSE_USE_PROXY says otherwise.

    Both spellings are set: `requests` reads the lower-case name first, but the
    upper-case one is what a Windows environment usually carries, and a library
    that reads only that one would still tunnel.
    """
    if os.environ.get("TSE_USE_PROXY", "").strip().lower() in ("1", "true", "yes"):
        return
    for name in ("no_proxy", "NO_PROXY"):
        current = os.environ.get(name, "")
        missing = [h for h in TSE_HOSTS.split(",") if h not in current]
        if missing:
            os.environ[name] = ",".join(filter(None, [current, *missing]))


# ---------------------------------------------------------------------------
# A DEFAULT HTTP TIMEOUT
#
# finpy_tse calls `requests.get(url, headers=headers)` for every symbol and never
# passes `timeout`, and requests without a timeout waits FOREVER. That is not a
# theoretical risk here: a TCP connection to TSETMC that stalls without a reset —
# routine on this network, where a filtering middlebox blackholes the connection
# instead of refusing it — parks the call in a socket read that no amount of
# waiting ends.
#
# What that cost, once, in full: a worker claimed «طلوع», disappeared into
# requests, and was still there 73 minutes later. Because the Windows worker runs
# `--pool=solo`, the task executes inline in the consumer thread, so the worker
# stopped taking messages at the same moment — 212 batches queued up in Redis
# behind it and the /update page sat at 197/293 reporting «بازیابی خودکار در
# جریان است» while the automatic recovery re-queued tickers that nothing was
# left to consume.
#
# Celery's `task_time_limit` (celery_app.py, 1800 s) does NOT cover this. Time
# limits are implemented by the PREFORK pool, which kills the child running the
# task; the solo pool has no child to kill, so on Windows that ceiling is
# configured and inert. The timeout has to live at the HTTP layer, which is the
# one place that works on every pool and every platform.
#
# `(connect, read)` — the read half is "no bytes for N seconds", not a deadline
# for the whole download, so a slow but progressing response is not cut off. A
# timeout raises inside fetch()'s `except Exception`, becomes a
# TransientFetchError, and is retried and recorded like any other flaky symbol.
# ---------------------------------------------------------------------------
def _http_timeout():
    raw = os.environ.get("TSE_HTTP_TIMEOUT", "10,45")
    try:
        connect, read = (float(x) for x in raw.split(","))
        return (connect, read)
    except (ValueError, TypeError):
        return (10.0, 45.0)


_TIMEOUT_INSTALLED = False


def _install_http_timeout():
    """Give every timeout-less `requests` call in this process a default one.

    Patched at `Session.request`, which is the single funnel every entry point
    goes through — `requests.get`, `Session.get`, all of them. It only fills in a
    timeout that was not supplied, so a caller that passes its own still wins,
    and it is installed once and never removed: restoring it around each fetch
    would open a window where a concurrent call is unprotected, for no gain.

    Monkey-patching a third-party library is not the first choice. The
    alternative — asking finpy_tse to accept a timeout — is a change to a package
    this project does not own, and `socket.setdefaulttimeout()`, the other way to
    do it without touching finpy, is process-global: it would also apply to
    psycopg2's connections and to the blocking Redis read Celery's broker sits
    in, and break both.
    """
    global _TIMEOUT_INSTALLED
    if _TIMEOUT_INSTALLED:
        return
    try:
        import requests
    except ImportError:                            # finpy is absent too; fetch() reports it
        return
    original = requests.Session.request
    if getattr(original, "_bn_default_timeout", False):
        _TIMEOUT_INSTALLED = True
        return
    timeout = _http_timeout()

    def request(self, method, url, *args, **kwargs):
        # `timeout` is the 7th positional parameter after `url`
        # (params, data, headers, cookies, files, auth, timeout), so anything
        # shorter than that cannot have supplied one positionally.
        if "timeout" not in kwargs and len(args) < 7:
            kwargs["timeout"] = timeout
        return original(self, method, url, *args, **kwargs)

    request._bn_default_timeout = True
    requests.Session.request = request
    _TIMEOUT_INSTALLED = True


def fetch(kind, ticker, start, end, full=False):
    """Pull one symbol's history from TSETMC. Returns a DataFrame.

    finpy_tse is imported lazily: the web process only needs to ENQUEUE jobs, and
    importing finpy there would pull in aiohttp and touch the event loop for no
    reason. Only the Celery worker actually calls this."""
    # Before finpy builds its session: requests reads the proxy environment at
    # request time, but a session created earlier can cache the resolution.
    _bypass_proxy_for_tsetmc()
    # …and before it makes a request, so no call can wait for ever. See the
    # comment on _install_http_timeout above for what that cost when it could.
    _install_http_timeout()
    try:
        import finpy_tse as fpy
        import pandas as pd
    except ImportError as e:                       # worker image without finpy
        raise TransientFetchError(f"finpy_tse unavailable: {e}") from e

    try:
        raw = fpy.Get_Price_History(
            stock=ticker,
            start_date=start,
            end_date=end,
            # finpy's flag means "ignore the start/end arguments": its own
            # docstring is «دریافت همه سابقه قیمت بدون توجه به تاریخ شروع و
            # پایان» and the body guards the date filter with
            # `if (not ignore_date)`. So True = the WHOLE history, False = just
            # the requested range — the opposite of what the comment that used
            # to sit here claimed, and this call passed `not full`, so BOTH
            # modes did the wrong thing:
            #
            #   full=True  (rebuild)     fetched only the narrow window, while
            #                            store() deletes the ticker's ENTIRE
            #                            history first — a rebuild of one week
            #                            destroyed twenty years of prices.
            #   full=False (incremental) fetched all twenty years every night,
            #                            which is why each historical run had a
            #                            complete copy of the history to write.
            ignore_date=full,
            adjust_price=True,
            show_weekday=True,
            double_date=True,
        )
    except Exception as e:
        # Everything finpy raises — connection resets, timeouts, HTML where JSON
        # was expected, KeyErrors from a changed response — is transient from our
        # point of view. Nothing here is worth distinguishing at this layer.
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e

    df = pd.DataFrame(raw)
    if df.empty:
        raise NoDataError(f"empty result for {ticker} ({start}..{end})")
    df.reset_index(inplace=True)
    missing = [c for c in _SOURCE if c not in df.columns]
    if missing:
        raise TransientFetchError(f"response missing columns {missing}")
    df["J-Date"] = df["J-Date"].astype(str)
    return df


def store(kind, entity_id, ticker, df, full=False):
    """Write one symbol's rows, replacing whatever is already there for the dates
    covered. Returns the number of rows written.

    The delete and the insert share one transaction, so a crash between them
    cannot leave the symbol with a hole, and a redelivered task cannot leave it
    with duplicates."""
    cfg = KINDS[kind]
    rows = []
    for _, r in df.iterrows():
        rows.append((entity_id,) + tuple(_clean(r[c]) for c in _SOURCE))

    if not rows:
        raise NoDataError(f"nothing to write for {ticker}")

    # One row per date before it reaches the database. The price tables now carry
    # a UNIQUE (ticker, date) index, so a source frame that repeats a date would
    # abort the whole symbol's transaction rather than simply overwriting it.
    # Later rows win, which matches this function's replace-what-is-there intent.
    _date_at = COLUMNS.index("date") + 1
    by_date = {}
    for r in rows:
        by_date[r[_date_at]] = r
    rows = list(by_date.values())

    dates = [r[_date_at] for r in rows]
    lo, hi = min(dates), max(dates)

    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            if full:
                # A full rebuild replaces the symbol's entire history.
                cur.execute(f"DELETE FROM {cfg['table']} WHERE ticker = %s", (ticker,))
            else:
                # Otherwise only the window actually being rewritten, so an
                # incremental run never touches history outside its range.
                cur.execute(
                    f"DELETE FROM {cfg['table']} WHERE ticker = %s "
                    f"AND date >= %s AND date <= %s", (ticker, lo, hi))
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO {cfg['table']} "
                f"({cfg['id_col']}, ticker, {', '.join(COLUMNS)}) VALUES %s",
                [(r[0], ticker) + r[1:] for r in rows],
                page_size=500,
            )
        conn.commit()
        return len(rows)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise StoreError(f"{type(e).__name__}: {e}") from e
    finally:
        db.release(conn)


def _clean(v):
    """pandas NaN / NaT / numpy scalars → something psycopg2 will accept."""
    if v is None:
        return None
    try:
        import pandas as pd
        if pd.isna(v):
            return None
    except (TypeError, ValueError, ImportError):
        pass
    item = getattr(v, "item", None)       # numpy scalar → python scalar
    if callable(item):
        try:
            return item()
        except Exception:
            pass
    return v


def fetch_and_store(kind, entity_id, ticker, start, end, full=False):
    """The whole per-ticker unit of work. Returns the row count, or raises a
    FetchError subclass describing what went wrong."""
    df = fetch(kind, ticker, start, end, full=full)
    return store(kind, entity_id, ticker, df, full=full)
