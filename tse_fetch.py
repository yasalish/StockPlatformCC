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


def fetch(kind, ticker, start, end, full=False):
    """Pull one symbol's history from TSETMC. Returns a DataFrame.

    finpy_tse is imported lazily: the web process only needs to ENQUEUE jobs, and
    importing finpy there would pull in aiohttp and touch the event loop for no
    reason. Only the Celery worker actually calls this."""
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
