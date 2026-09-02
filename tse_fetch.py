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
        "dataset": "price",
        "table": "stockpricehistory",
        "id_col": "stock_id",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "سهام",
    },
    "etf": {
        "dataset": "price",
        "table": "etfpricehistory",
        "id_col": "etf_id",
        "ref_sql": "SELECT id, ticker FROM etf ORDER BY ticker",
        "label": "صندوق",
    },

    # -----------------------------------------------------------------------
    # THE DATASETS ADDED ON TOP OF PRICE (market_data.py owns their schema)
    #
    # Everything about a run — the work list, the claim, the retry, the progress
    # tiles, «توقف», «مکث» and resume — is written against `kind` and nothing
    # else (jobs.py, tasks.py, market.py). So a new dataset is an entry in this
    # dict plus a handler below, and it inherits all of it. That is the whole
    # reason the update page needed no new machinery to gain five data types.
    #
    # `unit` is what the progress panel counts: «نماد» for the per-symbol jobs,
    # «شاخص» for the index sweep, «مرحله» for the two single-shot ones. Saying
    # «۱ از ۱ نماد» about a whole-market snapshot would be a lie about what is
    # happening.
    # -----------------------------------------------------------------------
    "stock_ri": {
        "dataset": "ri",
        "for_kind": "stock",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "حقیقی/حقوقی سهام",
        "unit": "نماد",
    },
    "etf_ri": {
        "dataset": "ri",
        "for_kind": "etf",
        "ref_sql": "SELECT id, ticker FROM etf ORDER BY ticker",
        "label": "حقیقی/حقوقی صندوق‌ها",
        "unit": "نماد",
    },
    "index": {
        "dataset": "index",
        "ref_fn": "_index_worklist",
        "label": "شاخص‌ها",
        "unit": "شاخص",
    },
    "usd": {
        "dataset": "usd",
        "ref_fn": "_single_worklist",
        "single": "دلار آزاد",
        "label": "قیمت دلار",
        "unit": "مرحله",
    },
    "watch": {
        "dataset": "watch",
        "ref_fn": "_single_worklist",
        "single": "دیده‌بان بازار",
        "label": "دیده‌بان و عمق بازار",
        "unit": "مرحله",
    },
    "symbols": {
        "dataset": "symbols",
        "ref_fn": "_single_worklist",
        "single": "فهرست نمادها",
        "label": "فهرست مرجع نمادها",
        "unit": "مرحله",
    },

    # ---- the intraday family ----------------------------------------------
    #
    # These three are ONE REQUEST PER SYMBOL-DAY, so a market-wide run of any of
    # them is thousands of requests. They are offered anyway, because for a
    # single symbol they are exactly the right tool — and the form says which is
    # which. `heavy` is what marks them: the run form uses it to insist on a
    # symbol before it will start one across the whole market.
    "stock_queue": {
        "dataset": "queue",
        "for_kind": "stock",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "سابقهٔ صف سهام",
        "unit": "نماد", "heavy": True,
    },
    "etf_queue": {
        "dataset": "queue",
        "for_kind": "etf",
        "ref_sql": "SELECT id, ticker FROM etf ORDER BY ticker",
        "label": "سابقهٔ صف صندوق‌ها",
        "unit": "نماد", "heavy": True,
    },
    "stock_ob": {
        "dataset": "intraday_ob",
        "for_kind": "stock",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "عمق بازار درون‌روز",
        "unit": "نماد", "heavy": True,
    },
    "stock_trades": {
        "dataset": "intraday_trades",
        "for_kind": "stock",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "ریز معاملات",
        "unit": "نماد", "heavy": True,
    },
    "shareholders": {
        "dataset": "shareholders",
        "for_kind": "stock",
        "ref_sql": "SELECT stockid AS id, ticker FROM stocks ORDER BY ticker",
        "label": "سهامداران عمده",
        "unit": "نماد",
    },
}

#: The kinds that write into a price table, and so are what «حذف داده‌های قیمت»,
#: the analytics rebuild and the nightly beat mean by "a kind". Everything that
#: asks "is this the price updater?" asks this, rather than testing
#: `kind in ("stock", "etf")` in eight places that would drift apart.
PRICE_KINDS = ("stock", "etf")


def is_price_kind(kind):
    return KINDS.get(kind, {}).get("dataset") == "price"


def kind_label(kind):
    return KINDS.get(kind, {}).get("label", kind)


def kind_unit(kind):
    return KINDS.get(kind, {}).get("unit", "نماد")

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
    """[(entity_id, ticker)] for every unit of work of this kind.

    For the price and حقیقی/حقوقی kinds that is the symbols in the reference
    table. For the others the "ticker" is whatever names one unit of work — an
    index key, or a single step — because that is the column jobs.py stores and
    the progress panel prints, and giving those jobs a work list in the same
    shape is what lets them reuse every part of the run machinery."""
    cfg = KINDS[kind]
    fn = cfg.get("ref_fn")
    if fn:
        return globals()[fn](cfg)
    return [(r["id"], r["ticker"]) for r in db._rows(cfg["ref_sql"])]


def _single_worklist(cfg):
    """A job with exactly one step — the whole-market snapshot, the dollar, the
    reference list. entity_id is 0 rather than None: jobs.claim_ticker returns
    the row itself, and a NULL there reads as "no row" at several call sites."""
    return [(0, cfg["single"])]


def _index_worklist(cfg):
    """The ten market-wide indices, then the forty گروه صنعت ones — fifty items,
    each its own retryable row on the progress panel.

    The sector names come from market_data.SECTOR_INDICES rather than from
    `stocks.sector`, and that is not a shortcut: Get_SectorIndex_History looks
    the name up in a hard-coded table of exactly these forty strings and, on a
    miss, scrapes a Google search page for the web-id. Feeding it this database's
    group names («خودرو و ساخت قطعات») misses all forty times. See the comment on
    SECTOR_INDICES."""
    import market_data
    return ([(0, key) for key, _name, _fn in market_data.MARKET_INDICES] +
            [(0, market_data.sector_key(name))
             for name in market_data.SECTOR_NAMES])


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
    """The whole unit of work for one item. Returns the row count, or raises a
    FetchError subclass describing what went wrong.

    Dispatch is on the kind's DATASET, not on the kind: سهام and صندوق‌ها share
    one price handler, and حقیقی/حقوقی for the two of them shares another."""
    handler = _HANDLERS.get(KINDS[kind].get("dataset", "price"))
    if handler is None:
        raise FetchError(f"unknown dataset for kind {kind!r}")
    return handler(kind, entity_id, ticker, start, end, full)


def _price_dataset(kind, entity_id, ticker, start, end, full):
    df = fetch(kind, ticker, start, end, full=full)
    return store(kind, entity_id, ticker, df, full=full)


# ===========================================================================
# THE DATASETS BEYOND PRICE
#
# Each handler below has the same contract as _price_dataset: fetch one unit of
# work, write it idempotently, return the row count, and raise a FetchError
# subclass for anything that went wrong. Nothing above this line and nothing in
# jobs.py / tasks.py needs to know which one ran.
#
# Every write is DELETE-then-INSERT inside one transaction, or an explicit
# ON CONFLICT upsert where the table has a key to conflict on — the same
# idempotence store() gives the price tables, and for the same reason: acks_late
# means a worker killed after writing and before acknowledging gets the same
# work handed back.
# ===========================================================================
def _finpy():
    """The lazy import, with the proxy bypass and the default HTTP timeout
    installed first — exactly the order fetch() uses, and for exactly the
    reasons documented on those two functions."""
    _bypass_proxy_for_tsetmc()
    _install_http_timeout()
    try:
        import finpy_tse as fpy
        import pandas as pd
        return fpy, pd
    except ImportError as e:
        raise TransientFetchError(f"finpy_tse unavailable: {e}") from e


def _frame(raw, pd):
    """finpy returns its frames INDEXED on J-Date. reset_index() is what puts
    that back in reach as a column, and every handler needs it."""
    df = pd.DataFrame(raw)
    if df.empty:
        raise NoDataError("empty result")
    return df.reset_index()


def _require(df, cols, what):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise TransientFetchError(f"{what}: response missing columns {missing}")


def _replace(table, key_cols, columns, rows):
    """Write `rows` into `table`, replacing whatever shares their key.

    ON CONFLICT DO UPDATE rather than delete-then-insert: every one of these
    tables HAS a primary key to conflict on (unlike the price tables, whose
    delete-then-insert exists precisely because they do not), and an upsert is
    both shorter and safe against two workers writing overlapping windows.
    """
    if not rows:
        raise NoDataError(f"nothing to write into {table}")
    cols = ", ".join(f'"{c}"' for c in columns)
    updates = ", ".join(f'"{c}" = EXCLUDED."{c}"'
                        for c in columns if c not in key_cols)
    conflict = ", ".join(f'"{c}"' for c in key_cols)
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                f'INSERT INTO {table} ({cols}) VALUES %s '
                f'ON CONFLICT ({conflict}) DO UPDATE SET {updates}',
                rows, page_size=500)
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


# ---------------------------------------------------------------------------
# حقیقی و حقوقی — Get_RI_History
# ---------------------------------------------------------------------------
_RI_COLUMNS = ("kind", "entity_id", "ticker", "j_date", "date", "weekday",
               "no_buy_r", "no_buy_i", "no_sell_r", "no_sell_i",
               "vol_buy_r", "vol_buy_i", "vol_sell_r", "vol_sell_i",
               "val_buy_r", "val_buy_i", "val_sell_r", "val_sell_i")

_RI_SOURCE = ("J-Date", "Date", "Weekday",
              "No_Buy_R", "No_Buy_I", "No_Sell_R", "No_Sell_I",
              "Vol_Buy_R", "Vol_Buy_I", "Vol_Sell_R", "Vol_Sell_I",
              "Val_Buy_R", "Val_Buy_I", "Val_Sell_R", "Val_Sell_I")


def _ri_dataset(kind, entity_id, ticker, start, end, full):
    fpy, pd = _finpy()
    try:
        raw = fpy.Get_RI_History(
            stock=ticker,
            start_date=start,
            end_date=end,
            # Same inversion as the price fetch: finpy's `ignore_date` means
            # "ignore the start/end arguments", so True is the WHOLE history.
            ignore_date=full,
            show_weekday=True,
            double_date=True,
            # ⚠ NOT optional. With the default (alt=False) this function raises
            # `AttributeError: Can only use .dt accessor with datetimelike
            # values` on every call in the installed build — measured, not
            # inferred: TSETMC's primary endpoint now returns a shape finpy's
            # own date handling cannot parse. `alt=True` takes the alternate
            # endpoint, which returns the full frame documented in the guide
            # (plus a Market column, minus Part). If a future finpy fixes the
            # primary path this stays correct; the alternate is not deprecated.
            alt=True,
        )
    except Exception as e:
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e

    df = _frame(raw, pd)
    _require(df, _RI_SOURCE, "Get_RI_History")
    df["J-Date"] = df["J-Date"].astype(str)

    ri_kind = KINDS[kind].get("for_kind", "stock")
    by_date = {}
    for _, r in df.iterrows():
        vals = tuple(_clean(r[c]) for c in _RI_SOURCE)
        if vals[1] is None:                    # no Gregorian date → nothing to key on
            continue
        by_date[vals[1]] = (ri_kind, entity_id, ticker) + vals
    return _replace("ri_history", ("ticker", "date"), _RI_COLUMNS,
                    list(by_date.values()))


# ---------------------------------------------------------------------------
# شاخص‌ها — the ten market indices and one per گروه صنعت
# ---------------------------------------------------------------------------
_INDEX_COLUMNS = ("index_key", "name", "j_date", "date", "weekday",
                  "open", "high", "low", "close", "adj_close", "volume")

_INDEX_SOURCE = ("J-Date", "Date", "Weekday", "Open", "High", "Low", "Close",
                 "Adj Close", "Volume")


def _index_dataset(kind, entity_id, index_key, start, end, full):
    import market_data
    fpy, pd = _finpy()
    args = dict(start_date=start, end_date=end, ignore_date=full,
                just_adj_close=False, show_weekday=True, double_date=True)
    try:
        if market_data.is_sector(index_key):
            sector = index_key[len(market_data.SECTOR_PREFIX):]
            raw = fpy.Get_SectorIndex_History(sector=sector, **args)
        else:
            fn_name = dict((k, f) for k, _n, f in market_data.MARKET_INDICES).get(index_key)
            if not fn_name:
                raise FetchError(f"unknown index key {index_key!r}")
            raw = getattr(fpy, fn_name)(**args)
    except FetchError:
        raise
    except Exception as e:
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e

    # finpy signals a name it cannot resolve by PRINTING and returning None
    # rather than raising, so None here is a bad key, not an outage. Saying so
    # is the difference between a symbol that retries three times for nothing
    # and one whose row on the failed list explains itself.
    if raw is None:
        raise FetchError(f"TSETMC has no index named {index_key!r}")

    df = _frame(raw, pd)
    _require(df, _INDEX_SOURCE, f"index {index_key}")
    df["J-Date"] = df["J-Date"].astype(str)

    name = market_data.index_label(index_key)
    by_date = {}
    for _, r in df.iterrows():
        vals = tuple(_clean(r[c]) for c in _INDEX_SOURCE)
        if vals[1] is None:
            continue
        by_date[vals[1]] = (index_key, name) + vals
    return _replace("index_history", ("index_key", "date"), _INDEX_COLUMNS,
                    list(by_date.values()))


# ---------------------------------------------------------------------------
# دلار آزاد — Get_USD_RIAL
# ---------------------------------------------------------------------------
_USD_COLUMNS = ("j_date", "date", "weekday", "open", "high", "low", "close")
_USD_SOURCE = ("J-Date", "Date", "Weekday", "Open", "High", "Low", "Close")


def _usd_dataset(kind, entity_id, ticker, start, end, full):
    fpy, pd = _finpy()
    try:
        raw = fpy.Get_USD_RIAL(start_date=start, end_date=end, ignore_date=full,
                               show_weekday=True, double_date=True)
    except Exception as e:
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e

    df = _frame(raw, pd)
    _require(df, _USD_SOURCE, "Get_USD_RIAL")
    df["J-Date"] = df["J-Date"].astype(str)
    by_date = {}
    for _, r in df.iterrows():
        vals = tuple(_clean(r[c]) for c in _USD_SOURCE)
        if vals[1] is None:
            continue
        by_date[vals[1]] = vals
    return _replace("usd_rial", ("date",), _USD_COLUMNS, list(by_date.values()))


# ---------------------------------------------------------------------------
# دیده‌بان بازار — Get_MarketWatch
#
# ONE request for the ENTIRE market: ~1,540 symbols and ~7,000 order-book rows
# in 3.3 seconds, measured. That is why the queue history is not fetched symbol
# by symbol — see the note at the top of market_data.py.
#
# The column names here are the INSTALLED package's, which differ from the
# printed guide in two places: the queue values are `BQ-Value` / `SQ-Value` with
# a hyphen (the guide prints an underscore), and the order-book frame carries
# Day_LL / Day_UL in its MultiIndex rather than as columns. Both were read off a
# live response, not off the PDF.
# ---------------------------------------------------------------------------
_WATCH_COLUMNS = ("ticker", "j_date", "date", "captured_at", "trade_type",
                  "board_time", "open", "high", "low", "close", "final",
                  "pct_close", "pct_final", "day_ul", "day_ll", "value",
                  "volume", "no", "bq_value", "sq_value", "bqpc", "sqpc",
                  "vol_buy_r", "vol_buy_i", "vol_sell_r", "vol_sell_i",
                  "no_buy_r", "no_buy_i", "no_sell_r", "no_sell_i",
                  "name", "market", "sector", "share_no", "base_vol",
                  "market_cap", "eps")

_WATCH_SOURCE = ("Trade Type", "Time", "Open", "High", "Low", "Close", "Final",
                 "Close(%)", "Final(%)", "Day_UL", "Day_LL", "Value", "Volume",
                 "No", "BQ-Value", "SQ-Value", "BQPC", "SQPC",
                 "Vol_Buy_R", "Vol_Buy_I", "Vol_Sell_R", "Vol_Sell_I",
                 "No_Buy_R", "No_Buy_I", "No_Sell_R", "No_Sell_I",
                 "Name", "Market", "Sector", "Share-No", "Base-Vol",
                 "Market Cap", "EPS")

_OB_COLUMNS = ("ticker", "depth", "captured_at", "j_date", "day_ul", "day_ll",
               "sell_no", "sell_vol", "sell_price", "buy_price", "buy_vol",
               "buy_no")


def _watch_dataset(kind, entity_id, ticker, start, end, full):
    import datetime
    import jdatetime
    import market_data
    fpy, pd = _finpy()
    try:
        raw = fpy.Get_MarketWatch(save_excel=False)
    except Exception as e:
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e
    if not isinstance(raw, tuple) or len(raw) != 2:
        raise TransientFetchError("Get_MarketWatch did not return (watch, orderbook)")
    watch, book = raw
    if watch is None or len(watch) == 0:
        raise NoDataError("Get_MarketWatch returned an empty board")

    now = datetime.datetime.now()
    today = datetime.date.today()
    jd = str(jdatetime.date.fromgregorian(
        year=today.year, month=today.month, day=today.day))

    wdf = watch.reset_index()
    _require(wdf, ("Ticker",) + _WATCH_SOURCE, "Get_MarketWatch")

    rows = []
    seen = set()
    for _, r in wdf.iterrows():
        tk = _clean(r["Ticker"])
        if not tk or tk in seen:
            # A symbol can appear twice when a بلوکی print shares its name with
            # the تابلو row. The board row is the one that matters and comes
            # first; keeping both would violate the (ticker, j_date) key.
            continue
        seen.add(tk)
        rows.append((tk, jd, today, now) + tuple(_clean(r[c]) for c in _WATCH_SOURCE))
    n = _replace("market_snapshot", ("ticker", "j_date"), _WATCH_COLUMNS, rows)

    # ---- عمق بازار. Replaced wholesale, never merged: it is a photograph of
    # right now, and half of an old one mixed into a new one is not a state the
    # market was ever in.
    if book is not None and len(book):
        bdf = book.reset_index()
        need = ("Ticker", "OB-Depth", "Day_UL", "Day_LL", "Sell-No", "Sell-Vol",
                "Sell-Price", "Buy-Price", "Buy-Vol", "Buy-No")
        _require(bdf, need, "Get_MarketWatch order book")
        brows = []
        bseen = set()
        for _, r in bdf.iterrows():
            tk, depth = _clean(r["Ticker"]), _clean(r["OB-Depth"])
            if not tk or depth is None or (tk, depth) in bseen:
                continue
            bseen.add((tk, depth))
            brows.append((tk, int(depth), now, jd,
                          _clean(r["Day_UL"]), _clean(r["Day_LL"]),
                          _clean(r["Sell-No"]), _clean(r["Sell-Vol"]),
                          _clean(r["Sell-Price"]), _clean(r["Buy-Price"]),
                          _clean(r["Buy-Vol"]), _clean(r["Buy-No"])))
        conn = db.get_db()
        try:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE order_book")
                psycopg2.extras.execute_values(
                    cur,
                    f'INSERT INTO order_book ({", ".join(_OB_COLUMNS)}) VALUES %s',
                    brows, page_size=1000)
            conn.commit()
            n += len(brows)
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise StoreError(f"order book: {type(e).__name__}: {e}") from e
        finally:
            db.release(conn)
    return n


# ---------------------------------------------------------------------------
# فهرست مرجع نمادها — Build_Market_StockList
#
# The `stocks` table is where every sector filter, every group heading and the
# designer's «اطلاعات نماد» blocks read from, and it was loaded once by hand:
# 810 rows against 1,543 symbols on the live board. This refreshes it in place.
#
# UPSERT, NEVER DELETE. stockpricehistory.stock_id references these rows and two
# million price rows hang off them; a symbol that has left the exchange keeps
# its row and its history, and simply stops being updated. Removing delistings
# is a data decision with consequences, not a side effect of a refresh.
# ---------------------------------------------------------------------------
_LIST_COLUMNS = ("ticker", "name", "market", "panel", "sector", "sub_sector",
                 "comment", "name_en", "company_code", "ticker_4", "ticker_5",
                 "ticker_12", "sector_code", "sub_sector_code", "panel_code")

_LIST_SOURCE = ("Ticker", "Name", "Market", "Panel", "Sector", "Sub-Sector",
                "Comment", "Name(EN)", "Company Code(12)", "Ticker(4)",
                "Ticker(5)", "Ticker(12)", "Sector Code", "Sub-Sector Code",
                "Panel Code")


def _symbols_dataset(kind, entity_id, ticker, start, end, full):
    fpy, pd = _finpy()
    try:
        raw = fpy.Build_Market_StockList(
            bourse=True, farabourse=True, payeh=True,
            # The detailed pass is what carries Sub-Sector, the codes and the
            # panel — the columns this table exists for. It is also the slow
            # half; without it the refresh would add nothing `stocks` does not
            # already have.
            detailed_list=True, show_progress=False,
            save_excel=False, save_csv=False)
    except Exception as e:
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e

    df = pd.DataFrame(raw)
    if df.empty:
        raise NoDataError("Build_Market_StockList returned nothing")
    df = df.reset_index()
    _require(df, _LIST_SOURCE, "Build_Market_StockList")

    rows, seen = [], set()
    for _, r in df.iterrows():
        vals = tuple(_clean(r[c]) for c in _LIST_SOURCE)
        tk = vals[0]
        if not tk or tk in seen:
            continue
        seen.add(tk)
        rows.append(vals)
    if not rows:
        raise NoDataError("Build_Market_StockList produced no usable rows")

    # `stocks.ticker` has no unique constraint in the baseline schema, so an
    # ON CONFLICT has nothing to name. One statement per row would be 1,500
    # round trips; instead the whole batch lands in a temp table and two set
    # operations do the work.
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cols = ", ".join(_LIST_COLUMNS)
            cur.execute(
                "CREATE TEMP TABLE _sl (LIKE stocks INCLUDING DEFAULTS) "
                "ON COMMIT DROP")
            psycopg2.extras.execute_values(
                cur, f"INSERT INTO _sl ({cols}) VALUES %s", rows, page_size=500)
            sets = ", ".join(f"{c} = s.{c}" for c in _LIST_COLUMNS if c != "ticker")
            cur.execute(f"UPDATE stocks t SET {sets} FROM _sl s "
                        f"WHERE t.ticker = s.ticker")
            updated = cur.rowcount
            cur.execute(
                f"INSERT INTO stocks ({cols}) "
                f"SELECT {cols} FROM _sl s "
                f"WHERE NOT EXISTS (SELECT 1 FROM stocks t WHERE t.ticker = s.ticker)")
            added = cur.rowcount
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        raise StoreError(f"{type(e).__name__}: {e}") from e
    finally:
        db.release(conn)
    return updated + added


# ===========================================================================
# THE INTRADAY LAYER — عمق بازار، ریز معاملات، سابقهٔ صف
#
# WHY THIS IS OURS AND NOT finpy's
#
# All three of these go through finpy's `__Get_Day_LOB__` /
# `__Get_Day_MarketClose_BQ_SQ__`, and both are broken in the installed build
# in the same way: they rename TSETMC's order-book columns BY POSITION.
#
#   data.columns = ['Time','Depth','Buy_Vol','Buy_No','Buy_Price', …]   # 8 names
#
# TSETMC has since added a `title` column, so nine columns arrive where eight
# are expected and the assignment raises
# `ValueError: Length mismatch: Expected axis has 9 elements, new values have 8`.
# Both callers wrap the day in a bare `except:` and report the crash as
#
#   WARNING: The following days data is not available on TSE website…
#
# …which is why «سابقهٔ صف» and «عمق بازار» looked like missing upstream data.
# They are not: the raw endpoint returns 6,991 rows for a day finpy reports as
# empty. Verified against the API directly.
#
# Selecting BY NAME instead is both the fix and immune to the next column
# TSETMC adds. The names below are TSETMC's own:
#
#   hEven      HHMMSS of the quote        number     depth (1..5)
#   qTitMeDem  buy volume  (تقاضا)        zOrdMeDem  buy order count
#   pMeDem     buy price                  pMeOf      sell price
#   zOrdMeOf   sell order count           qTitMeOf   sell volume  (عرضه)
#
# One more thing worth stating: finpy's queue function also assigns
# `psGelStaMax` (the UPPER band) to `Day_LL` and the lower band to `Day_UL` —
# they are swapped in its output. Ours are not.
# ===========================================================================
_LOB_COLS = {"hEven": "t", "number": "depth",
             "qTitMeDem": "buy_vol", "zOrdMeDem": "buy_no", "pMeDem": "buy_price",
             "pMeOf": "sell_price", "zOrdMeOf": "sell_no", "qTitMeOf": "sell_vol"}

#: The trading window a quote has to fall in to be kept. 08:45 → 12:30 is the
#: session; anything outside it is pre-open noise or the post-close tail.
_LOB_FROM, _LOB_TO = 84500, 123000

_TSE_API = "http://cdn.tsetmc.com/api"


def _web_id(ticker):
    """TSETMC's internal instrument id for a symbol.

    finpy's lookup is reused rather than reimplemented — that part of it works,
    and it carries the market/active bookkeeping that picks the CURRENT
    instrument when a symbol has been reissued."""
    fpy, _pd = _finpy()
    resolver = None
    for name, obj in vars(fpy).items():
        if "Get_TSE_WebID" in name and callable(obj):
            resolver = obj
            break
    if resolver is None:
        raise TransientFetchError("finpy_tse has no web-id resolver")
    try:
        df = resolver(ticker)
    except Exception as e:
        raise TransientFetchError(f"web-id lookup failed: {type(e).__name__}: {e}") from e
    if df is None or isinstance(df, bool) or not len(df):
        raise NoDataError(f"TSETMC does not know the symbol {ticker!r}")
    df = df.reset_index()
    if "Active" in df.columns:
        active = df[df["Active"] == 1]
        if len(active):
            df = active
    return int(df["WEB-ID"].values[0])


def _api_json(url, what):
    fpy, _pd = _finpy()
    import requests
    try:
        r = requests.get(url, headers=fpy.headers, timeout=_http_timeout())
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise TransientFetchError(f"{what}: {type(e).__name__}: {e}") from e


def _greg(jdate):
    """'1405-06-04' → '20260826', the format the intraday endpoints take."""
    import jdatetime
    y, m, d = (int(x) for x in str(jdate).split("-"))
    g = jdatetime.date(y, m, d).togregorian()
    return f"{g.year:04d}{g.month:02d}{g.day:02d}", g


def _day_bands(web_id, greg):
    """(day_ul, day_ll) — the session's permitted price band."""
    j = _api_json(f"{_TSE_API}/MarketData/GetStaticThreshold/{web_id}/{greg}",
                  "price band")
    rows = j.get("staticThreshold") or []
    if not rows:
        return None, None
    last = rows[-1]
    return last.get("psGelStaMax"), last.get("psGelStaMin")


def _hms(h_even):
    """TSETMC's HHMMSS integer → a datetime.time. Five digits means H:MM:SS."""
    import datetime
    s = str(int(h_even)).zfill(6)
    try:
        return datetime.time(int(s[:2]), int(s[2:4]), int(s[4:6]))
    except ValueError:
        return None


def _day_lob(web_id, jdate):
    """[(time, seq, depth, sell_no, sell_vol, sell_price, buy_price, buy_vol,
    buy_no)] for one symbol-day, plus (day_ul, day_ll). Empty list when TSETMC
    has no tape for that day, which is a real and common answer."""
    greg, _g = _greg(jdate)
    day_ul, day_ll = _day_bands(web_id, greg)
    j = _api_json(f"{_TSE_API}/BestLimits/{web_id}/{greg}", "order book")
    rows = j.get("bestLimitsHistory") or []
    if not rows:
        return [], day_ul, day_ll

    keep = []
    for r in rows:
        t = r.get("hEven")
        if t is None or not (_LOB_FROM <= int(t) < _LOB_TO):
            continue
        hms = _hms(t)
        if hms is None:
            continue
        keep.append((hms, int(t), {k: r.get(k) for k in _LOB_COLS}))
    # Sorted by (time, depth) so `seq` below numbers repeats deterministically —
    # a re-fetch has to produce the same key for the same quote or the upsert
    # would append instead of replace.
    keep.sort(key=lambda x: (x[1], x[2].get("number") or 0))

    out, seen = [], {}
    for hms, _raw_t, r in keep:
        depth = int(r.get("number") or 0)
        key = (hms, depth)
        seq = seen.get(key, 0)
        seen[key] = seq + 1
        out.append((hms, seq, depth,
                    r.get("zOrdMeOf"), r.get("qTitMeOf"), r.get("pMeOf"),
                    r.get("pMeDem"), r.get("qTitMeDem"), r.get("zOrdMeDem")))
    return out, day_ul, day_ll


def _trading_jdates(kind, ticker, start, end):
    """The sessions to fetch, taken from the PRICE table.

    Not from a calendar and not from TSETMC: the price table already knows
    exactly which days this symbol traded, so a symbol halted for three weeks
    costs three weeks fewer requests, and a public holiday is never asked for.
    That matters here more than anywhere else in this file — these datasets are
    one request PER DAY."""
    cfg = KINDS[kind]
    tbl = "stockpricehistory" if cfg.get("for_kind", "stock") == "stock" \
        else "etfpricehistory"
    rows = db._rows(
        f"""SELECT j_date, date FROM {tbl}
             WHERE ticker = %s AND j_date >= %s AND j_date <= %s
             ORDER BY date""", (ticker, start, end))
    return [(r["j_date"], r["date"]) for r in rows]


# ---------------------------------------------------------------------------
# سابقهٔ ارزش صف — one row per session
# ---------------------------------------------------------------------------
_QUEUE_COLUMNS = ("kind", "entity_id", "ticker", "j_date", "date", "weekday",
                  "board_time", "day_ul", "day_ll", "value",
                  "bq_value", "sq_value", "bqpc", "sqpc")


def _queue_of_day(lob, day_ul, day_ll):
    """(board_time, bq, sq, bqpc, sqpc) from the last quote of the session.

    A BUY queue exists only when the best bid is sitting ON the upper band —
    that is what «صف خرید» means: everyone wants it at the maximum permitted
    price and nobody will sell there. Symmetrically for the sell queue at the
    lower band. Anything else is an ordinary spread and both values are zero,
    which is a real answer rather than a missing one.
    """
    best = [q for q in lob if q[2] == 1]
    if not best:
        return None, 0, 0, 0, 0
    t, _seq, _d, sell_no, sell_vol, sell_price, buy_price, buy_vol, buy_no = best[-1]
    bq = sq = bqpc = sqpc = 0
    if day_ul is not None and buy_price == day_ul and buy_vol:
        bq = int(day_ul * buy_vol)
        bqpc = int(round(bq / buy_no)) if buy_no else 0
    if day_ll is not None and sell_price == day_ll and sell_vol:
        sq = int(day_ll * sell_vol)
        sqpc = int(round(sq / sell_no)) if sell_no else 0
    return t.strftime("%H:%M:%S"), bq, sq, bqpc, sqpc


def _queue_dataset(kind, entity_id, ticker, start, end, full):
    cfg = KINDS[kind]
    ri_kind = cfg.get("for_kind", "stock")
    days = _trading_jdates(kind, ticker, "1300-01-01" if full else start,
                           "1499-12-29" if full else end)
    if not days:
        raise NoDataError(f"no price sessions for {ticker} in {start}..{end}")
    web_id = _web_id(ticker)

    rows, misses = [], 0
    for jd, gd in days:
        try:
            lob, day_ul, day_ll = _day_lob(web_id, jd)
        except FetchError:
            misses += 1
            continue
        if not lob:
            misses += 1
            continue
        t, bq, sq, bqpc, sqpc = _queue_of_day(lob, day_ul, day_ll)
        rows.append((ri_kind, entity_id, ticker, jd, gd, None, t,
                     day_ul, day_ll, None, bq, sq, bqpc, sqpc))
    if not rows:
        # Every day came back empty. TSETMC genuinely does not keep the
        # intraday tape for every session, so this is reported as "no data in
        # the window" rather than as a failure of the symbol.
        raise NoDataError(f"TSETMC has no intraday tape for {ticker} "
                          f"on any of the {len(days)} sessions in range")
    return _replace("queue_history", ("ticker", "date"), _QUEUE_COLUMNS, rows)


# ---------------------------------------------------------------------------
# عمق بازار درون‌روز — the whole tape
# ---------------------------------------------------------------------------
_IOB_COLUMNS = ("ticker", "j_date", "date", "time", "seq", "depth",
                "sell_no", "sell_vol", "sell_price",
                "buy_price", "buy_vol", "buy_no", "day_ul", "day_ll")


def _intraday_ob_dataset(kind, entity_id, ticker, start, end, full):
    days = _trading_jdates(kind, ticker, "1300-01-01" if full else start,
                           "1499-12-29" if full else end)
    if not days:
        raise NoDataError(f"no price sessions for {ticker} in {start}..{end}")
    web_id = _web_id(ticker)
    written = 0
    for jd, gd in days:
        try:
            lob, day_ul, day_ll = _day_lob(web_id, jd)
        except FetchError:
            continue
        if not lob:
            continue
        rows = [(ticker, jd, gd, t, seq, depth, s_no, s_vol, s_px,
                 b_px, b_vol, b_no, day_ul, day_ll)
                for (t, seq, depth, s_no, s_vol, s_px, b_px, b_vol, b_no) in lob]
        # Written PER DAY rather than accumulated: a month of tape for a liquid
        # symbol is ~150,000 rows, and holding all of it in memory to write once
        # buys nothing — while writing as we go means a run interrupted half-way
        # keeps the days it already finished.
        written += _replace("intraday_orderbook",
                            ("ticker", "date", "time", "seq", "depth"),
                            _IOB_COLUMNS, rows)
    if not written:
        raise NoDataError(f"TSETMC has no order-book tape for {ticker} in range")
    return written


# ---------------------------------------------------------------------------
# ریز معاملات — every executed trade
# ---------------------------------------------------------------------------
_ITR_COLUMNS = ("ticker", "j_date", "date", "time", "seq", "volume", "price",
                "canceled")


def _intraday_trades_dataset(kind, entity_id, ticker, start, end, full):
    days = _trading_jdates(kind, ticker, "1300-01-01" if full else start,
                           "1499-12-29" if full else end)
    if not days:
        raise NoDataError(f"no price sessions for {ticker} in {start}..{end}")
    web_id = _web_id(ticker)
    written = 0
    for jd, gd in days:
        greg, _g = _greg(jd)
        try:
            j = _api_json(
                f"{_TSE_API}/Trade/GetTradeHistory/{web_id}/{greg}/false",
                "trades")
        except FetchError:
            continue
        raw = j.get("tradeHistory") or []
        if not raw:
            continue
        # `nTran` is TSETMC's own per-trade sequence number, so it is used as
        # `seq` where present: two trades in the same second are ordinary, and
        # a positional counter would renumber them on a re-fetch.
        rows, seen = [], {}
        for r in raw:
            t = _hms(r.get("hEven"))
            if t is None:
                continue
            seq = r.get("nTran")
            if seq is None:
                seq = seen.get(t, 0)
                seen[t] = seq + 1
            # qTitTran / pTran are TSETMC's own names for the executed volume
            # and price. `canceled` is KEPT rather than filtered out: the
            # exchange does cancel erroneous trades, they are part of the tape,
            # and a reader summing volume must be able to exclude them — which
            # they cannot do if we dropped them silently. Reads exclude them by
            # default (market_data.trades).
            rows.append((ticker, jd, gd, t, int(seq),
                         r.get("qTitTran"), r.get("pTran"),
                         bool(r.get("canceled"))))
        if rows:
            written += _replace("intraday_trades",
                                ("ticker", "date", "time", "seq"),
                                _ITR_COLUMNS, rows)
    if not written:
        raise NoDataError(f"TSETMC has no trade tape for {ticker} in range")
    return written


# ---------------------------------------------------------------------------
# سهامداران عمده — holders above 1%
# ---------------------------------------------------------------------------
def _read_html_str_shim():
    """Let finpy's `pd.read_html(html_string)` work on pandas ≥ 2.1.

    That call is how Get_ShareHoldersInfo reaches the symbol's ISIN, and modern
    pandas no longer accepts a raw HTML string there — it treats the argument as
    a path and raises `FileNotFoundError: [Errno 2] … <!doctype html>…`, which is
    why the function fails outright in this build.

    Wrapping rather than reimplementing: everything else in that function is
    correct, and this is the smallest change that makes it correct too. Applied
    once and left in place, like the HTTP timeout above.
    """
    import io as _io
    import pandas as pd
    if getattr(pd.read_html, "_bn_str_ok", False):
        return
    original = pd.read_html

    def read_html(io_arg, *args, **kwargs):
        if isinstance(io_arg, str) and "<" in io_arg[:2048]:
            io_arg = _io.StringIO(io_arg)
        return original(io_arg, *args, **kwargs)

    read_html._bn_str_ok = True
    pd.read_html = read_html
    try:
        import finpy_tse
        finpy_tse.pd.read_html = read_html
    except Exception:
        pass


_SH_COLUMNS = ("ticker", "holder", "market", "share_no", "share_pct",
               "changes", "j_date")


def _shareholders_dataset(kind, entity_id, ticker, start, end, full):
    import jdatetime
    import datetime
    fpy, pd = _finpy()
    _read_html_str_shim()
    try:
        raw = fpy.Get_ShareHoldersInfo(ticker=ticker)
    except Exception as e:
        raise TransientFetchError(f"{type(e).__name__}: {e}") from e
    if raw is None or not len(raw):
        raise NoDataError(f"no shareholders listed for {ticker}")

    df = raw.reset_index()
    _require(df, ("Ticker", "Market", "Name", "ShareNo", "SharePct", "Changes"),
             "Get_ShareHoldersInfo")
    today = datetime.date.today()
    jd = str(jdatetime.date.fromgregorian(
        year=today.year, month=today.month, day=today.day))

    rows, seen = [], set()
    for _, r in df.iterrows():
        holder = _clean(r["Name"])
        if not holder or holder in seen:
            continue
        seen.add(holder)
        rows.append((ticker, holder, _clean(r["Market"]), _clean(r["ShareNo"]),
                     _clean(r["SharePct"]), _clean(r["Changes"]), jd))
    if not rows:
        raise NoDataError(f"no usable shareholder rows for {ticker}")

    # DELETE then insert, unlike every other dataset here: the source page shows
    # only who holds the symbol TODAY, so a holder who has dropped below 1% must
    # disappear. An upsert would leave them on the list for ever.
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM shareholders WHERE ticker = %s", (ticker,))
            psycopg2.extras.execute_values(
                cur,
                f'INSERT INTO shareholders ({", ".join(_SH_COLUMNS)}) VALUES %s',
                rows, page_size=200)
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


_HANDLERS = {
    "price": _price_dataset,
    "ri": _ri_dataset,
    "index": _index_dataset,
    "usd": _usd_dataset,
    "watch": _watch_dataset,
    "symbols": _symbols_dataset,
    "queue": _queue_dataset,
    "intraday_ob": _intraday_ob_dataset,
    "intraday_trades": _intraday_trades_dataset,
    "shareholders": _shareholders_dataset,
}
