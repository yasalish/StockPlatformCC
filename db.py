"""
db.py — لایهٔ داده و تحلیل «بورس‌نگار»
Data + analytics layer for the Tehran Stock Exchange platform, backed by the
existing PostgreSQL database «Stock» (tables: stocks, stockpricehistory, etf,
etfpricehistory) — the same DB the original scripts (stock_updater.py,
etf_updater.py, stock_gainer.py, etf_gainer.py, search.py) read and write.

All connection settings live HERE, once — instead of being copy-pasted into a
dozen scripts. The analytics mirror the logic of those scripts:

    gain  = (adj_final_latest - adj_final_past) / adj_final_past * 100
    ceil  (سقف)  = (adj_final_latest - max_over_period) / max_over_period * 100
    floor (کف)   = (adj_final_latest - min_over_period) / min_over_period * 100

Periods are measured in TRADING DAYS (rows), exactly like
search.py::calculate_period_gains — deterministic and index-friendly.
"""
import os
import time
import threading
import psycopg2
import psycopg2.extras
import psycopg2.pool

try:                                  # load .env if python-dotenv is installed
    from dotenv import load_dotenv     # (app.py does this too — load_dotenv is
    load_dotenv()                      # idempotent, and this keeps `import db`
except ImportError:                    # working from scripts and `python -c`)
    pass

# NOTE: this MUST stay above `import cache`. cache.py reads its Redis settings
# into module constants at import time, so importing it before .env is loaded
# would silently pin them to the built-in defaults.
import analytics_views
import cache                               # Redis-backed analytics cache

import observability
log = observability.get_logger("boursenegar.db")

# ---------------------------------------------------------------------------
# Connection — one place, override with env vars if you like
#
# The password has NO default. It used to fall back to a literal value written
# in this file, which meant the production credential lived in source control
# (and is why that credential should now be considered compromised) and a
# container started with the variable missing would silently connect to whatever
# accepted that password instead of failing. It is required now, and a missing
# value stops the process at import with an actionable message — loudly, at
# startup, rather than on the first request that happens to touch the database.
# ---------------------------------------------------------------------------
def _required_env(name, hint):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set — refusing to start.\n"
            f"  {hint}\n"
            f"  Local development: put it in .env next to app.py (see .env.example).\n"
            f"  Docker/production: set it in deploy/.env; docker-compose.yml passes "
            f"it through to the container."
        )
    return value


DB_SETTINGS = {
    "dbname": os.environ.get("STOCK_DB_NAME", "Stock"),
    "user": os.environ.get("STOCK_DB_USER", "postgres"),
    "password": _required_env(
        "STOCK_DB_PASSWORD",
        "It is the password for the PostgreSQL role in STOCK_DB_USER."),
    "host": os.environ.get("STOCK_DB_HOST", "localhost"),
    "port": os.environ.get("STOCK_DB_PORT", "5432"),
}

# ---------------------------------------------------------------------------
# Connection pool — reuse a handful of PostgreSQL connections instead of paying
# a fresh TCP + auth handshake on EVERY query. A single page load fires many
# queries, so pooling removes a lot of per-request latency. Created lazily (and
# thread-safely) on first use, so importing this module never needs the DB up.
# ---------------------------------------------------------------------------
_POOL = None
_POOL_LOCK = threading.Lock()


def _pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = psycopg2.pool.ThreadedConnectionPool(
                    minconn=1,
                    maxconn=int(os.environ.get("STOCK_DB_POOL_MAX", "10")),
                    **DB_SETTINGS,
                )
    return _POOL


def get_db():
    """Borrow a connection from the pool. Every call MUST be paired with
    release(conn) — the try/finally blocks throughout this module do exactly
    that (they used to call conn.close(); release() returns it to the pool)."""
    return _pool().getconn()


def release(conn):
    """Return a borrowed connection to the pool. Rolls back first so no
    idle-in-transaction session lingers holding locks (a no-op after commit)."""
    if conn is None:
        return
    try:
        conn.rollback()
    except Exception:
        pass
    try:
        _pool().putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


def ensure_indexes():
    """Guarantee a (ticker, date DESC) INCLUDE (adj_final) index on each price
    table — the single-ticker detail / chart / datafeed lookups depend on it, and
    the INCLUDE makes them index-only scans that never touch the heap.

    This replaced the old (ticker, j_date) pair: once every range filter and
    ORDER BY moved onto the real `date` column those were never chosen again
    (measured: 0 scans across a full workload) and were dropped. Recreating them
    here would silently hand back the 151 MB they cost, so this function must
    stay in step with the schema.

    (The market-wide gainer / scan pages are NOT index-bound — they read a ~2-year
    slice of every ticker — so their speed comes from the materialized views, not
    from indexes.) Best-effort: a failure here (e.g. a read-only role) is logged,
    never fatal."""
    tables = [("stockpricehistory", "ix_sph_ticker_date"),
              ("etfpricehistory", "ix_eph_ticker_date")]
    conn = get_db()
    try:
        conn.autocommit = True                # CREATE INDEX commits per statement
        with conn.cursor() as cur:
            for table, name in tables:
                try:
                    # Skip if any btree index already leads with (ticker, date).
                    cur.execute(
                        "SELECT 1 FROM pg_indexes WHERE tablename = %s "
                        "AND indexdef ILIKE %s LIMIT 1",
                        (table, "%(ticker, date%"))
                    if cur.fetchone():
                        continue
                    cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} "
                                f"(ticker, date DESC) INCLUDE (adj_final)")
                    log.info("created index", extra={"index": name, "table": table})
                except Exception as e:        # missing table / no permission → skip
                    log.warning("index check skipped", extra={"table": table, "error": str(e)})
    finally:
        conn.autocommit = False
        release(conn)


# ---------------------------------------------------------------------------
# Materialized analytics — the market-wide tables live in the DATABASE now
#
# warm_cache() used to live here: a background thread that pre-computed all of
# this into per-process RAM on every start. It is gone. The same numbers are now
# materialized views, built once per data update and shared by every worker
# process, so there is nothing to warm and no N-workers-N-copies problem.
# The SQL is in analytics_views.py, which documents how it stays bit-exact with
# the Python it replaced.
# ---------------------------------------------------------------------------
_ANALYTICS_VIEWS = None
_VIEWS_READY = None          # None = not checked yet; True/False = last answer


def analytics_catalogue():
    """[(view, ddl, unique_cols)] in dependency order. Built lazily because the
    DDL is generated from PERIODS / CALC_PERIODS / PERF_PERIODS / SCORE_WEIGHTS,
    which are defined further down this module — generating it from those means
    the views can never drift from the Python definitions."""
    global _ANALYTICS_VIEWS
    if _ANALYTICS_VIEWS is None:
        _ANALYTICS_VIEWS = analytics_views.all_views(
            PERIODS, CALC_PERIODS, PERF_PERIODS, SCORE_WEIGHTS)
    return _ANALYTICS_VIEWS


def analytics_ready(force=False):
    """True when every analytics view exists AND is populated.

    Cached, because it is consulted on every read. A database that has never run
    ensure_analytics_views() answers False and every reader quietly falls back to
    computing in Python — that is the migration-safe path: the app starts and
    serves correctly against a database where the views do not exist yet, just
    more slowly."""
    global _VIEWS_READY
    if _VIEWS_READY is None or force:
        names = [n for n, _, _ in analytics_catalogue()]
        try:
            have = {r["matviewname"] for r in _rows(
                "SELECT matviewname FROM pg_matviews "
                "WHERE matviewname = ANY(%s) AND ispopulated", (names,))}
            _VIEWS_READY = len(have) == len(names)
        except Exception as e:               # unreachable DB → behave as "absent"
            log.warning("analytics_ready check failed", extra={"error": str(e)})
            _VIEWS_READY = False
    return _VIEWS_READY


def ensure_analytics_views(rebuild=False):
    """Create any missing analytics view (and its UNIQUE index). Idempotent, so
    it is safe to call on every start. `rebuild=True` drops and recreates them —
    use that after changing the period definitions or the SQL.

    The UNIQUE index is not optional: REFRESH MATERIALIZED VIEW CONCURRENTLY
    requires one, and CONCURRENTLY is what keeps readers unblocked during the
    nightly refresh."""
    conn = get_db()
    made = []
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for name, ddl, idx in analytics_catalogue():
                try:
                    if rebuild:
                        cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE")
                    cur.execute("SELECT 1 FROM pg_matviews WHERE matviewname = %s", (name,))
                    if cur.fetchone():
                        continue
                    t0 = time.time()
                    cur.execute(ddl)
                    cur.execute(f"CREATE UNIQUE INDEX ux_{name} ON {name} ({idx})")
                    made.append(name)
                    log.info("built materialized view", extra={"view": name, "seconds": round(time.time() - t0, 1)})
                except Exception as e:
                    log.error("could not build materialized view", extra={"view": name, "error": str(e)})
                    raise
    finally:
        conn.autocommit = False
        release(conn)
    analytics_ready(force=True)
    return made


def refresh_analytics(concurrently=True):
    """Rebuild every analytics view, in dependency order, after a data update.

    analytics_catalogue() is ordered bars -> indicators -> derived tables, so each view
    is refreshed only once its inputs already hold the new prices. CONCURRENTLY
    means readers keep seeing the previous contents until each swap completes and
    are never blocked — at the cost of needing the UNIQUE index above.

    A view that does not exist yet is created first, so the first data update on
    a fresh database also bootstraps the analytics. Returns per-view seconds."""
    ensure_analytics_views()
    timings = {}
    conn = get_db()
    try:
        conn.autocommit = True               # REFRESH CONCURRENTLY cannot run in a txn
        with conn.cursor() as cur:
            for name, _, _ in analytics_catalogue():
                t0 = time.time()
                try:
                    mode = "CONCURRENTLY " if concurrently else ""
                    cur.execute(f"REFRESH MATERIALIZED VIEW {mode}{name}")
                    timings[name] = round(time.time() - t0, 2)
                except Exception as e:
                    log.error("materialized view refresh failed", extra={"view": name, "error": str(e)})
                    timings[name] = None
    finally:
        conn.autocommit = False
        release(conn)
    clear_cache()                            # per-process caches now hold stale rows
    log.info("refresh_analytics complete", extra={"seconds": round(sum(v for v in timings.values() if v), 1), "views": len(timings)})
    return timings


def _latest_cached(kind):
    """latest_date() memoised for the request path — it is consulted on every
    read to decide whether the views cover the requested as_of."""
    return cache.get_or_set("latest", ("__latest__", kind),
                            lambda: latest_date(kind))


def _use_view(kind, as_of):
    """The views are materialized for ONE as_of: the latest trading date. A
    caller asking for a historical date (e.g. /stocks?as_of=…) still gets the
    live computation."""
    if not analytics_ready():
        return False
    return as_of is None or as_of == _latest_cached(kind)


def _rows(sql, params=()):
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        release(conn)


def _one(sql, params=()):
    r = _rows(sql, params)
    return r[0] if r else None

# ---------------------------------------------------------------------------
# Persian helpers (digits / formatting)
# ---------------------------------------------------------------------------
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_persian(n):
    """Render a number with Persian digits, thousands-grouped for big numbers."""
    if n is None:
        return "—"
    if isinstance(n, bool):
        return str(n)
    if isinstance(n, (int, float)):
        n = f"{n:,.0f}" if float(n).is_integer() else f"{n:,.2f}"
    return str(n).translate(_FA_DIGITS)


def to_persian_plain(n):
    """Persian digits with NO grouping — for years / small counts / dates."""
    if n is None:
        return "—"
    return str(n).translate(_FA_DIGITS)

# ---------------------------------------------------------------------------
# Periods (trading days). label is shown in the UI; `n` is the row offset.
# ---------------------------------------------------------------------------
PERIODS = [
    {"key": "p5", "n": 5, "label": "۱ هفته"},
    {"key": "p20", "n": 20, "label": "۱ ماه"},
    {"key": "p60", "n": 60, "label": "۳ ماه"},
    {"key": "p120", "n": 120, "label": "۶ ماه"},
    {"key": "p240", "n": 240, "label": "۱ سال"},
    {"key": "p360", "n": 360, "label": "۱۸ ماه"},
]

# Finer, day-based period set for the «محاسبهٔ بازدهٔ دوره‌ای» calculator panel on
# the stock / ETF pages (mirrors etf_gainer.py: 5,10,15,20,30,60,90,120,180,360
# trading days). Same FILTER-aggregation engine as PERIODS — just more columns.
CALC_PERIODS = [
    {"key": "d5",   "n": 5,   "label": "۵ روز"},
    {"key": "d10",  "n": 10,  "label": "۱۰ روز"},
    {"key": "d15",  "n": 15,  "label": "۱۵ روز"},
    {"key": "d20",  "n": 20,  "label": "۲۰ روز"},
    {"key": "d25",  "n": 25,  "label": "۲۵ روز"},
    {"key": "d30",  "n": 30,  "label": "۳۰ روز"},
    {"key": "d60",  "n": 60,  "label": "۶۰ روز"},
    {"key": "d90",  "n": 90,  "label": "۹۰ روز"},
    {"key": "d120", "n": 120, "label": "۱۲۰ روز"},
    {"key": "d180", "n": 180, "label": "۱۸۰ روز"},
    {"key": "d360", "n": 360, "label": "۳۶۰ روز"},
]

# Multi-period performance set for the «بازدهٔ بازه» (/performance) page — the
# fixed windows the old Streamlit stock_gain analyzer showed, each with its own
# gain / سقف (ceil) / کف (floor). Trading-day based so it matches the rest of the
# app; the special «از ابتدا» (from-first) column is handled separately.
PERF_PERIODS = [
    {"key": "w1",  "n": 5,   "label": "۱ هفته"},
    {"key": "m1",  "n": 20,  "label": "۱ ماه"},
    {"key": "m3",  "n": 60,  "label": "۳ ماه"},
    {"key": "m6",  "n": 120, "label": "۶ ماه"},
    {"key": "m9",  "n": 180, "label": "۹ ماه"},
    {"key": "y1",  "n": 240, "label": "۱ سال"},
    {"key": "y1h", "n": 360, "label": "۱۸ ماه"},
    {"key": "y2",  "n": 480, "label": "۲ سال"},
    {"key": "y3",  "n": 720, "label": "۳ سال"},
]

ETF_TYPE_COLORS = {
    "ثابت": "#2f6db3",
    "در سهام": "#1a9d63",
    "کالا": "#e08a1e",
    "مختلط": "#7a4fb3",
    "املاک و مستغلات": "#b3452f",
    "صندوق در صندوق": "#0f8a8a",
    "خصوصی": "#5f6b7a",
    "تضمین اصل": "#3f8f7a",
}

# ---------------------------------------------------------------------------
# Summary / meta
# ---------------------------------------------------------------------------
def latest_date(kind="stock"):
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    r = _one(f"SELECT MAX(j_date) d FROM {tbl}")
    return r["d"] if r else None


def recent_trading_dates(kind="stock", n=1, as_of=None):
    """The `n` most recent distinct Jalali trading dates (newest first), optionally
    ending at/at-or-before `as_of`. Used to seed a sensible default از/تا window."""
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = _rows(
        f"SELECT DISTINCT j_date, date FROM {tbl} "
        f"WHERE (%s::date IS NULL OR date <= %s) ORDER BY date DESC LIMIT %s",
        (_date_for(kind, as_of, "hi"), _date_for(kind, as_of, "hi"), n))
    return [r["j_date"] for r in rows]


def db_summary():
    """Dashboard header counts. The two COUNT(*)s scan the ENTIRE price-history
    tables (millions of rows, no index helps a full COUNT), so this is cached like
    every other analytic — the numbers only change on a data update, when
    clear_cache() runs. Without the cache this ran on every dashboard load."""
    def build():
        s = _one(
            """SELECT
                 (SELECT COUNT(*) FROM stocks) AS stocks,
                 (SELECT COUNT(*) FROM etf)    AS etfs,
                 (SELECT COUNT(*) FROM stockpricehistory) AS stock_rows,
                 (SELECT COUNT(*) FROM etfpricehistory)   AS etf_rows"""
        )
        s["stock_latest"] = latest_date("stock")
        s["etf_latest"] = latest_date("etf")
        return s

    return cache.get_or_set("summary", ("__summary__",), build)


def _cutoff(as_of, years=2):
    """A Jalali date ~`years` before as_of — used to slice recent history so the
    window queries stay fast (max lookback is 360 trading days ≈ 1.5y).

    Still Jalali: it is the *definition* of the window. _cutoff_date() below
    translates it onto the real `date` column, which is what actually gets
    compared per row."""
    try:
        y = int(as_of[:4]) - years
        return f"{y:04d}{as_of[4:]}"
    except Exception:
        return "1400-01-01"


# ---------------------------------------------------------------------------
# Jalali ↔ Gregorian window bounds
#
# j_date is varchar(10). Comparing it per row means a string comparison over
# millions of rows, and no index on it can serve a market-wide scan. Every range
# filter and every ORDER BY therefore runs on the real `date` column instead;
# j_date is now display-only.
#
# That substitution is exact, not approximate: verify_schema.py asserts that
# `date` is never NULL, that j_date ↔ date is 1:1, and that sorting by one gives
# the identical sequence to sorting by the other. Given that, translating the
# WINDOW BOUNDS through the same calendar selects exactly the same rows.
# The bounds are scalars resolved once per query and cached, so the per-row work
# is a plain 4-byte date comparison.
# ---------------------------------------------------------------------------
def _date_for(kind, jdate, side):
    """Map a Jalali bound onto the date axis.
      side="lo": first trading date at/after `jdate`  (twin of j_date >= jdate)
      side="hi": last trading date at/before `jdate`  (twin of j_date <= jdate)
    Returns None when the table has nothing on that side of the bound."""
    if jdate is None:
        return None
    def build():
        tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
        if side == "lo":
            r = _one(f"SELECT MIN(date) d FROM {tbl} WHERE j_date >= %s", (jdate,))
        else:
            r = _one(f"SELECT MAX(date) d FROM {tbl} WHERE j_date <= %s", (jdate,))
        return r["d"] if r else None

    # A datetime.date, not a row dict — the tagged JSON codec round-trips it.
    return cache.get_or_set("dateof", ("__dateof__", kind, jdate, side), build)


def _window(kind, as_of, years=2):
    """(as_of_date, cutoff_date) — the Gregorian twins of the (as_of, _cutoff())
    pair every window query used to compare against j_date."""
    return _date_for(kind, as_of, "hi"), _date_for(kind, _cutoff(as_of, years), "lo")

# ---------------------------------------------------------------------------
# Market-wide gainer (the heart of stock_gainer.py / etf_gainer.py)
# ---------------------------------------------------------------------------
def _gainer(kind, as_of=None, periods=PERIODS, sort_key="p20"):
    """Return EVERY ticker with its % gain over each period as of `as_of`
    (latest trading date if None). One fast query using FILTER aggregation over
    a recent slice of the price table. `periods` selects which offsets to compute
    (defaults to the coarse PERIODS shown in the market table; the calculator
    passes the finer CALC_PERIODS). `sort_key` is the period key rows sort by.

    Deliberately UNFILTERED. This function used to take market / etf_type /
    sector / sub_sector too, but the SQL below never referenced them — they were
    applied in a Python loop over `meta` AFTER the full table scan had already
    run. So the query is byte-identical for every filter combination, and one
    result can serve all of them. Narrowing now happens in market_gainer(),
    against the cached list. See _gainer_all()."""
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return [], None

    filters = ",\n".join(
        f"MAX(v) FILTER (WHERE rn={p['n'] + 1}) {p['key']}" for p in periods
    )
    sql = f"""
    WITH ranked AS (
        SELECT ticker, adj_final::float v, j_date,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
        FROM {price_tbl}
        WHERE adj_final > 0 AND date <= %s AND date >= %s
    )
    SELECT ticker,
           MAX(j_date) FILTER (WHERE rn=1) AS ldate,
           MAX(v)      FILTER (WHERE rn=1) AS latest,
           {filters}
    FROM ranked
    GROUP BY ticker
    HAVING MAX(v) FILTER (WHERE rn=1) IS NOT NULL
    """
    price_rows = _rows(sql, _window(kind, as_of))
    price = {r["ticker"]: r for r in price_rows}

    # Join to the reference table (name / market / sector / sub_sector / type / id)
    if kind == "stock":
        meta = _rows("SELECT stockid AS id, ticker, name, market, sector, sub_sector FROM stocks")
    else:
        meta = _rows("SELECT id, ticker, name, type FROM etf")

    out = []
    for m in meta:
        p = price.get(m["ticker"])
        if not p:
            continue
        row = {
            "id": m["id"], "ticker": m["ticker"], "name": m["name"],
            "market": m.get("market"), "sector": m.get("sector"),
            "sub_sector": m.get("sub_sector"), "type": m.get("type"),
            "latest": p["latest"], "ldate": p["ldate"],
        }
        base = p["latest"]
        for per in periods:
            past = p.get(per["key"])
            row[per["key"]] = ((base - past) / past * 100.0) if past else None
        out.append(row)
    # default sort: biggest gainers over the chosen sort period first
    out.sort(key=lambda r: (r.get(sort_key) is None, -(r.get(sort_key) or 0)))
    return out, as_of


# The analytics cache lives in Redis now (cache.py). It used to be five
# module-level dicts here — _CACHE, _STRAT_CACHE, _FILTER_CACHE, _SCORE_CACHE and
# _MKT_CACHE — which meant every Gunicorn worker kept its own unbounded, never
# expiring copy and computed every scan independently. The call sites and their
# key tuples are unchanged; only the storage moved, plus a `namespace` argument
# that keeps the four scans which all key on (kind, as_of) from colliding now
# that they share one keyspace.


def _gainer_from_view(view, kind, periods, sort_key):
    """Read a pre-materialized gainer table. The percentages were computed by the
    view; the only work left is the sort, which SQL cannot express identically
    (python's sort is stable over the reference-table order). Ties are broken by
    `id` so the order is at least deterministic — the old code inherited the heap
    order of `stocks`, which is not."""
    cols = ", ".join(p["key"] for p in periods)
    rows = _rows(f"SELECT id, ticker, name, market, sector, sub_sector, type, "
                 f"latest, ldate, {cols} FROM {view}_{kind}")
    rows.sort(key=lambda r: (r.get(sort_key) is None, -(r.get(sort_key) or 0), r["id"]))
    return rows, _latest_cached(kind)


def _gainer_all(tag, kind, as_of, periods, sort_key, view=None):
    """The gainer table, cached ONCE per (tag, kind, as_of) — never per filter.

    Reads the materialized view when it covers the requested date, and otherwise
    falls back to computing it live (a historical as_of, or a database where the
    views have not been created yet).

    The cache key deliberately carries no filters. It used to: the old key was
    (kind, as_of, market, etf_type, sector, sub_sector), which made every filter
    click a guaranteed miss even though _gainer()'s SQL ignores the filters
    entirely. One unfiltered result serves all ~288 combinations; the callers
    below narrow it in memory."""
    def build():
        if view and _use_view(kind, as_of):
            return _gainer_from_view(view, kind, periods, sort_key)
        return _gainer(kind, as_of, periods=periods, sort_key=sort_key)

    # A (rows, as_of) tuple — the codec preserves the tuple, not just its items.
    return cache.get_or_set("gain", (tag, kind, as_of), build)


def market_gainer(kind, as_of=None, market=None, etf_type=None, sector=None,
                  sub_sector=None):
    """Every ticker with its per-period gains, optionally narrowed to one market /
    ETF type / industry group / sub-group. Signature and (rows, as_of) return
    shape are unchanged — only the caching moved: the scan is shared across all
    filter combinations and the narrowing is a few hundred dict lookups here
    (sub-millisecond) instead of a fresh scan per combination.

    Filtering the sorted full list rather than sorting a filtered list keeps the
    row order byte-identical to the old code (the sort is stable, and slicing
    preserves relative order)."""
    rows, as_of = _gainer_all("__gain__", kind, as_of, PERIODS, "p20",
                              view="mv_market_gainer")
    if not (market or etf_type or sector or sub_sector):
        return rows, as_of
    # The rows already carry market / sector / sub_sector / type straight off the
    # reference table, so this is the same comparison _gainer() used to make.
    out = [r for r in rows
           if (not market or r.get("market") == market)
           and (not sector or r.get("sector") == sector)
           and (not sub_sector or r.get("sub_sector") == sub_sector)
           and (not etf_type or r.get("type") == etf_type)]
    return out, as_of


def period_gainer(kind, as_of=None):
    """All tickers with their gains over the finer CALC_PERIODS set, for the
    «محاسبهٔ بازدهٔ دوره‌ای» calculator panel. Cached like market_gainer; sorted by
    the 20-day return so the ranked (blank-ticker) view is meaningful."""
    return _gainer_all("__calc__", kind, as_of, CALC_PERIODS, "d20",
                       view="mv_period_gainer")


# ---------------------------------------------------------------------------
# Custom date-window performance (از/تا) — gain + ceil + floor over an arbitrary
# Jalali range, for the from/to controls on the stock / ETF pages.
# ---------------------------------------------------------------------------
_FA_TO_ASCII = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")


def norm_jdate(s):
    """Normalise a user-entered Jalali date to the ASCII 'YYYY-MM-DD' form the DB
    stores (accepts Persian digits). Returns '' for blank input."""
    return (s or "").strip().translate(_FA_TO_ASCII)


def _range_gainer(kind, start, end, market=None, etf_type=None, sector=None):
    """Per-ticker performance over the custom Jalali window [start, end]:
        gain  = (last − first) / first          — return across the window
        ceil  (سقف: فاصله تا اوج) = (last − max) / max   (≤ 0)
        floor (کف:  فاصله تا کف)  = (last − min) / min   (≥ 0)
    where first/last are the earliest/latest closes INSIDE the window. j_date is
    an ASCII 'YYYY-MM-DD' Jalali string that sorts chronologically, so the same
    string comparison the trailing-period query relies on works here too."""
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    sql = f"""
    WITH r AS (
        SELECT ticker, adj_final::float v, j_date,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date ASC)  rn_a,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn_z
        FROM {price_tbl}
        WHERE adj_final > 0 AND date >= %s AND date <= %s
    )
    SELECT ticker,
           MAX(v) FILTER (WHERE rn_a = 1) AS first_v,
           MAX(v) FILTER (WHERE rn_z = 1) AS last_v,
           MIN(v) AS min_v, MAX(v) AS max_v,
           MIN(j_date) AS d_from, MAX(j_date) AS d_to,
           COUNT(*) AS n
    FROM r
    GROUP BY ticker
    """
    price = {row["ticker"]: row for row in _rows(
        sql, (_date_for(kind, start, "lo"), _date_for(kind, end, "hi")))}

    if kind == "stock":
        meta = _rows("SELECT stockid AS id, ticker, name, market, sector FROM stocks")
    else:
        meta = _rows("SELECT id, ticker, name, type FROM etf")

    out = []
    for m in meta:
        p = price.get(m["ticker"])
        if not p or not p["first_v"]:
            continue
        if market and m.get("market") != market:
            continue
        if sector and m.get("sector") != sector:
            continue
        if etf_type and m.get("type") != etf_type:
            continue
        first, last, mx, mn = p["first_v"], p["last_v"], p["max_v"], p["min_v"]
        out.append({
            "id": m["id"], "ticker": m["ticker"], "name": m["name"],
            "last": last,
            "gain": ((last - first) / first * 100.0) if first else None,
            "ceil": ((last - mx) / mx * 100.0) if mx else None,
            "floor": ((last - mn) / mn * 100.0) if mn else None,
            "d_from": p["d_from"], "d_to": p["d_to"], "n": p["n"],
        })
    return out


def range_gainer(kind, start, end, market=None, etf_type=None, sector=None):
    """Cached custom-window performance, keyed to the exact filter set. Returns a
    dict {ticker: {gain, ceil, floor, ...}} for easy per-row lookup in the table."""
    return cache.get_or_set(
        "range", ("__range__", kind, start, end, market, etf_type, sector),
        lambda: {r["ticker"]: r for r in
                 _range_gainer(kind, start, end, market, etf_type, sector)})


# ---------------------------------------------------------------------------
# Multi-period performance (/performance «بازدهٔ دوره‌ای») — for every ticker, the
# gain over each PERF_PERIODS window PLUS an all-time «از ابتدا» column. Mirrors
# the old Streamlit stock_gain analyzer, but computed in SQL.
# ---------------------------------------------------------------------------
def _pct(cur, base):
    return ((cur - base) / base * 100.0) if base else None


def _perf_prices(kind, as_of):
    """Per-ticker gain / سقف (ceil) / کف (floor) behind the multi-period table,
    keyed by ticker.

    Trailing windows (1W…3Y) come from a recent slice of the price table with the
    same FILTER-aggregation trick as _gainer: for a window of N trading days, `g`
    is the close N days back (rn=N+1) for the gain, and `cmax`/`fmin` are the
    max/min INSIDE the window (rn≤N+1) for ceil/floor. The «از ابتدا» column needs
    the whole history, so all-time min/max come from a plain GROUP BY and the very
    first close from an index-backed LATERAL lookup (cheap: one seek per ticker).
    All bounded by `as_of` so the page is reproducible for a past date."""
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    ref_tbl = "stocks" if kind == "stock" else "etf"

    # --- trailing windows: gain source + running max/min per period ---
    parts = []
    for p in PERF_PERIODS:
        k, rn = p["key"], p["n"] + 1
        parts.append(f"MAX(v) FILTER (WHERE rn={rn})  g_{k}")
        parts.append(f"MAX(v) FILTER (WHERE rn<={rn}) cmax_{k}")
        parts.append(f"MIN(v) FILTER (WHERE rn<={rn}) fmin_{k}")
    trailing = ",\n           ".join(parts)
    # 4 calendar years of slice comfortably covers the 720-trading-day (3y) window.
    sql = f"""
    WITH ranked AS (
        SELECT ticker, adj_final::float v,
               ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
        FROM {price_tbl}
        WHERE adj_final > 0 AND date <= %s AND date >= %s
    )
    SELECT ticker,
           MAX(v) FILTER (WHERE rn=1) AS latest,
           {trailing}
    FROM ranked
    GROUP BY ticker
    HAVING MAX(v) FILTER (WHERE rn=1) IS NOT NULL
    """
    trail = {r["ticker"]: r for r in _rows(sql, _window(kind, as_of, years=4))}

    # --- «از ابتدا»: all-time min/max, then the very first recorded close ---
    allt = {r["ticker"]: r for r in _rows(
        f"SELECT ticker, MIN(adj_final::float) AS mn, MAX(adj_final::float) AS mx "
        f"FROM {price_tbl} WHERE adj_final > 0 AND date <= %s GROUP BY ticker",
        (_date_for(kind, as_of, "hi"),))}
    first = {r["ticker"]: r["fv"] for r in _rows(
        f"SELECT s.ticker, f.fv FROM {ref_tbl} s JOIN LATERAL ("
        f"  SELECT adj_final::float AS fv FROM {price_tbl} p "
        f"  WHERE p.ticker = s.ticker AND p.adj_final > 0 AND p.date <= %s "
        f"  ORDER BY p.date ASC LIMIT 1) f ON true", (_date_for(kind, as_of, "hi"),))}

    out = {}
    for t, tr in trail.items():
        latest = tr["latest"]
        row = {"latest": latest}
        for p in PERF_PERIODS:
            k = p["key"]
            row[f"{k}_gain"] = _pct(latest, tr[f"g_{k}"])
            row[f"{k}_ceil"] = _pct(latest, tr[f"cmax_{k}"])
            row[f"{k}_floor"] = _pct(latest, tr[f"fmin_{k}"])
        a = allt.get(t)
        row["first_gain"] = _pct(latest, first.get(t))
        row["first_ceil"] = _pct(latest, a["mx"]) if a else None
        row["first_floor"] = _pct(latest, a["mn"]) if a else None
        out[t] = row
    return out


def _perf_prices_from_view(kind):
    """mv_perf_prices_<kind> keyed by ticker — same dict shape _perf_prices()
    returns, so perf_multi() cannot tell the difference."""
    out = {}
    for r in _rows(f"SELECT * FROM mv_perf_prices_{kind}"):
        t = r.pop("ticker")
        out[t] = r
    return out


def _perf_prices_cached(kind, as_of):
    return cache.get_or_set(
        "perf", ("__perf__", kind, as_of),
        lambda: (_perf_prices_from_view(kind) if _use_view(kind, as_of)
                 else _perf_prices(kind, as_of)))


def _perf_multi_all(kind, as_of):
    """The whole multi-period table — price maths joined to the reference table
    and sorted — cached once per (kind, as_of), UNFILTERED.

    Same shape of fix as _gainer_all(): the old cache key carried the four
    filters, so every market/group/sub-group click wrote its own entry holding
    its own copy of up to ~780 rows. The heavy scan (_perf_prices_cached) was
    already shared, so this was never the multi-second bug market_gainer had —
    but it is the same unbounded-per-combination caching, and the join + sort it
    repeated per combination is exactly the work one shared list avoids."""
    def build():
        prices = _perf_prices_cached(kind, as_of)
        if kind == "stock":
            meta = _rows("SELECT stockid AS id, ticker, name, market, sector, sub_sector FROM stocks")
        else:
            meta = _rows("SELECT id, ticker, name, type FROM etf")

        out = []
        for m in meta:
            pr = prices.get(m["ticker"])
            if not pr:
                continue
            row = dict(pr)
            row.update(id=m["id"], ticker=m["ticker"], name=m["name"],
                       market=m.get("market"), sector=m.get("sector"),
                       sub_sector=m.get("sub_sector"), type=m.get("type"))
            out.append(row)
        out.sort(key=lambda r: (r["m1_gain"] is None, -(r["m1_gain"] or 0)))
        return out

    return cache.get_or_set("perfm", ("__perfm__", kind, as_of), build)


def perf_multi(kind, as_of=None, market=None, sector=None, etf_type=None,
               sub_sector=None):
    """Multi-period performance rows joined to the reference table and filtered by
    market / group / sub-group. Sorted by the 1-month gain (blanks last). The
    table is computed once per (kind, as_of) and narrowed in memory."""
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return [], None

    rows = _perf_multi_all(kind, as_of)
    if market or sector or sub_sector or etf_type:
        rows = [r for r in rows
                if (not market or r.get("market") == market)
                and (not sector or r.get("sector") == sector)
                and (not sub_sector or r.get("sub_sector") == sub_sector)
                and (not etf_type or r.get("type") == etf_type)]
    # Hand back COPIES. /performance writes custom_gain / custom_ceil /
    # custom_floor onto these rows for the «بازهٔ دلخواه» column, and the row dicts
    # are now shared by every filter combination — without the copy that write
    # would land in the cache and bleed into other requests.
    return [dict(r) for r in rows], as_of


def clear_cache():
    """Invalidate every cached analytic — called after a data update, when the
    numbers behind all of them change.

    It no longer clears anything: it INCRs the analytics version key in Redis,
    which changes the key namespace every worker computes from. That is what
    makes invalidation atomic. The old blanket `.clear()` of five dicts only
    emptied the caches of the ONE process that ran it — under Gunicorn the other
    three workers would have carried on serving pre-update numbers until their
    own next restart. Orphaned old-version entries are never read again and
    expire on their own via the TTL.

    Returns the new version (0 when Redis is unreachable, where there is no
    cross-process cache to invalidate in the first place)."""
    return cache.bump_version()


def delete_price_history(kind, ticker=None, start=None, end=None):
    """Delete price-history rows for one ticker (or ALL tickers when ticker is
    empty) within a Jalali date range. `start`/`end` are inclusive Jalali bounds
    — required so a stray call can never wipe the whole table. They are resolved
    to real dates through the calendar and matched against the `date` column, so
    the delete uses the (ticker, date) index instead of scanning string
    comparisons. An out-of-range bound resolves to NULL, which matches no rows —
    the same (safe) outcome the string comparison gave.

    Returns the number of rows deleted."""
    if kind not in ("stock", "etf"):
        raise ValueError("kind must be 'stock' or 'etf'")
    if not start or not end:
        raise ValueError("a from/to date range is required")
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    clauses = ["date >= %s", "date <= %s"]
    params = [_date_for(kind, start, "lo"), _date_for(kind, end, "hi")]
    if ticker:
        clauses.append("ticker = %s")
        params.append(ticker)
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {tbl} WHERE " + " AND ".join(clauses), params)
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        release(conn)


def stock_markets():
    return [r["market"] for r in _rows(
        "SELECT DISTINCT market FROM stocks WHERE market IS NOT NULL ORDER BY market")]


def stock_sectors():
    """Distinct industry groups (گروه صنعت) for the stock-page «گروه» filter."""
    return [r["sector"] for r in _rows(
        "SELECT DISTINCT sector FROM stocks "
        "WHERE sector IS NOT NULL AND sector <> '' ORDER BY sector")]


def stock_sub_sectors(sector=None):
    """Distinct sub-industry groups (زیرگروه صنعت) for the stock-page «زیرگروه»
    filter. When a parent `sector` is given the list is narrowed to that sector,
    so the two dropdowns cascade."""
    if sector:
        rows = _rows(
            "SELECT DISTINCT sub_sector FROM stocks "
            "WHERE sub_sector IS NOT NULL AND sub_sector <> '' AND sector = %s "
            "ORDER BY sub_sector", (sector,))
    else:
        rows = _rows(
            "SELECT DISTINCT sub_sector FROM stocks "
            "WHERE sub_sector IS NOT NULL AND sub_sector <> '' ORDER BY sub_sector")
    return [r["sub_sector"] for r in rows]


def etf_types():
    return [r["type"] for r in _rows(
        "SELECT type, COUNT(*) c FROM etf WHERE type IS NOT NULL GROUP BY type ORDER BY c DESC")]

# ---------------------------------------------------------------------------
# Technical-analysis strategy scanner
#
# Classic, widely-documented indicator strategies. Each one scans every ticker's
# recent adjusted-close series and flags a *buy* signal. Parameters use the
# textbook defaults (SMA 50/200, RSI-14 with 30/70 bands, MACD 12/26/9,
# Bollinger 20×2σ). This is an educational screener, NOT financial advice.
#
# Sources:
#   Golden Cross — StockCharts ChartSchool
#     https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/moving-average-trading-strategies/trading-using-the-golden-cross
#   MACD + RSI confirmation — QuantifiedStrategies
#     https://www.quantifiedstrategies.com/macd-and-rsi-strategy/
#   Bollinger Band + RSI mean reversion — QuantifiedStrategies
#     https://www.quantifiedstrategies.com/bollinger-bands-trading-strategy/
# ---------------------------------------------------------------------------
_SRC_MA = "https://www.investopedia.com/terms/g/goldencross.asp"
_SRC_MACD = "https://www.investopedia.com/terms/m/macd.asp"
_SRC_RSI = "https://www.investopedia.com/terms/r/rsi.asp"
_SRC_BOLL = "https://www.investopedia.com/terms/b/bollingerbands.asp"

STRATEGIES = [
    {"key": "golden", "name": "تقاطع طلایی",
     "short": "میانگین متحرک ۵۰ روزه به‌تازگی از بالای ۲۰۰ روزه عبور کرده و قیمت بالای آن است",
     "desc": "سیگنال کلاسیک آغاز روند صعودی بلندمدت: هرگاه میانگین متحرک سادهٔ ۵۰ روزه از پایین به بالای میانگین ۲۰۰ روزه عبور کند (Golden Cross).",
     "source": _SRC_MA},
    {"key": "sma_20_50", "name": "تقاطع میانگین ۲۰ و ۵۰ روزه",
     "short": "میانگین ۲۰ روزه از بالای میانگین ۵۰ روزه عبور کرده است",
     "desc": "نسخهٔ کوتاه‌مدت‌تر تقاطع میانگین‌ها: عبور صعودی میانگین سادهٔ ۲۰ روزه از میانگین ۵۰ روزه، برای شناسایی زودتر شروع موج صعودی.",
     "source": _SRC_MA},
    {"key": "macd_rsi", "name": "تقاطع مکدی با تأیید RSI",
     "short": "خط مکدی از بالای خط سیگنال عبور کرده و RSI بالای ۵۰ است",
     "desc": "ترکیب مومنتوم و روند: تقاطع صعودی خط MACD با خط سیگنال (دورهٔ ۱۲/۲۶/۹) که با قرار گرفتن RSI بالای ۵۰ تأیید می‌شود تا سیگنال‌های ضعیف حذف شوند.",
     "source": "https://www.quantifiedstrategies.com/macd-and-rsi-strategy/"},
    {"key": "macd_zero", "name": "عبور مکدی از خط صفر",
     "short": "خط مکدی از پایینِ صفر به بالای آن عبور کرده است",
     "desc": "چرخش مومنتوم به مثبت: هنگامی که خط MACD از خط صفر به سمت بالا عبور می‌کند، نشانهٔ غلبهٔ میانگین کوتاه‌مدت بر بلندمدت و آغاز فشار خرید است.",
     "source": _SRC_MACD},
    {"key": "rsi_bounce", "name": "خروج از اشباع فروش (RSI)",
     "short": "RSI به‌تازگی از زیر ۳۰ به بالای آن بازگشته است",
     "desc": "استراتژی بازگشت به میانگین: وقتی شاخص قدرت نسبی (RSI-14) از ناحیهٔ اشباع فروش (زیر ۳۰) خارج شده و رو به بالا حرکت می‌کند.",
     "source": _SRC_RSI},
    {"key": "rsi_50", "name": "ورود RSI به ناحیهٔ صعودی",
     "short": "RSI از خط میانی ۵۰ به بالا عبور کرده است",
     "desc": "تأیید مومنتوم: عبور RSI از سطح ۵۰ به سمت بالا معمولاً به‌عنوان تغییر توازن قدرت به نفع خریداران و ورود به فاز صعودی تفسیر می‌شود.",
     "source": _SRC_RSI},
    {"key": "boll", "name": "بازگشت از باند پایین بولینگر",
     "short": "قیمت پس از لمس باند پایینی بولینگر به داخل کانال بازگشته است",
     "desc": "بازگشت به میانگین با باند بولینگر (۲۰ دوره، ۲ انحراف معیار): قیمت باند پایینی را لمس کرده (همراه با RSI پایین) و اکنون به داخل کانال بازمی‌گردد؛ هدف، میانگین ۲۰ روزه است.",
     "source": "https://www.quantifiedstrategies.com/bollinger-bands-trading-strategy/"},
    {"key": "boll_breakout", "name": "شکست باند بالای بولینگر",
     "short": "قیمت با گسترش پهنای باند، از باند بالایی بولینگر عبور کرده است",
     "desc": "شکست نوسانی: عبور قیمت از باند بالایی بولینگر همراه با افزایش پهنای باند، که اغلب پس از دورهٔ فشردگی نشانهٔ آغاز یک حرکت صعودی قوی است.",
     "source": _SRC_BOLL},
    {"key": "uptrend", "name": "ادامهٔ روند صعودی",
     "short": "قیمت بالای میانگین ۵۰ روزهٔ صعودی و بالای میانگین ۲۰۰ روزه است",
     "desc": "روندی پیرو: نماد در روند صعودی پایدار قرار دارد — قیمت بالای میانگین ۵۰ روزه، میانگین ۵۰ روزه رو به بالا، و بالاتر از میانگین ۲۰۰ روزه.",
     "source": _SRC_MA},
    {"key": "above_200", "name": "بالای میانگین ۲۰۰ روزهٔ صعودی",
     "short": "قیمت بالای میانگین ۲۰۰ روزه است و این میانگین رو به بالاست",
     "desc": "فیلتر روند بلندمدت: قرار گرفتن قیمت بالای میانگین متحرک ۲۰۰ روزهٔ رو به بالا، سادهٔ‌ترین معیار برای تشخیص بازار صعودی بلندمدت یک نماد.",
     "source": _SRC_MA},
    {"key": "roc", "name": "مومنتوم (نرخ تغییر سه‌ماهه)",
     "short": "بازدهی سه‌ماههٔ مثبت (بیش از ۵٪) و همچنان رو به رشد",
     "desc": "مومنتوم قیمتی: نرخ تغییر قیمت در ۳ ماه اخیر (۶۰ روز معاملاتی) مثبت و بزرگ‌تر از ۵٪ است و روند کوتاه‌مدت همچنان صعودی است.",
     "source": "https://www.investopedia.com/terms/p/pricerateofchange.asp"},
    {"key": "high_52w", "name": "نزدیک سقف ۵۲ هفته",
     "short": "قیمت در فاصلهٔ کمتر از ۳٪ از بالاترین قیمت یک‌سالهٔ خود است",
     "desc": "شکست/مومنتوم قدرت: نزدیک شدن قیمت به بالاترین سطح ۵۲ هفتهٔ اخیر که اغلب نشانهٔ قدرت خریداران و احتمال ادامهٔ روند صعودی است.",
     "source": "https://www.investopedia.com/terms/1/52weekhigh.asp"},
    # --- strategies grounded in academic / widely-backtested research ---------
    {"key": "rsi2", "name": "بازگشت کوتاه‌مدت RSI(2) کانرز",
     "short": "قیمت بالای میانگین ۲۰۰ روزه و RSI با دورهٔ ۲ زیر ۱۰ است",
     "desc": "استراتژی بازگشت به میانگینِ لری کانرز (کتاب Short Term Trading Strategies That Work): در روند صعودی بلندمدت (قیمت بالای میانگین ۲۰۰ روزه)، افت RSI دو‌دوره‌ای به زیر ۱۰ یک فرصت خرید کوتاه‌مدت با نرخ برد بالا در آزمون‌های تاریخی است.",
     "source": "https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies/rsi-2"},
    {"key": "abs_mom", "name": "مومنتوم زمانی (بازده ۱۲ ماهه مثبت)",
     "short": "بازده ۱۲ ماه اخیر نماد مثبت است",
     "desc": "مومنتوم زمانی/مطلق (Moskowitz، Ooi و Pedersen ۲۰۱۲): اگر بازده مطلق نماد در ۱۲ ماه گذشته مثبت باشد، تمایل به ادامهٔ روند وجود دارد؛ معیاری ساده و آزمون‌شده برای ماندن در بازار.",
     "source": "https://doi.org/10.1016/j.jfineco.2011.11.003"},
    {"key": "xsec_mom", "name": "مومنتوم نسبی (دهک برتر ۱۲-۱)",
     "short": "جزو ۱۰٪ برتر نمادها از نظر بازده ۱۲ تا ۱ ماه گذشته",
     "desc": "مومنتوم مقطعی برندگان (Jegadeesh و Titman ۱۹۹۳): نمادها بر پایهٔ بازدهِ از ۱۲ ماه پیش تا یک ماه پیش (با حذف ماه آخر) رتبه‌بندی می‌شوند و دهک برتر خریداری می‌شود — پرارجاع‌ترین استراتژی مومنتوم دانشگاهی.",
     "source": "https://doi.org/10.1111/j.1540-6261.1993.tb04702.x"},
]


def _sma_series(px, n):
    out = [None] * len(px)
    if len(px) < n:
        return out
    s = sum(px[:n])
    out[n - 1] = s / n
    for i in range(n, len(px)):
        s += px[i] - px[i - n]
        out[i] = s / n
    return out


def _ema_series(px, n):
    out = [None] * len(px)
    if len(px) < n:
        return out
    k = 2.0 / (n + 1)
    ema = sum(px[:n]) / n
    out[n - 1] = ema
    for i in range(n, len(px)):
        ema = px[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def _rsi_series(px, n=14):
    """Wilder's RSI."""
    out = [None] * len(px)
    if len(px) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = px[i] - px[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else (0.0 if ag == 0 else 100 - 100 / (1 + ag / al))
    for i in range(n + 1, len(px)):
        ch = px[i] - px[i - 1]
        ag = (ag * (n - 1) + max(ch, 0.0)) / n
        al = (al * (n - 1) + max(-ch, 0.0)) / n
        out[i] = 100.0 if al == 0 else (0.0 if ag == 0 else 100 - 100 / (1 + ag / al))
    return out


def _macd_series(px, fast=12, slow=26, sig=9):
    ef, es = _ema_series(px, fast), _ema_series(px, slow)
    macd = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    vals = [m for m in macd if m is not None]
    sig_vals = _ema_series(vals, sig)
    signal = [None] * len(px)
    j = 0
    for i, m in enumerate(macd):
        if m is not None:
            signal[i] = sig_vals[j]
            j += 1
    hist = [(m - s) if (m is not None and s is not None) else None for m, s in zip(macd, signal)]
    return macd, signal, hist


def _boll_series(px, n=20, k=2.0):
    """Bollinger mid/upper/lower bands via a rolling mean/variance (O(n)) — the
    sliding std-dev is the hot path of the scan, so it avoids per-window recompute."""
    mid = [None] * len(px)
    upper = [None] * len(px)
    lower = [None] * len(px)
    if len(px) < n:
        return mid, upper, lower
    s = sum(px[:n])
    s2 = sum(v * v for v in px[:n])
    for i in range(n - 1, len(px)):
        if i >= n:
            s += px[i] - px[i - n]
            s2 += px[i] * px[i] - px[i - n] * px[i - n]
        mean = s / n
        var = s2 / n - mean * mean
        sd = var ** 0.5 if var > 0 else 0.0
        mid[i] = mean
        upper[i] = mean + k * sd
        lower[i] = mean - k * sd
    return mid, upper, lower


def _cross_up_recent(a, b, lookback):
    """True if series a crossed from ≤b to >b within the last `lookback` bars and
    is still above b now."""
    n = len(a)
    if n < 2 or a[-1] is None or b[-1] is None or a[-1] <= b[-1]:
        return False
    for i in range(max(1, n - lookback), n):
        p1, p2, c1, c2 = a[i - 1], b[i - 1], a[i], b[i]
        if None in (p1, p2, c1, c2):
            continue
        if p1 <= p2 and c1 > c2:
            return True
    return False


def _cross_above_level(s, level, lookback):
    n = len(s)
    if n < 2 or s[-1] is None or s[-1] <= level:
        return False
    for i in range(max(1, n - lookback), n):
        if s[i - 1] is None or s[i] is None:
            continue
        if s[i - 1] <= level < s[i]:
            return True
    return False


def _boll_bounce(px, lower, rsi_s, lookback):
    n = len(px)
    if lower[-1] is None or px[-1] <= lower[-1]:
        return False
    for i in range(max(0, n - lookback), n):
        if lower[i] is None:
            continue
        if px[i] <= lower[i] and (rsi_s[i] is None or rsi_s[i] < 40):
            return True
    return False


def _rising(s, k):
    return s[-1] is not None and s[-1 - k] is not None and s[-1] > s[-1 - k]


def _eval_strategies(px):
    """Return the list of strategy keys currently giving a BUY signal for one
    ticker, plus a snapshot of key indicator values for display."""
    close = px[-1]
    n = len(px)
    sma20 = _sma_series(px, 20)
    sma50 = _sma_series(px, 50)
    sma200 = _sma_series(px, 200)
    rsi_s = _rsi_series(px, 14)
    macd, signal, _ = _macd_series(px)
    _, boll_up, boll_lo = _boll_series(px, 20, 2.0)
    rsi = rsi_s[-1]

    sig = []
    # 1) Golden Cross — SMA50 crosses above SMA200
    if _cross_up_recent(sma50, sma200, 15) and sma200[-1] and close > sma200[-1]:
        sig.append("golden")
    # 2) Short-term MA crossover — SMA20 crosses above SMA50
    if _cross_up_recent(sma20, sma50, 10) and sma50[-1] and close > sma50[-1]:
        sig.append("sma_20_50")
    # 3) MACD bullish cross confirmed by RSI > 50
    if _cross_up_recent(macd, signal, 5) and rsi is not None and rsi > 50:
        sig.append("macd_rsi")
    # 4) MACD crosses above the zero line (momentum turns positive)
    if _cross_above_level(macd, 0.0, 5):
        sig.append("macd_zero")
    # 5) RSI recovers out of oversold (crosses back above 30)
    if _cross_above_level(rsi_s, 30, 5):
        sig.append("rsi_bounce")
    # 6) RSI crosses above 50 (momentum enters bullish zone)
    if _cross_above_level(rsi_s, 50, 5):
        sig.append("rsi_50")
    # 7) Bollinger lower-band bounce (mean reversion)
    if _boll_bounce(px, boll_lo, rsi_s, 5):
        sig.append("boll")
    # 8) Bollinger breakout — price breaks above upper band with expanding width
    if (boll_up[-1] is not None and boll_up[-4] is not None
            and _cross_up_recent(px, boll_up, 3)
            and (boll_up[-1] - boll_lo[-1]) > (boll_up[-4] - boll_lo[-4])):
        sig.append("boll_breakout")
    # 9) Established uptrend — price > rising SMA50 > SMA200
    if sma50[-1] and sma200[-1] and close > sma50[-1] > sma200[-1] and _rising(sma50, 20):
        sig.append("uptrend")
    # 10) Long-term trend filter — price above a rising SMA200
    if sma200[-1] and close > sma200[-1] and _rising(sma200, 20):
        sig.append("above_200")
    # 11) Momentum — positive & rising 3-month rate of change
    if n > 66 and px[-61] > 0 and (close / px[-61] - 1) > 0.05 and close > px[-6]:
        sig.append("roc")
    # 12) Near a 52-week high (breakout momentum)
    if n >= 120:
        window = px[-240:] if n >= 240 else px
        hi = max(window)
        if hi > 0 and close >= 0.97 * hi and close > px[-6]:
            sig.append("high_52w")
    # 13) Connors RSI(2) pullback inside an uptrend (Larry Connors)
    if sma200[-1] and close > sma200[-1]:
        rsi2 = _rsi_series(px, 2)
        if rsi2[-1] is not None and rsi2[-1] < 10:
            sig.append("rsi2")
    # 14) Absolute (time-series) momentum — positive 12-month return
    #     (Moskowitz, Ooi & Pedersen 2012)
    if n >= 253 and px[-253] > 0 and (close / px[-253] - 1) > 0:
        sig.append("abs_mom")
    return sig, {"rsi": rsi, "sma50": sma50[-1], "sma200": sma200[-1]}


def _scan_meta_counts(rows):
    """scanned / scanned_by_group / scanned_by_sub over EVERY scanned ticker —
    including the ones that matched nothing, exactly as the Python scans count."""
    scanned_by_group, scanned_by_sub = {}, {}
    for r in rows:
        g = r["group"]
        scanned_by_group[g] = scanned_by_group.get(g, 0) + 1
        if r.get("sub_group"):
            scanned_by_sub[r["sub_group"]] = scanned_by_sub.get(r["sub_group"], 0) + 1
    return len(rows), scanned_by_group, scanned_by_sub


def _strategy_from_view(kind, as_of):
    """Rebuild _strategy_scan_full()'s return shape from mv_strategy_<kind>.

    The view holds one row per SCANNED ticker (signals may be empty) because the
    `scanned` counts include tickers that matched nothing. The grouping, ranking
    and pick selection are pure list work over ~780 rows — microseconds."""
    rows = _rows(f'SELECT id, ticker, name, "group", sub_group, latest, rsi, signals '
                 f'FROM mv_strategy_{kind}')
    scanned, by_group, by_sub = _scan_meta_counts(rows)

    matched = []
    for r in rows:
        if not r["signals"]:
            continue
        r["score"] = len(r["signals"])
        matched.append(r)

    by_strategy = {s["key"]: [] for s in STRATEGIES}
    for r in matched:
        for k in r["signals"]:
            by_strategy[k].append(r)
    for k in by_strategy:
        by_strategy[k].sort(key=lambda r: (r["rsi"] if r["rsi"] is not None else 999))

    picks = sorted([r for r in matched if r["score"] >= 2],
                   key=lambda r: (-r["score"], r["rsi"] if r["rsi"] is not None else 999))
    return {"as_of": as_of, "by_strategy": by_strategy, "picks": picks,
            "count": len(matched), "scanned": scanned,
            "scanned_by_group": by_group, "scanned_by_sub": by_sub}


def _filter_from_view(kind, as_of):
    """Rebuild _filter_scan_full()'s return shape from mv_filter_<kind>."""
    rows = _rows(f'SELECT id, ticker, name, "group", sub_group, latest, rsi, matches '
                 f'FROM mv_filter_{kind}')
    scanned, by_group, by_sub = _scan_meta_counts(rows)

    by_filter = {f["key"]: [] for f in FILTERS}
    matched_tickers = set()
    for r in rows:
        if not r["matches"]:
            continue
        matched_tickers.add(r["ticker"])
        for k in r["matches"]:
            by_filter[k].append(r)
    for k in by_filter:
        by_filter[k].sort(key=lambda r: (r["rsi"] if r["rsi"] is not None else 999))
    return {"as_of": as_of, "by_filter": by_filter, "count": len(matched_tickers),
            "scanned": scanned, "scanned_by_group": by_group, "scanned_by_sub": by_sub}


def _score_from_view(kind, as_of):
    """Rebuild score_scan_full()'s return shape from mv_score_<kind>.

    The view stores the RAW composite; round() and _verdict() stay here on
    purpose — python rounds half-to-even on the binary double while postgres
    rounds half-up on the decimal expansion, so doing it in SQL could move a
    score by 0.1. _verdict() is fed the unrounded value, as signal_score does."""
    rows = _rows(f'SELECT id, ticker, name, "group", sub_group, latest, composite, '
                 f'trend, momentum, risk, rsi, range_pos FROM mv_score_{kind}')
    scanned, by_group, by_sub = _scan_meta_counts(rows)
    out = []
    for r in rows:
        c = r.pop("composite")
        r["score"] = round(c, 1)
        r["verdict"] = _verdict(c)
        for k in ("trend", "momentum", "risk"):
            r[k] = round(r[k], 1) if r[k] is not None else None
        out.append(r)
    out.sort(key=lambda r: -r["score"])
    return {"as_of": as_of, "rows": out, "scanned": scanned,
            "scanned_by_group": by_group, "scanned_by_sub": by_sub}


def _strategy_scan_full(kind, as_of=None):
    """Scan every ticker of `kind` and group the buy signals by strategy. Cached
    per (kind, as_of); the group/type filter is applied cheaply on top in
    strategy_scan() so the heavy scan runs once and is shared across filters."""
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return {"as_of": None, "by_strategy": {}, "picks": [], "count": 0,
                "scanned": 0, "scanned_by_group": {}}
    return cache.get_or_set(
        "strategy", (kind, as_of),
        lambda: (_strategy_from_view(kind, as_of) if _use_view(kind, as_of)
                 else _strategy_scan_live(kind, as_of)))


def _strategy_scan_live(kind, as_of):
    """The strategy scan computed from raw prices — the fallback for a historical
    as_of, or a database where mv_strategy_<kind> has not been built yet."""
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = _rows(
        f"""
        WITH ranked AS (
            SELECT ticker, adj_final::float v,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
            FROM {price_tbl}
            WHERE adj_final > 0 AND date <= %s AND date >= %s
        )
        SELECT ticker, v FROM ranked WHERE rn <= 300 ORDER BY ticker, rn DESC
        """,
        _window(kind, as_of, 2))
    series = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(r["v"])   # oldest→newest (rn DESC)

    if kind == "stock":
        meta = {m["ticker"]: m for m in _rows(
            "SELECT stockid AS id, ticker, name, sector, sub_sector FROM stocks")}
    else:
        meta = {m["ticker"]: m for m in _rows(
            "SELECT id, ticker, name, type FROM etf")}

    scanned = 0
    scanned_by_group = {}
    scanned_by_sub = {}
    graded = []   # per-ticker interim results (signals get amended below)
    for ticker, px in series.items():
        if len(px) < 30:
            continue
        m = meta.get(ticker)
        if not m:
            continue
        grp = m.get("sector") if kind == "stock" else m.get("type")
        sub = m.get("sub_sector") if kind == "stock" else None
        scanned += 1
        scanned_by_group[grp] = scanned_by_group.get(grp, 0) + 1
        if sub:
            scanned_by_sub[sub] = scanned_by_sub.get(sub, 0) + 1
        sig, ind = _eval_strategies(px)
        n = len(px)
        # Jegadeesh–Titman 12-1 momentum: return from ~12 months ago (253 trading
        # days) to ~1 month ago (22), skipping the most recent month.
        mom = (px[-22] / px[-253] - 1) if (n >= 253 and px[-253] > 0) else None
        graded.append({
            "id": m["id"], "ticker": ticker, "name": m["name"], "group": grp,
            "sub_group": sub, "latest": px[-1], "rsi": ind["rsi"],
            "signals": sig, "mom": mom,
        })

    # Cross-sectional momentum is a RELATIVE ranking across the whole universe:
    # flag the top decile of 12-1 momentum as a buy (Jegadeesh & Titman 1993).
    moms = sorted(r["mom"] for r in graded if r["mom"] is not None)
    if len(moms) >= 20:
        thr = moms[int(0.9 * (len(moms) - 1))]
        for r in graded:
            if r["mom"] is not None and r["mom"] >= thr:
                r["signals"].append("xsec_mom")

    matched = []
    for r in graded:
        if not r["signals"]:
            continue
        r["score"] = len(r["signals"])
        r.pop("mom", None)
        matched.append(r)

    by_strategy = {s["key"]: [] for s in STRATEGIES}
    for r in matched:
        for k in r["signals"]:
            by_strategy[k].append(r)
    for k in by_strategy:
        by_strategy[k].sort(key=lambda r: (r["rsi"] if r["rsi"] is not None else 999))

    picks = sorted([r for r in matched if r["score"] >= 2],
                   key=lambda r: (-r["score"], r["rsi"] if r["rsi"] is not None else 999))

    return {"as_of": as_of, "by_strategy": by_strategy, "picks": picks,
            "count": len(matched), "scanned": scanned,
            "scanned_by_group": scanned_by_group,
            "scanned_by_sub": scanned_by_sub}


def strategy_scan(kind, as_of=None, group=None, sub_group=None):
    """Full strategy scan, optionally narrowed to one industry group (stocks) or
    ETF type, and further to a sub-group (زیرگروه). Filtering is applied to the
    cached full scan, so all groups share a single expensive computation."""
    full = _strategy_scan_full(kind, as_of)
    if not group and not sub_group:
        return full

    def keep(r):
        return ((not group or r["group"] == group) and
                (not sub_group or r.get("sub_group") == sub_group))

    by = {k: [r for r in rows if keep(r)]
          for k, rows in full["by_strategy"].items()}
    picks = [r for r in full["picks"] if keep(r)]
    matched = {r["ticker"] for rows in by.values() for r in rows}
    if sub_group:
        scanned = full["scanned_by_sub"].get(sub_group, 0)
    else:
        scanned = full["scanned_by_group"].get(group, 0)
    return {**full, "by_strategy": by, "picks": picks,
            "count": len(matched), "scanned": scanned}


# ===========================================================================
# امتیاز و سیگنالِ فنی — composite technical score, verdict & risk metrics
#
# The "should I buy this?" layer. It synthesises the SAME price-history
# indicators the strategy scanner uses (SMA 20/50/200, RSI-14, MACD 12/26/9,
# Bollinger) plus risk stats into a single 0–100 score, a plain-language verdict
# and the reasons behind it. It is deliberately PURELY TECHNICAL (price-only):
# the score is a weighted average over indicator BUCKETS, and a `fundamental`
# bucket is reserved at weight 0 so P/E-EPS-revenue data can be folded in later
# without reworking the math. Educational — NOT financial advice.
# ===========================================================================
SCORE_WEIGHTS = {
    "trend":       0.30,   # price vs SMA50/200, alignment, slope
    "momentum":    0.26,   # 3-month / 6-month return + MACD histogram
    "rsi":         0.14,   # RSI-14 zone
    "range":       0.14,   # position within the 52-week range
    "risk":        0.16,   # volatility + max drawdown (lower is better)
    "fundamental": 0.00,   # reserved — filled when fundamentals data exists
}

# Verdict bands, high→low: (min_score, key, label, tone). tone drives the colour.
SCORE_BANDS = [
    (75, "strong_buy", "خرید قوی", "pos"),
    (60, "buy",        "خرید",     "pos"),
    (45, "neutral",    "خنثی",     "mid"),
    (30, "weak",       "پرریسک",   "neg"),
    (0,  "avoid",      "اجتناب",   "neg"),
]


def _verdict(score):
    for lo, key, label, tone in SCORE_BANDS:
        if score >= lo:
            return {"key": key, "label": label, "tone": tone, "score": round(score)}
    return {"key": "avoid", "label": "اجتناب", "tone": "neg", "score": round(score)}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _fa_pct(frac):
    """A signed percentage in Persian digits, e.g. 0.234 → «۲۳٪», -0.08 → «−۸٪»."""
    p = round(frac * 100)
    return ("−" if p < 0 else "") + to_persian_plain(abs(p)) + "٪"


# --- risk statistics -------------------------------------------------------
def _daily_returns(px):
    return [px[i] / px[i - 1] - 1 for i in range(1, len(px)) if px[i - 1] > 0]


def _stdev(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1)) ** 0.5


def _max_drawdown(px):
    """Largest peak-to-trough drop as a positive fraction (0.42 == a −42% fall)."""
    peak = px[0]
    mdd = 0.0
    for p in px:
        if p > peak:
            peak = p
        elif peak > 0:
            mdd = max(mdd, 1 - p / peak)
    return mdd


def _annual_vol(px):
    sd = _stdev(_daily_returns(px))
    return sd * (252 ** 0.5) if sd is not None else None


def _return_over_risk(px):
    """Sharpe-like ratio with no risk-free rate: annualised mean daily return
    divided by annualised volatility."""
    rets = _daily_returns(px)
    if len(rets) < 20:
        return None
    sd = _stdev(rets)
    if not sd:
        return None
    return (sum(rets) / len(rets) * 252) / (sd * (252 ** 0.5))


def _market_returns(kind, as_of):
    """Equal-weight daily market-return series {j_date: mean_return} over ~2y,
    cached — the benchmark used as beta's denominator."""
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return {}

    def build():
        price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
        rows = _rows(
            f"""
            WITH r AS (
                SELECT j_date,
                       adj_final::float / NULLIF(
                           LAG(adj_final::float) OVER (PARTITION BY ticker ORDER BY date), 0) - 1 AS ret
                FROM {price_tbl}
                WHERE adj_final > 0 AND date <= %s AND date >= %s
            )
            SELECT j_date, AVG(ret) AS mret FROM r WHERE ret IS NOT NULL GROUP BY j_date
            """,
            _window(kind, as_of, 2))
        return {r["j_date"]: r["mret"] for r in rows}

    return cache.get_or_set("mktret", (kind, as_of), build)


def _beta(dates, px, kind, as_of=None):
    """β of the ticker's daily returns against the equal-weight market index."""
    mret = _market_returns(kind, as_of)
    if not mret or len(px) < 30:
        return None
    xs, ys = [], []                       # market return, ticker return (aligned)
    for i in range(1, len(px)):
        if px[i - 1] <= 0:
            continue
        m = mret.get(dates[i])
        if m is None:
            continue
        xs.append(m)
        ys.append(px[i] / px[i - 1] - 1)
    if len(xs) < 30:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / var


# --- per-bucket sub-scores (each 0..100, or None when data is too short) ----
def _score_vol(vol):
    if vol is None:
        return None
    for thr, sc in ((0.30, 90), (0.45, 72), (0.60, 55), (0.80, 40), (1.10, 28)):
        if vol <= thr:
            return sc
    return 18


def _score_dd(mdd):
    if mdd is None:
        return None
    for thr, sc in ((0.15, 90), (0.30, 72), (0.45, 55), (0.60, 40), (0.75, 28)):
        if mdd <= thr:
            return sc
    return 18


def _score_rsi(rsi):
    if rsi is None:
        return None, []
    r = to_persian_plain(round(rsi))
    if rsi >= 80:
        return 22, [(f"RSI در اشباع خرید شدید ({r}) — احتمال اصلاح", "neg")]
    if rsi >= 70:
        return 42, [(f"RSI در ناحیهٔ اشباع خرید ({r})", "neg")]
    if rsi >= 55:
        return 80, [(f"RSI در ناحیهٔ صعودی سالم ({r})", "pos")]
    if rsi >= 45:
        return 66, []
    if rsi >= 35:
        return 50, []
    if rsi >= 25:
        return 44, [(f"RSI در ناحیهٔ اشباع فروش ({r}) — احتمال بازگشت", "mid")]
    return 40, [(f"RSI در اشباع فروش شدید ({r})", "mid")]


def signal_score(px, dates=None, kind=None, as_of=None):
    """Compute the composite technical score for one adjusted-close series
    `px` (oldest→newest). If `dates`+`kind` are given, β is added too.
    Returns the full analysis dict, or None when there's too little history."""
    n = len(px)
    if n < 30:
        return None
    close = px[-1]
    sma20 = _sma_series(px, 20)[-1]
    sma50_s = _sma_series(px, 50)
    sma200_s = _sma_series(px, 200)
    sma50, sma200 = sma50_s[-1], sma200_s[-1]
    sma50_prev = sma50_s[-21] if len(sma50_s) > 21 else None
    rsi = _rsi_series(px, 14)[-1]
    macd_l, sig_l, hist_l = _macd_series(px)
    macd_last, macd_sig, macd_hist = macd_l[-1], sig_l[-1], hist_l[-1]
    _, boll_up, boll_lo = _boll_series(px, 20, 2.0)

    reasons = []

    # 1) trend — position relative to the 50/200-day averages + slope
    trend = None
    if sma50 is not None or sma200 is not None:
        t = 50.0
        if sma200 is not None:
            if close > sma200:
                t += 20; reasons.append(("قیمت بالای میانگین ۲۰۰ روزه (روند بلندمدت صعودی)", "pos"))
            else:
                t -= 20; reasons.append(("قیمت زیر میانگین ۲۰۰ روزه (روند بلندمدت نزولی)", "neg"))
        if sma50 is not None:
            t += 12 if close > sma50 else -12
        if sma50 is not None and sma200 is not None:
            if sma50 > sma200:
                t += 10; reasons.append(("آرایش صعودی میانگین‌ها (۵۰ بالای ۲۰۰)", "pos"))
            else:
                t -= 10; reasons.append(("آرایش نزولی میانگین‌ها (۵۰ زیر ۲۰۰)", "neg"))
        if sma50 is not None and sma50_prev is not None:
            t += 8 if sma50 > sma50_prev else -8
        trend = _clamp(t, 0, 100)

    # 2) momentum — 3-month & 6-month return, confirmed by the MACD histogram
    r3 = (close / px[-61] - 1) if (n > 61 and px[-61] > 0) else None
    r6 = (close / px[-121] - 1) if (n > 121 and px[-121] > 0) else None
    momentum = None
    if r3 is not None or r6 is not None:
        mo = 50.0
        if r3 is not None:
            mo += _clamp(r3 * 150, -25, 25)
            if r3 >= 0.10:
                reasons.append((f"بازدهی سه‌ماههٔ قوی ({_fa_pct(r3)})", "pos"))
            elif r3 <= -0.10:
                reasons.append((f"افت سه‌ماهه ({_fa_pct(r3)})", "neg"))
        if r6 is not None:
            mo += _clamp(r6 * 60, -15, 15)
        if macd_hist is not None:
            mo += 8 if macd_hist > 0 else -8
        momentum = _clamp(mo, 0, 100)

    # 3) RSI zone
    rsi_score, rsi_reasons = _score_rsi(rsi)
    reasons += rsi_reasons

    # 4) position within the 52-week range
    rng = None
    range_pos = None
    if n >= 60:
        win = px[-240:] if n >= 240 else px
        hi, lo = max(win), min(win)
        range_pos = (close - lo) / (hi - lo) if hi > lo else 0.5
        rng = _clamp(30 + range_pos * 60, 0, 100)
        if range_pos >= 0.9:
            reasons.append(("قیمت نزدیک سقف ۵۲ هفته (قدرت خریداران)", "pos"))
            rng = _clamp(rng - 8, 0, 100)     # extended — trim a little
        elif range_pos <= 0.1:
            reasons.append(("قیمت نزدیک کف ۵۲ هفته", "neg"))
    else:
        hi, lo = max(px), min(px)

    # 5) risk — volatility + drawdown over the trailing window (lower is better)
    rwin = px[-480:] if n >= 480 else px
    vol = _annual_vol(rwin)
    mdd = _max_drawdown(rwin)
    ror = _return_over_risk(rwin)
    vs, ds = _score_vol(vol), _score_dd(mdd)
    parts = [s for s in (vs, ds) if s is not None]
    risk = (sum(parts) / len(parts)) if parts else None
    if vol is not None and vol >= 0.80:
        reasons.append((f"نوسان‌پذیری بالا (سالانه {_fa_pct(vol)})", "neg"))
    if mdd is not None and mdd >= 0.60:
        reasons.append((f"افت حداکثری تاریخی زیاد ({_fa_pct(mdd)})", "neg"))

    subs = {"trend": trend, "momentum": momentum, "rsi": rsi_score,
            "range": rng, "risk": risk, "fundamental": None}

    # composite — weighted average over the buckets that have data
    num = den = 0.0
    for k, w in SCORE_WEIGHTS.items():
        v = subs[k]
        if v is not None and w > 0:
            num += v * w
            den += w
    composite = (num / den) if den else 50.0

    beta = _beta(dates, px, kind, as_of) if (dates is not None and kind) else None

    indicators = {
        "rsi": rsi, "macd": macd_last, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "boll_up": boll_up[-1], "boll_lo": boll_lo[-1], "close": close,
        "hi_52w": hi, "lo_52w": lo, "range_pos": range_pos,
        "vol": vol, "max_dd": mdd, "ror": ror, "beta": beta,
    }
    return {
        "score": round(composite, 1),
        "verdict": _verdict(composite),
        "subs": {k: (round(v, 1) if v is not None else None) for k, v in subs.items()},
        "reasons": [{"text": t, "tone": tone} for t, tone in reasons],
        "indicators": indicators,
    }


def score_scan_full(kind, as_of=None):
    """Score EVERY ticker of `kind` and rank them by the composite score — the
    data behind the /screener page. Cached per (kind, as_of); group filtering is
    layered on cheaply in score_scan() so the heavy scan runs once."""
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return {"as_of": None, "rows": [], "scanned": 0,
                "scanned_by_group": {}, "scanned_by_sub": {}}
    return cache.get_or_set(
        "score", (kind, as_of),
        lambda: (_score_from_view(kind, as_of) if _use_view(kind, as_of)
                 else _score_scan_live(kind, as_of)))


def _score_scan_live(kind, as_of):
    """The score scan computed from raw prices — the fallback for a historical
    as_of, or a database where mv_score_<kind> has not been built yet."""
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = _rows(
        f"""
        WITH ranked AS (
            SELECT ticker, adj_final::float v,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
            FROM {price_tbl}
            WHERE adj_final > 0 AND date <= %s AND date >= %s
        )
        SELECT ticker, v FROM ranked WHERE rn <= 300 ORDER BY ticker, rn DESC
        """,
        _window(kind, as_of, 2))
    series = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(r["v"])   # oldest→newest

    if kind == "stock":
        meta = {m["ticker"]: m for m in _rows(
            "SELECT stockid AS id, ticker, name, sector, sub_sector FROM stocks")}
    else:
        meta = {m["ticker"]: m for m in _rows(
            "SELECT id, ticker, name, type FROM etf")}

    out, scanned = [], 0
    scanned_by_group, scanned_by_sub = {}, {}
    for ticker, px in series.items():
        m = meta.get(ticker)
        if not m:
            continue
        res = signal_score(px)
        if res is None:
            continue
        grp = m.get("sector") if kind == "stock" else m.get("type")
        sub = m.get("sub_sector") if kind == "stock" else None
        scanned += 1
        scanned_by_group[grp] = scanned_by_group.get(grp, 0) + 1
        if sub:
            scanned_by_sub[sub] = scanned_by_sub.get(sub, 0) + 1
        out.append({
            "id": m["id"], "ticker": ticker, "name": m["name"],
            "group": grp, "sub_group": sub, "latest": px[-1],
            "score": res["score"], "verdict": res["verdict"],
            "rsi": res["indicators"]["rsi"], "trend": res["subs"]["trend"],
            "momentum": res["subs"]["momentum"], "risk": res["subs"]["risk"],
            "range_pos": res["indicators"]["range_pos"],
        })
    out.sort(key=lambda r: -r["score"])
    return {"as_of": as_of, "rows": out, "scanned": scanned,
            "scanned_by_group": scanned_by_group, "scanned_by_sub": scanned_by_sub}


def score_scan(kind, as_of=None, group=None, sub_group=None, verdict=None, min_score=None):
    """Ranked score scan, optionally narrowed to a group / sub-group / verdict
    band / minimum score. Filters the cached full scan."""
    full = score_scan_full(kind, as_of)
    rows = full["rows"]
    if group:
        rows = [r for r in rows if r["group"] == group]
    if sub_group:
        rows = [r for r in rows if r.get("sub_group") == sub_group]
    if verdict:
        rows = [r for r in rows if r["verdict"]["key"] == verdict]
    if min_score is not None:
        rows = [r for r in rows if r["score"] >= min_score]
    if sub_group:
        scanned = full["scanned_by_sub"].get(sub_group, 0)
    elif group:
        scanned = full["scanned_by_group"].get(group, 0)
    else:
        scanned = full["scanned"]
    return {**full, "rows": rows, "scanned": scanned, "count": len(rows)}


# ===========================================================================
# فیلترها — Technical FILTERS (candlestick patterns + indicator states)
# ---------------------------------------------------------------------------
# A lighter cousin of STRATEGIES: instead of composite buy setups, each FILTER
# is a single, self-contained technical condition on the latest bar(s) — the
# kind of thing a TSETMC-style "فیلتر" screens for. Two families:
#   • «الگوهای کندلی» — classic Japanese candlestick patterns (پوشای صعودی/نزولی …)
#   • «اندیکاتورها»   — plain indicator states (RSI اشباع، مکدی، بولینگر، میانگین …)
# Patterns use the ADJUSTED OHLC (adj_open/high/low/close); indicators use the
# adjusted final price (adj_final) — same basis as the rest of the platform.
# ---------------------------------------------------------------------------
_SRC_CANDLE = "https://www.investopedia.com/trading/candlestick-charting-what-is-it/"

FILTER_CATEGORIES = [
    {"key": "candle", "name": "الگوهای کندلی"},
    {"key": "indicator", "name": "اندیکاتورها"},
]

FILTERS = [
    # --- الگوهای کندلی (candlestick patterns) ------------------------------
    {"key": "bull_engulf", "cat": "candle", "dir": "up", "name": "کندل پوشای صعودی",
     "desc": "الگوی بازگشتی صعودی: بدنهٔ یک کندل صعودی، بدنهٔ کندل نزولیِ روز پیش را کاملاً می‌پوشاند (Bullish Engulfing).",
     "source": _SRC_CANDLE},
    {"key": "bear_engulf", "cat": "candle", "dir": "down", "name": "کندل پوشای نزولی",
     "desc": "الگوی بازگشتی نزولی: بدنهٔ یک کندل نزولی، بدنهٔ کندل صعودیِ روز پیش را کاملاً می‌پوشاند (Bearish Engulfing).",
     "source": _SRC_CANDLE},
    {"key": "hammer", "cat": "candle", "dir": "up", "name": "چکش",
     "desc": "بازگشت صعودی پس از افت: بدنهٔ کوچک در بالای دامنه با سایهٔ پایینی بلند (دستِ‌کم دو برابر بدنه) و سایهٔ بالایی ناچیز (Hammer).",
     "source": _SRC_CANDLE},
    {"key": "shooting_star", "cat": "candle", "dir": "down", "name": "ستارهٔ ثاقب",
     "desc": "بازگشت نزولی پس از رشد: بدنهٔ کوچک در پایین دامنه با سایهٔ بالایی بلند و سایهٔ پایینی ناچیز (Shooting Star).",
     "source": _SRC_CANDLE},
    {"key": "doji", "cat": "candle", "dir": "neutral", "name": "دوجی",
     "desc": "بلاتکلیفی بازار: قیمت باز و بستهٔ کندل تقریباً برابرند (بدنهٔ بسیار کوچک نسبت به دامنهٔ روز) (Doji).",
     "source": _SRC_CANDLE},
    {"key": "piercing", "cat": "candle", "dir": "up", "name": "پوشش نافذ",
     "desc": "بازگشت صعودی دو کندلی: پس از یک کندل نزولی، کندل صعودیِ بعدی پایین‌تر باز شده و بالای میانهٔ بدنهٔ کندل قبل بسته می‌شود (Piercing Line).",
     "source": _SRC_CANDLE},
    {"key": "dark_cloud", "cat": "candle", "dir": "down", "name": "ابر سیاه پوشاننده",
     "desc": "بازگشت نزولی دو کندلی: پس از یک کندل صعودی، کندل نزولیِ بعدی بالاتر باز شده و زیر میانهٔ بدنهٔ کندل قبل بسته می‌شود (Dark Cloud Cover).",
     "source": _SRC_CANDLE},
    {"key": "morning_star", "cat": "candle", "dir": "up", "name": "ستارهٔ صبحگاهی",
     "desc": "الگوی بازگشتی صعودی سه‌کندلی: کندل نزولی بزرگ، سپس یک کندل کوچک، و در پایان کندل صعودی که تا میانهٔ کندل نخست نفوذ می‌کند (Morning Star).",
     "source": _SRC_CANDLE},
    {"key": "evening_star", "cat": "candle", "dir": "down", "name": "ستارهٔ شامگاهی",
     "desc": "الگوی بازگشتی نزولی سه‌کندلی: کندل صعودی بزرگ، سپس یک کندل کوچک، و در پایان کندل نزولی که تا میانهٔ کندل نخست نفوذ می‌کند (Evening Star).",
     "source": _SRC_CANDLE},
    {"key": "three_white", "cat": "candle", "dir": "up", "name": "سه سرباز سفید",
     "desc": "ادامهٔ صعود: سه کندل صعودیِ پیاپی که هر یک بالاتر از قبلی باز و بسته می‌شود (Three White Soldiers).",
     "source": _SRC_CANDLE},
    {"key": "three_black", "cat": "candle", "dir": "down", "name": "سه کلاغ سیاه",
     "desc": "ادامهٔ نزول: سه کندل نزولیِ پیاپی که هر یک پایین‌تر از قبلی باز و بسته می‌شود (Three Black Crows).",
     "source": _SRC_CANDLE},
    # --- اندیکاتورها (indicator states) ------------------------------------
    {"key": "rsi_oversold", "cat": "indicator", "dir": "up", "name": "اشباع فروش RSI (زیر ۳۰)",
     "desc": "شاخص قدرت نسبی با دورهٔ ۱۴ زیر ۳۰ قرار دارد — ناحیهٔ اشباع فروش که اغلب زمینه‌ساز بازگشت صعودی است.",
     "source": _SRC_RSI},
    {"key": "rsi_overbought", "cat": "indicator", "dir": "down", "name": "اشباع خرید RSI (بالای ۷۰)",
     "desc": "شاخص قدرت نسبی با دورهٔ ۱۴ بالای ۷۰ قرار دارد — ناحیهٔ اشباع خرید که ممکن است مقدمهٔ اصلاح باشد.",
     "source": _SRC_RSI},
    {"key": "macd_bull", "cat": "indicator", "dir": "up", "name": "مکدی بالای خط سیگنال",
     "desc": "خط مکدی (۱۲/۲۶/۹) بالای خط سیگنال قرار دارد — وضعیت مومنتوم مثبت.",
     "source": _SRC_MACD},
    {"key": "macd_bear", "cat": "indicator", "dir": "down", "name": "مکدی زیر خط سیگنال",
     "desc": "خط مکدی (۱۲/۲۶/۹) زیر خط سیگنال قرار دارد — وضعیت مومنتوم منفی.",
     "source": _SRC_MACD},
    {"key": "above_sma200", "cat": "indicator", "dir": "up", "name": "بالای میانگین ۲۰۰ روزه",
     "desc": "قیمت پایانی بالای میانگین متحرک سادهٔ ۲۰۰ روزه است — نشانهٔ روند بلندمدت صعودی.",
     "source": _SRC_MA},
    {"key": "below_sma200", "cat": "indicator", "dir": "down", "name": "زیر میانگین ۲۰۰ روزه",
     "desc": "قیمت پایانی زیر میانگین متحرک سادهٔ ۲۰۰ روزه است — نشانهٔ روند بلندمدت نزولی.",
     "source": _SRC_MA},
    {"key": "boll_lower", "cat": "indicator", "dir": "up", "name": "نزدیک باند پایین بولینگر",
     "desc": "قیمت پایانی روی یا نزدیک باند پایینی بولینگر (۲۰ دوره، ۲ انحراف معیار) است — احتمال اشباع فروش.",
     "source": _SRC_BOLL},
    {"key": "boll_upper", "cat": "indicator", "dir": "down", "name": "نزدیک باند بالای بولینگر",
     "desc": "قیمت پایانی روی یا نزدیک باند بالایی بولینگر (۲۰ دوره، ۲ انحراف معیار) است — احتمال اشباع خرید.",
     "source": _SRC_BOLL},
]


def _eval_filters(o, h, l, c, px):
    """Return the FILTER keys currently matched for one ticker (candlestick
    patterns on adjusted OHLC + indicator states on adjusted final price), plus a
    snapshot of indicator values for display. Patterns are read on the latest
    completed bar(s); indices [-1] newest, [-2] the bar before, etc."""
    n = len(c)
    m = []

    def body(i):
        return abs(c[i] - o[i])

    def rng(i):
        return h[i] - l[i]

    # trend context for reversal patterns (was price falling / rising into it?)
    down_ctx = n >= 6 and px[-2] < px[-6]
    up_ctx = n >= 6 and px[-2] > px[-6]

    if n >= 2:
        # 1) Bullish Engulfing — up body fully wraps the prior down body
        if (c[-2] < o[-2] and c[-1] > o[-1]
                and o[-1] <= c[-2] and c[-1] >= o[-2]
                and body(-1) > body(-2)):
            m.append("bull_engulf")
        # 2) Bearish Engulfing — down body fully wraps the prior up body
        if (c[-2] > o[-2] and c[-1] < o[-1]
                and o[-1] >= c[-2] and c[-1] <= o[-2]
                and body(-1) > body(-2)):
            m.append("bear_engulf")
        # 6) Piercing Line — bullish reversal opening below then closing >mid
        mid2 = (o[-2] + c[-2]) / 2.0
        if (c[-2] < o[-2] and c[-1] > o[-1] and o[-1] < c[-2]
                and c[-1] > mid2 and c[-1] < o[-2]):
            m.append("piercing")
        # 7) Dark Cloud Cover — bearish reversal opening above then closing <mid
        if (c[-2] > o[-2] and c[-1] < o[-1] and o[-1] > c[-2]
                and c[-1] < mid2 and c[-1] > o[-2]):
            m.append("dark_cloud")

    # single-bar shape patterns (Hammer / Shooting Star / Doji)
    r0 = rng(-1)
    if r0 > 0:
        b0 = body(-1)
        lower = min(o[-1], c[-1]) - l[-1]
        upper = h[-1] - max(o[-1], c[-1])
        # 3) Hammer — small top body, long lower shadow, after a decline
        if b0 <= 0.35 * r0 and lower >= 2 * b0 and upper <= b0 and down_ctx:
            m.append("hammer")
        # 4) Shooting Star — small bottom body, long upper shadow, after a rise
        if b0 <= 0.35 * r0 and upper >= 2 * b0 and lower <= b0 and up_ctx:
            m.append("shooting_star")
        # 5) Doji — open ≈ close
        if b0 <= 0.1 * r0:
            m.append("doji")

    if n >= 3:
        b1 = body(-3)
        mid1 = (o[-3] + c[-3]) / 2.0
        # 8) Morning Star — down, small, up-into-first-body
        if (c[-3] < o[-3] and body(-2) <= 0.5 * b1 and c[-1] > o[-1]
                and c[-1] > mid1 and max(o[-2], c[-2]) <= c[-3]):
            m.append("morning_star")
        # 9) Evening Star — up, small, down-into-first-body
        if (c[-3] > o[-3] and body(-2) <= 0.5 * b1 and c[-1] < o[-1]
                and c[-1] < mid1 and min(o[-2], c[-2]) >= c[-3]):
            m.append("evening_star")
        # 10) Three White Soldiers — three rising bullish bars
        if (c[-1] > o[-1] and c[-2] > o[-2] and c[-3] > o[-3]
                and c[-1] > c[-2] > c[-3] and o[-1] > o[-2] > o[-3]):
            m.append("three_white")
        # 11) Three Black Crows — three falling bearish bars
        if (c[-1] < o[-1] and c[-2] < o[-2] and c[-3] < o[-3]
                and c[-1] < c[-2] < c[-3] and o[-1] < o[-2] < o[-3]):
            m.append("three_black")

    # --- indicator states ---------------------------------------------------
    rsi_s = _rsi_series(px, 14)
    rsi = rsi_s[-1]
    macd, signal, _ = _macd_series(px)
    sma200 = _sma_series(px, 200)
    _, boll_up, boll_lo = _boll_series(px, 20, 2.0)
    close = px[-1]

    if rsi is not None:
        if rsi < 30:
            m.append("rsi_oversold")
        elif rsi > 70:
            m.append("rsi_overbought")
    if macd[-1] is not None and signal[-1] is not None:
        m.append("macd_bull" if macd[-1] > signal[-1] else "macd_bear")
    if sma200[-1]:
        m.append("above_sma200" if close > sma200[-1] else "below_sma200")
    if boll_up[-1] is not None and boll_lo[-1] is not None:
        band = boll_up[-1] - boll_lo[-1]
        if band > 0:
            if close <= boll_lo[-1] + 0.05 * band:
                m.append("boll_lower")
            if close >= boll_up[-1] - 0.05 * band:
                m.append("boll_upper")

    return m, {"rsi": rsi}


def _filter_scan_full(kind, as_of=None):
    """Scan every ticker of `kind` and group FILTER matches by filter key. Cached
    per (kind, as_of); the group/type filter is applied cheaply on top in
    filter_scan(). Mirrors _strategy_scan_full but fetches OHLC (patterns need it)."""
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return {"as_of": None, "by_filter": {}, "count": 0,
                "scanned": 0, "scanned_by_group": {}}
    return cache.get_or_set(
        "filter", (kind, as_of),
        lambda: (_filter_from_view(kind, as_of) if _use_view(kind, as_of)
                 else _filter_scan_live(kind, as_of)))


def _filter_scan_live(kind, as_of):
    """The technical-filter scan computed from raw OHLC — the fallback for a
    historical as_of, or a database where mv_filter_<kind> is absent."""
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = _rows(
        f"""
        WITH ranked AS (
            SELECT ticker,
                   adj_open::float  o, adj_high::float h,
                   adj_low::float   l, adj_close::float c,
                   adj_final::float v,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
            FROM {price_tbl}
            WHERE adj_close > 0 AND adj_final > 0 AND date <= %s AND date >= %s
        )
        SELECT ticker, o, h, l, c, v FROM ranked WHERE rn <= 300
        ORDER BY ticker, rn DESC
        """,
        _window(kind, as_of, 2))
    series = {}
    for r in rows:
        series.setdefault(r["ticker"], []).append(r)   # oldest→newest (rn DESC)

    if kind == "stock":
        meta = {m["ticker"]: m for m in _rows(
            "SELECT stockid AS id, ticker, name, sector, sub_sector FROM stocks")}
    else:
        meta = {m["ticker"]: m for m in _rows(
            "SELECT id, ticker, name, type FROM etf")}

    scanned = 0
    scanned_by_group = {}
    scanned_by_sub = {}
    by_filter = {f["key"]: [] for f in FILTERS}
    matched_tickers = set()
    for ticker, bars in series.items():
        if len(bars) < 30:
            continue
        meta_row = meta.get(ticker)
        if not meta_row:
            continue
        grp = meta_row.get("sector") if kind == "stock" else meta_row.get("type")
        sub = meta_row.get("sub_sector") if kind == "stock" else None
        scanned += 1
        scanned_by_group[grp] = scanned_by_group.get(grp, 0) + 1
        if sub:
            scanned_by_sub[sub] = scanned_by_sub.get(sub, 0) + 1
        o = [b["o"] for b in bars]
        h = [b["h"] for b in bars]
        low = [b["l"] for b in bars]
        c = [b["c"] for b in bars]
        px = [b["v"] for b in bars]
        keys, ind = _eval_filters(o, h, low, c, px)
        if not keys:
            continue
        row = {"id": meta_row["id"], "ticker": ticker, "name": meta_row["name"],
               "group": grp, "sub_group": sub, "latest": px[-1],
               "rsi": ind["rsi"], "matches": keys}
        matched_tickers.add(ticker)
        for k in keys:
            by_filter[k].append(row)

    for k in by_filter:
        by_filter[k].sort(key=lambda r: (r["rsi"] if r["rsi"] is not None else 999))

    return {"as_of": as_of, "by_filter": by_filter, "count": len(matched_tickers),
            "scanned": scanned, "scanned_by_group": scanned_by_group,
            "scanned_by_sub": scanned_by_sub}


def filter_scan(kind, as_of=None, group=None, sub_group=None):
    """Full technical-filter scan, optionally narrowed to one industry group
    (stocks) or ETF type, and further to a sub-group (زیرگروه). Filtering is
    applied to the cached full scan so every group shares one computation."""
    full = _filter_scan_full(kind, as_of)
    if not group and not sub_group:
        return full

    def keep(r):
        return ((not group or r["group"] == group) and
                (not sub_group or r.get("sub_group") == sub_group))

    by = {k: [r for r in rows if keep(r)]
          for k, rows in full["by_filter"].items()}
    matched = {r["ticker"] for rows in by.values() for r in rows}
    if sub_group:
        scanned = full["scanned_by_sub"].get(sub_group, 0)
    else:
        scanned = full["scanned_by_group"].get(group, 0)
    return {**full, "by_filter": by, "count": len(matched), "scanned": scanned}


# ---------------------------------------------------------------------------
# Search + entity lookup
# ---------------------------------------------------------------------------
def search(q, limit=30):
    like = f"%{q}%"
    stocks = _rows(
        """SELECT stockid AS id, ticker, name, market FROM stocks
           WHERE ticker ILIKE %s OR name ILIKE %s ORDER BY ticker LIMIT %s""",
        (like, like, limit))
    etfs = _rows(
        """SELECT id, ticker, name, type FROM etf
           WHERE ticker ILIKE %s OR name ILIKE %s ORDER BY ticker LIMIT %s""",
        (like, like, limit))
    out = [dict(kind="stock", sub=r["market"], **r) for r in stocks]
    out += [dict(kind="etf", sub=r["type"], **r) for r in etfs]
    return out[:limit]


def get_stock(stock_id):
    return _one(
        "SELECT stockid AS id, ticker, name, market, sector, sub_sector FROM stocks WHERE stockid=%s",
        (stock_id,))


def get_etf(etf_id):
    return _one("SELECT id, ticker, name, type, comment FROM etf WHERE id=%s", (etf_id,))


def _history(kind, ticker):
    """Full adjusted-price history (oldest→newest) for one ticker."""
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    return _rows(
        f"""SELECT j_date, adj_final::float AS px, close::float AS close,
                   volume, value
            FROM {tbl} WHERE ticker=%s AND adj_final>0 ORDER BY date""",
        (ticker,))


# ===========================================================================
# خلاصهٔ تکنیکال — a TradingView/Investing.com-style Technical Summary.
#
# Rates 11 oscillators + 15 moving averages each as Buy/Sell/Neutral, then
# aggregates them (each ±1/0) into an overall خرید قوی…فروش قوی gauge — plus
# classic & Fibonacci pivot points (support/resistance), ATR and volume stats.
# Standard, widely-documented formulas. Educational — NOT financial advice.
# Method reference: TradingView «Technical Ratings»; pivots: Investopedia.
# ===========================================================================
def _wma_series(px, n):
    out = [None] * len(px)
    if n < 1 or len(px) < n:
        return out
    denom = n * (n + 1) / 2.0
    for i in range(n - 1, len(px)):
        s = 0.0
        for j in range(n):
            s += px[i - n + 1 + j] * (j + 1)
        out[i] = s / denom
    return out


def _hma_last(px, n):
    """Hull MA (last value): WMA(2·WMA(n/2) − WMA(n), √n)."""
    import math
    if len(px) < n:
        return None
    half, full = _wma_series(px, max(1, n // 2)), _wma_series(px, n)
    raw = [(2 * h - f) if (h is not None and f is not None) else None
           for h, f in zip(half, full)]
    vals = [v for v in raw if v is not None]
    sq = max(1, int(round(math.sqrt(n))))
    w = _wma_series(vals, sq)
    return w[-1] if w else None


def _vwma_last(px, vol, n):
    if len(px) < n:
        return None
    pv = sum(px[i] * (vol[i] or 0) for i in range(len(px) - n, len(px)))
    vv = sum((vol[i] or 0) for i in range(len(px) - n, len(px)))
    return pv / vv if vv else None


def _sma_of(series, n):
    """SMA of a series that may carry leading None (indicator-of-indicator)."""
    out = [None] * len(series)
    for i in range(n - 1, len(series)):
        w = series[i - n + 1:i + 1]
        if all(x is not None for x in w):
            out[i] = sum(w) / n
    return out


def _cci_series(H, L, C, n=20):
    tp = [(h + l + c) / 3 for h, l, c in zip(H, L, C)]
    out = [None] * len(tp)
    for i in range(n - 1, len(tp)):
        w = tp[i - n + 1:i + 1]
        m = sum(w) / n
        md = sum(abs(x - m) for x in w) / n
        out[i] = 0.0 if md == 0 else (tp[i] - m) / (0.015 * md)
    return out


def _stoch_kd(H, L, C, n=14, ks=3, ds=3):
    raw = [None] * len(C)
    for i in range(n - 1, len(C)):
        hh, ll = max(H[i - n + 1:i + 1]), min(L[i - n + 1:i + 1])
        raw[i] = 50.0 if hh == ll else (C[i] - ll) / (hh - ll) * 100.0
    k = _sma_of(raw, ks)
    d = _sma_of(k, ds)
    return k, d


def _willr_series(H, L, C, n=14):
    out = [None] * len(C)
    for i in range(n - 1, len(C)):
        hh, ll = max(H[i - n + 1:i + 1]), min(L[i - n + 1:i + 1])
        out[i] = -50.0 if hh == ll else (hh - C[i]) / (hh - ll) * -100.0
    return out


def _ao_series(H, L):
    med = [(h + l) / 2 for h, l in zip(H, L)]
    f, s = _sma_series(med, 5), _sma_series(med, 34)
    return [(a - b) if (a is not None and b is not None) else None for a, b in zip(f, s)]


def _uo_last(H, L, C):
    n = len(C)
    if n < 29:
        return None
    bp, tr = [0.0] * n, [0.0] * n
    for i in range(1, n):
        bp[i] = C[i] - min(L[i], C[i - 1])
        tr[i] = max(H[i], C[i - 1]) - min(L[i], C[i - 1])
    def avg(p):
        sb, st = sum(bp[-p:]), sum(tr[-p:])
        return (sb / st) if st else 0.0
    return 100.0 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7.0


def _atr_last(H, L, C, n=14):
    if len(C) <= n:
        return None
    tr = [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
          for i in range(1, len(C))]
    atr = sum(tr[:n]) / n
    for x in tr[n:]:
        atr = (atr * (n - 1) + x) / n
    return atr


def _adx_last(H, L, C, n=14):
    """Return (adx, +DI, −DI) at the last bar via Wilder smoothing."""
    m = len(C)
    if m <= 2 * n:
        return None, None, None
    tr = [0.0] * m; pdm = [0.0] * m; ndm = [0.0] * m
    for i in range(1, m):
        up, dn = H[i] - H[i - 1], L[i - 1] - L[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
    def rma(x):
        out = [None] * m
        s = sum(x[1:n + 1])
        out[n] = s
        for i in range(n + 1, m):
            s = s - s / n + x[i]
            out[i] = s
        return out
    trS, pdmS, ndmS = rma(tr), rma(pdm), rma(ndm)
    dx = [None] * m
    pdi = ndi = None
    for i in range(n, m):
        if trS[i]:
            pdi = 100 * pdmS[i] / trS[i]
            ndi = 100 * ndmS[i] / trS[i]
            tot = pdi + ndi
            dx[i] = (100 * abs(pdi - ndi) / tot) if tot else 0.0
    vals = [dx[i] for i in range(m) if dx[i] is not None]
    if len(vals) < n:
        return None, pdi, ndi
    a = sum(vals[:n]) / n
    for v in vals[n:]:
        a = (a * (n - 1) + v) / n
    return a, pdi, ndi


# each rater returns +1 (buy) / 0 (neutral) / -1 (sell)
def _rate_buy_sell(buy, sell):
    return 1 if buy else (-1 if sell else 0)


def technical_summary(kind, ticker):
    """Full technical-summary payload for one security (see section header)."""
    candles = ohlc_history(kind, ticker)
    if len(candles) < 40:
        return None
    # Data-quality guard: many TSE symbols carry only the adjusted CLOSE — their
    # open/high/low arrive as 0 (or equal to close). Fall back to close so the
    # window oscillators stay valid and nothing divides by a zero range.
    C = [c["close"] for c in candles]
    H = [(c["high"] if c["high"] and c["high"] > 0 else c["close"]) for c in candles]
    L = [(c["low"] if c["low"] and c["low"] > 0 else c["close"]) for c in candles]
    O = [(c["open"] if c["open"] and c["open"] > 0 else c["close"]) for c in candles]
    V = [c["volume"] for c in candles]
    price = C[-1]

    def prev(series):
        return (series[-1], series[-2] if len(series) > 1 else None)

    # ---- oscillators (11) --------------------------------------------------
    osc = []
    rsi_s = _rsi_series(C, 14); r, rp = prev(rsi_s)
    if r is not None:
        osc.append(("RSI (۱۴)", r, _rate_buy_sell(r < 30 and rp is not None and r > rp,
                                                   r > 70 and rp is not None and r < rp)))
    k_s, d_s = _stoch_kd(H, L, C); k, d = k_s[-1], d_s[-1]
    if k is not None and d is not None:
        osc.append(("استوکاستیک (۱۴،۳،۳)", k,
                    _rate_buy_sell(k < 20 and d < 20 and k > d, k > 80 and d > 80 and k < d)))
    cci_s = _cci_series(H, L, C, 20); cc, ccp = prev(cci_s)
    if cc is not None:
        osc.append(("CCI (۲۰)", cc, _rate_buy_sell(cc < -100 and ccp is not None and cc > ccp,
                                                    cc > 100 and ccp is not None and cc < ccp)))
    adx, pdi, ndi = _adx_last(H, L, C, 14)
    if adx is not None and pdi is not None:
        osc.append(("ADX (۱۴)", adx, _rate_buy_sell(adx > 20 and pdi > ndi, adx > 20 and pdi < ndi)))
    ao_s = _ao_series(H, L); ao, aop = prev(ao_s)
    if ao is not None:
        osc.append(("اوسام (AO)", ao, _rate_buy_sell(ao > 0 and aop is not None and ao > aop,
                                                     ao < 0 and aop is not None and ao < aop)))
    if len(C) > 11:
        mom = C[-1] - C[-11]; momp = C[-2] - C[-12]
        osc.append(("مومنتوم (۱۰)", mom, _rate_buy_sell(mom > momp, mom < momp)))
    macd_l, sig_l, _ = _macd_series(C)
    if macd_l[-1] is not None and sig_l[-1] is not None:
        osc.append(("مکدی (۱۲،۲۶،۹)", macd_l[-1] - sig_l[-1],
                    _rate_buy_sell(macd_l[-1] > sig_l[-1], macd_l[-1] < sig_l[-1])))
    # Stochastic RSI (3,3,14,14)
    rsi14 = [x for x in rsi_s if x is not None]
    if len(rsi14) >= 17:
        srraw = [None] * len(rsi14)
        for i in range(13, len(rsi14)):
            hh, ll = max(rsi14[i - 13:i + 1]), min(rsi14[i - 13:i + 1])
            srraw[i] = 50.0 if hh == ll else (rsi14[i] - ll) / (hh - ll) * 100.0
        sk = _sma_of(srraw, 3); sd = _sma_of(sk, 3)
        if sk[-1] is not None and sd[-1] is not None:
            osc.append(("استوکاستیک RSI", sk[-1],
                        _rate_buy_sell(sk[-1] < 20 and sk[-1] > sd[-1], sk[-1] > 80 and sk[-1] < sd[-1])))
    wr_s = _willr_series(H, L, C, 14); wr, wrp = prev(wr_s)
    if wr is not None:
        osc.append(("ویلیامز ٪R (۱۴)", wr, _rate_buy_sell(wr < -80 and wrp is not None and wr > wrp,
                                                          wr > -20 and wrp is not None and wr < wrp)))
    uo = _uo_last(H, L, C)
    if uo is not None:
        osc.append(("اولتیمیت (۷،۱۴،۲۸)", uo, _rate_buy_sell(uo > 70, uo < 30)))
    ema13 = _ema_series(C, 13)
    if ema13[-1] is not None and ema13[-2] is not None:
        bull, bear = H[-1] - ema13[-1], L[-1] - ema13[-1]
        bullp, bearp = H[-2] - ema13[-2], L[-2] - ema13[-2]
        osc.append(("قدرت خرید/فروش (۱۳)", bull + bear,
                    _rate_buy_sell(ema13[-1] > ema13[-2] and bear < 0 and bear > bearp,
                                   ema13[-1] < ema13[-2] and bull > 0 and bull < bullp)))

    # ---- moving averages (15) ---------------------------------------------
    ma = []
    for p in (10, 20, 30, 50, 100, 200):
        v = _sma_series(C, p)[-1]
        if v is not None:
            ma.append((f"SMA {p}", v, _rate_buy_sell(price > v, price < v)))
    for p in (10, 20, 30, 50, 100, 200):
        v = _ema_series(C, p)[-1]
        if v is not None:
            ma.append((f"EMA {p}", v, _rate_buy_sell(price > v, price < v)))
    hma = _hma_last(C, 9)
    if hma is not None:
        ma.append(("HMA ۹", hma, _rate_buy_sell(price > hma, price < hma)))
    vwma = _vwma_last(C, V, 20)
    if vwma is not None:
        ma.append(("VWMA ۲۰", vwma, _rate_buy_sell(price > vwma, price < vwma)))
    if len(C) >= 26:
        conv = (max(H[-9:]) + min(L[-9:])) / 2
        base = (max(H[-26:]) + min(L[-26:])) / 2
        ma.append(("ایچیموکو (پایه)", base,
                   _rate_buy_sell(price > base and conv > base, price < base and conv < base)))

    def summarize(items):
        buy = sum(1 for _, _, s in items if s > 0)
        sell = sum(1 for _, _, s in items if s < 0)
        neu = sum(1 for _, _, s in items if s == 0)
        avg = (sum(s for _, _, s in items) / len(items)) if items else 0.0
        return {"buy": buy, "sell": sell, "neutral": neu, "avg": avg,
                "rating": _tv_rating(avg)}

    osc_sum, ma_sum = summarize(osc), summarize(ma)
    overall_avg = ((osc_sum["avg"] + ma_sum["avg"]) / 2) if (osc and ma) else \
                  (osc_sum["avg"] if osc else ma_sum["avg"])

    # ---- pivots — from the most recent bar that has a real intraday range ---
    # (limit-days «صف» and close-only symbols have H==L, which collapses pivots;
    # skip back to the last bar with H>L, else omit pivots for this symbol).
    piv_i = None
    for i in range(len(C) - 1, max(-1, len(C) - 61), -1):
        if H[i] > L[i]:
            piv_i = i
            break
    pivots = None
    # Fall back to the final bar when no pivot bar exists. A symbol whose last 60
    # bars are ALL limit-days («صف», H == L) leaves piv_i None — pivots are
    # correctly omitted, but "last" below still needs values, and reading the
    # unassigned ph/pl/pc used to raise UnboundLocalError and 500 the whole
    # detail page (e.g. درازی, ثروت). Pre-existing; unrelated to the date/numeric
    # migration, but it breaks the same pages so it is fixed here.
    ph, pl, pc = H[-1], L[-1], C[-1]
    if piv_i is not None:
        ph, pl, pc = H[piv_i], L[piv_i], C[piv_i]
        P = (ph + pl + pc) / 3
        rng = ph - pl
        pivots = {
            "classic": {"P": P,
                        "R1": 2 * P - pl, "R2": P + rng, "R3": ph + 2 * (P - pl),
                        "S1": 2 * P - ph, "S2": P - rng, "S3": pl - 2 * (ph - P)},
            "fibonacci": {"P": P,
                          "R1": P + 0.382 * rng, "R2": P + 0.618 * rng, "R3": P + rng,
                          "S1": P - 0.382 * rng, "S2": P - 0.618 * rng, "S3": P - rng},
        }

    atr = _atr_last(H, L, C, 14)
    vol_avg = (sum(v or 0 for v in V[-20:]) / min(20, len(V))) if V else None
    vol_last = V[-1] if V else None

    return {
        "oscillators": [{"name": n, "value": v, "signal": s} for n, v, s in osc],
        "moving_averages": [{"name": n, "value": v, "signal": s} for n, v, s in ma],
        "osc": osc_sum, "ma": ma_sum,
        "overall": {**_tv_rating(overall_avg), "avg": overall_avg,
                    "buy": osc_sum["buy"] + ma_sum["buy"],
                    "sell": osc_sum["sell"] + ma_sum["sell"],
                    "neutral": osc_sum["neutral"] + ma_sum["neutral"]},
        "pivots": pivots,
        "atr": atr, "vol_avg": vol_avg, "vol_last": vol_last,
        "vol_ratio": (vol_last / vol_avg) if (vol_avg and vol_last) else None,
        "last": {"high": ph, "low": pl, "close": pc},
    }


def _tv_rating(avg):
    """Map an aggregate ±1 signal average to the TradingView-style verdict."""
    if avg >= 0.5:
        return {"key": "strong_buy", "label": "خرید قوی", "tone": "pos"}
    if avg >= 0.1:
        return {"key": "buy", "label": "خرید", "tone": "pos"}
    if avg > -0.1:
        return {"key": "neutral", "label": "خنثی", "tone": "mid"}
    if avg > -0.5:
        return {"key": "sell", "label": "فروش", "tone": "neg"}
    return {"key": "strong_sell", "label": "فروش قوی", "tone": "neg"}


def ohlc_history(kind, ticker):
    """Full adjusted OHLCV candle history (oldest→newest) for one ticker.

    Feeds the professional chart and the raw-data history table. Uses the
    ADJUSTED series (adj_open/high/low/close) so splits/dividends don't create
    artificial gaps; `final` (weighted close) and traded value/volume come along
    for the tooltip and history table. `date` is the Gregorian calendar date —
    the chart needs a real timestamp for its x-axis, while `j_date` (Jalali) is
    what we actually show the user.
    """
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = _rows(
        f"""SELECT date, j_date,
                   adj_open::float  AS open,
                   adj_high::float  AS high,
                   adj_low::float   AS low,
                   adj_close::float AS close,
                   adj_final::float AS final,
                   volume, value
            FROM {tbl}
            WHERE ticker=%s AND adj_close>0
            ORDER BY date""",
        (ticker,))
    out = []
    for r in rows:
        d = r["date"]
        # ms-epoch timestamp from the Gregorian date (chart x-axis is time-based)
        ts = int(time.mktime(d.timetuple()) * 1000) if d else None
        out.append({
            "timestamp": ts,
            "jdate": r["j_date"],
            "open": r["open"], "high": r["high"],
            "low": r["low"], "close": r["close"],
            "final": r["final"],
            "volume": int(r["volume"]) if r["volume"] is not None else 0,
            "turnover": int(r["value"]) if r["value"] is not None else 0,
        })
    return out


def tv_bars(kind, ticker):
    """Daily OHLCV bars for the TradingView (UDF) datafeed, ascending.

    `t` is UNIX time in SECONDS at 00:00 UTC of each Gregorian trading date
    (calendar.timegm avoids local-timezone drift). Uses the ADJUSTED series so
    splits/dividends don't create artificial gaps — same basis as the rest of
    the platform's charts.
    """
    import calendar
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = _rows(
        f"""SELECT date,
                   adj_open::float  AS o, adj_high::float AS h,
                   adj_low::float   AS l, adj_close::float AS c,
                   volume AS v
            FROM {tbl}
            WHERE ticker=%s AND adj_close>0 AND date IS NOT NULL
            ORDER BY date""",
        (ticker,))
    out = []
    for r in rows:
        d = r["date"]
        t = calendar.timegm((d.year, d.month, d.day, 0, 0, 0, 0, 0, 0))
        out.append({"t": t, "o": r["o"], "h": r["h"], "l": r["l"],
                    "c": r["c"], "v": int(r["v"]) if r["v"] is not None else 0})
    return out


def name_for_ticker(kind, ticker):
    """Human-readable name for a ticker (for TradingView symbol resolution)."""
    if kind == "etf":
        r = _one("SELECT name FROM etf WHERE ticker=%s", (ticker,))
    else:
        r = _one("SELECT name FROM stocks WHERE ticker=%s", (ticker,))
    return r["name"] if r else ticker

# ---------------------------------------------------------------------------
# Per-security deep analysis (mirrors search.py)
# ---------------------------------------------------------------------------
def _pct(cur, base):
    if cur is None or base in (None, 0):
        return None
    return (cur - base) / base * 100.0


def security_analysis(kind, ticker):
    hist = _history(kind, ticker)
    if len(hist) < 2:
        return None
    px = [h["px"] for h in hist]
    dates = [h["j_date"] for h in hist]
    latest = px[-1]
    latest_date_ = dates[-1]

    # period gains + ceil/floor (positional, oldest→newest)
    periods = []
    for per in PERIODS:
        n = per["n"]
        if len(px) > n:
            window = px[-(n + 1):]
            past = window[0]
            mx, mn = max(window), min(window)
            periods.append({
                "label": per["label"], "n": n,
                "gain": _pct(latest, past),
                "ceil": _pct(latest, mx), "floor": _pct(latest, mn),
                "start_date": dates[-(n + 1)],
            })
        else:
            periods.append({"label": per["label"], "n": n,
                            "gain": None, "ceil": None, "floor": None, "start_date": None})

    # from the very first record in the DB
    first = {
        "gain": _pct(latest, px[0]),
        "ceil": _pct(latest, max(px)),
        "floor": _pct(latest, min(px)),
        "start_date": dates[0],
    }

    # year-over-year: same date N Jalali years back → today (nearest ≤ target)
    yoy = []
    for yb in (1, 2, 3):
        target = _same_date_years_back(latest_date_, yb)
        idx = _nearest_le(dates, target)
        if idx is not None:
            yoy.append({"years": yb, "date": dates[idx], "gain": _pct(latest, px[idx])})
        else:
            yoy.append({"years": yb, "date": None, "gain": None})

    # day change
    day_change = _pct(latest, px[-2]) if len(px) >= 2 else None

    # chart series — last 500 trading days
    tail = hist[-500:]
    chart = {"labels": [h["j_date"] for h in tail], "px": [h["px"] for h in tail]}

    # composite technical score + verdict + risk metrics (the «should I buy?» box)
    signal = signal_score(px, dates=dates, kind=kind, as_of=latest_date_)

    return {
        "ticker": ticker, "latest": latest, "latest_date": latest_date_,
        "day_change": day_change, "points": len(px),
        "periods": periods, "first": first, "yoy": yoy, "chart": chart,
        "high": max(px), "low": min(px), "signal": signal,
    }


def _same_date_years_back(jdate, n):
    y = int(jdate[:4]) - n
    return f"{y:04d}{jdate[4:]}"


def _nearest_le(dates, target):
    """Index of the newest date <= target (dates is ascending). None if none."""
    import bisect
    i = bisect.bisect_right(dates, target)
    return i - 1 if i > 0 else None


# ===========================================================================
# Users / authentication (multi-user platform)
# ---------------------------------------------------------------------------
# A single `users` table lives alongside the market data in the same PostgreSQL
# database. Passwords are stored ONLY as a salted Werkzeug hash (see auth.py) —
# never in plaintext. `role` gates admin-only pages (e.g. the data-update page).
# ===========================================================================
def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# Every user_prefs column beyond the original seven, as (name, DDL). Kept as a
# list rather than inline ALTERs so adding a preference is a one-line change in
# exactly two files (prefs.DEFAULTS and here) plus the migration — and so the
# migration and the boot-time ensure cannot disagree about a column's default.
_PREF_COLUMNS = [
    ("font_scale",     "TEXT NOT NULL DEFAULT 'md'"),
    ("top_scrollbar",  "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("scrollbar_size", "TEXT NOT NULL DEFAULT 'lg'"),
    ("sticky_head",    "BOOLEAN NOT NULL DEFAULT TRUE"),
    ("zebra",          "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("updown_scheme",  "TEXT NOT NULL DEFAULT 'classic'"),
    ("auto_refresh",   "INTEGER NOT NULL DEFAULT 0"),
    ("wide",           "BOOLEAN NOT NULL DEFAULT FALSE"),
]


def init_db():
    """Create the `users` table if it does not yet exist. Safe to call on every
    boot (idempotent). Adds the OAuth / role columns to pre-existing tables."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id            BIGSERIAL PRIMARY KEY,
                    username      TEXT NOT NULL UNIQUE,
                    display_name  TEXT NOT NULL DEFAULT '',
                    password_hash TEXT NOT NULL DEFAULT '',
                    email         TEXT DEFAULT '',
                    google_id     TEXT,
                    role          TEXT NOT NULL DEFAULT 'user',
                    created_at    TEXT NOT NULL,
                    last_login    TEXT DEFAULT ''
                )
                """
            )
            # Columns for databases created before these features existed.
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT DEFAULT ''")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS google_id TEXT")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'user'")
            # A Google account maps to exactly one user (NULLs stay distinct).
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google ON users(google_id)")
            # Per-user watchlist («دیده‌بان») — starred stocks / ETFs.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist (
                    id         BIGSERIAL PRIMARY KEY,
                    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    kind       TEXT NOT NULL,
                    ticker     TEXT NOT NULL,
                    entity_id  BIGINT,
                    created_at TEXT NOT NULL,
                    UNIQUE (user_id, kind, ticker)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlist(user_id)")
            # Per-user settings («تنظیمات») — one row, updated in place, never an
            # event log: nothing is ever asked of this table except "what does
            # this user see right now". The column defaults MUST equal
            # prefs.DEFAULTS; if they drift, a fresh account and an account that
            # saved-then-reset render differently, which is the kind of bug that
            # is reported as "the site looks different on my laptop".
            #
            # The DDL is duplicated in migrations/versions/0005_user_prefs_and_
            # screens.py on purpose, exactly as jobs.ensure_tables() duplicates
            # the order-06 tables: this keeps `python app.py` working against a
            # database nobody has run Alembic on, which is every developer
            # laptop and the first boot of a fresh deployment.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_prefs (
                    user_id        BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    theme          TEXT    NOT NULL DEFAULT 'light',
                    digits         TEXT    NOT NULL DEFAULT 'fa',
                    default_kind   TEXT    NOT NULL DEFAULT 'stock',
                    rows_per_page  INTEGER NOT NULL DEFAULT 50,
                    default_period TEXT    NOT NULL DEFAULT 'p20',
                    density        TEXT    NOT NULL DEFAULT 'comfortable',
                    reduce_motion  BOOLEAN NOT NULL DEFAULT FALSE,
                    font_scale     TEXT    NOT NULL DEFAULT 'md',
                    top_scrollbar  BOOLEAN NOT NULL DEFAULT TRUE,
                    scrollbar_size TEXT    NOT NULL DEFAULT 'lg',
                    sticky_head    BOOLEAN NOT NULL DEFAULT TRUE,
                    zebra          BOOLEAN NOT NULL DEFAULT FALSE,
                    updown_scheme  TEXT    NOT NULL DEFAULT 'classic',
                    auto_refresh   INTEGER NOT NULL DEFAULT 0,
                    wide           BOOLEAN NOT NULL DEFAULT FALSE,
                    updated_at     TEXT    NOT NULL
                )
                """
            )
            # Columns for databases created before a preference existed. Adding a
            # preference is: one key in prefs.DEFAULTS, one line here, one line in
            # the migration — never a backfill, because db.get_prefs() merges the
            # stored row UNDER prefs.DEFAULTS.
            for _col, _ddl in _PREF_COLUMNS:
                cur.execute(f"ALTER TABLE user_prefs ADD COLUMN IF NOT EXISTS {_col} {_ddl}")
            # Saved filter presets («غربالگرهای ذخیره‌شده»). The query string is
            # stored verbatim rather than parsed into columns: the filters on
            # those pages change shape as the platform grows, and a preset that
            # is just "the URL that worked" cannot go stale in a way that needs a
            # migration.
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS saved_screens (
                    id         BIGSERIAL PRIMARY KEY,
                    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name       TEXT NOT NULL,
                    kind       TEXT NOT NULL,
                    page       TEXT NOT NULL,
                    query      TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE (user_id, name)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_screens_user ON saved_screens(user_id, id DESC)")
        conn.commit()
    finally:
        release(conn)


def _user_row(sql, params=()):
    """Run a SELECT and return the first row as a plain dict (or None)."""
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        release(conn)


def create_user(username, password_hash, display_name="", role="user"):
    """Insert a password user. Returns the new id, or None if the username is
    taken (unique violation)."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, display_name, password_hash, role, created_at)"
                " VALUES (%s,%s,%s,%s,%s) RETURNING id",
                (username, display_name or username, password_hash, role, _utcnow()),
            )
            uid = cur.fetchone()[0]
        conn.commit()
        return uid
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return None
    finally:
        release(conn)


def create_oauth_user(username, email, google_id, display_name="", role="user"):
    """Create a user who signs in through Google (no local password)."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, display_name, password_hash, email, google_id, role, created_at)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (username, display_name or username, "", email or "", google_id, role, _utcnow()),
            )
            uid = cur.fetchone()[0]
        conn.commit()
        return uid
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return None
    finally:
        release(conn)


def get_user(user_id):
    return _user_row("SELECT * FROM users WHERE id = %s", (user_id,))


def get_user_by_username(username):
    return _user_row("SELECT * FROM users WHERE username = %s", (username,))


def get_user_by_google_id(google_id):
    return _user_row("SELECT * FROM users WHERE google_id = %s", (google_id,))


def get_user_by_email(email):
    if not email:
        return None
    return _user_row(
        "SELECT * FROM users WHERE lower(email) = lower(%s) ORDER BY id ASC LIMIT 1",
        (email,),
    )


def link_google(user_id, google_id, email=""):
    """Attach a Google identity to an existing password account."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET google_id = %s, email = COALESCE(NULLIF(email,''), %s) WHERE id = %s",
                (google_id, email or "", user_id),
            )
        conn.commit()
    finally:
        release(conn)


def touch_user_login(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET last_login = %s WHERE id = %s", (_utcnow(), user_id))
        conn.commit()
    finally:
        release(conn)


def count_users():
    """How many accounts exist — used to make the very first registrant an admin."""
    row = _user_row("SELECT COUNT(*) AS n FROM users")
    return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Watchlist («دیده‌بان») — each user stars stocks / ETFs to follow.
# ---------------------------------------------------------------------------
def toggle_watch(user_id, kind, ticker, entity_id=None):
    """Star ⇄ un-star a symbol. Returns True if it is now watched, False if it
    was removed. Idempotent per (user, kind, ticker)."""
    if kind not in ("stock", "etf") or not ticker:
        raise ValueError("bad kind/ticker")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM watchlist WHERE user_id=%s AND kind=%s AND ticker=%s",
                (user_id, kind, ticker),
            )
            if cur.rowcount:                       # was there → removed
                conn.commit()
                return False
            cur.execute(
                "INSERT INTO watchlist (user_id, kind, ticker, entity_id, created_at)"
                " VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (user_id, kind, ticker, entity_id, _utcnow()),
            )
        conn.commit()
        return True
    finally:
        release(conn)


def watch_keys(user_id):
    """Set of "kind:ticker" strings the user follows — for marking stars fast."""
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT kind, ticker FROM watchlist WHERE user_id=%s", (user_id,))
            return {f"{k}:{t}" for (k, t) in cur.fetchall()}
    finally:
        release(conn)


def watch_count(user_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM watchlist WHERE user_id=%s", (user_id,))
            return int(cur.fetchone()[0])
    finally:
        release(conn)


# ===========================================================================
# تنظیمات کاربر و غربالگرهای ذخیره‌شده — prefs, saved screens
# ---------------------------------------------------------------------------
# `prefs.py` decides what a preference MAY be; this section only stores it. The
# import is safe and intended: prefs.py is pure (no Flask, no db, no network),
# so there is no import cycle to create.
# ===========================================================================
import prefs as _prefs                      # noqa: E402  (bottom-of-file section)

# The stored preference columns, taken from prefs.DEFAULTS so the two cannot
# drift: a SELECT * here must never hand `updated_at` to the browser, and adding
# a preference must not mean remembering to extend a second list.
_PREF_KEYS = tuple(_prefs.DEFAULTS.keys())


def get_prefs(user_id):
    """This user's settings, merged UNDER prefs.DEFAULTS.

    A user with no row gets the defaults — not None, and not an empty dict.
    That is what makes adding a preference a one-line change: every account,
    including ones created long before the preference existed, answers with the
    default until they save something else.
    """
    row = _user_row("SELECT * FROM user_prefs WHERE user_id = %s", (user_id,))
    stored = {k: row[k] for k in _PREF_KEYS if row and k in row} if row else {}
    return _prefs.payload(stored)


def set_prefs(user_id, values):
    """UPSERT the settings this call carries and return the full merged payload.

    Only the keys present in `values` are written. The settings screen saves one
    control at a time (each switch PATCHes on change), and a whole-row write
    would let two tabs open on that screen overwrite each other with whatever
    each had on display when it loaded.
    """
    clean = _prefs.normalize(values)
    if not clean:                            # nothing recognised → nothing to write
        return get_prefs(user_id)
    cols = list(clean.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
    sql = (f"INSERT INTO user_prefs (user_id, {', '.join(cols)}, updated_at) "
           f"VALUES (%s, {placeholders}, %s) "
           f"ON CONFLICT (user_id) DO UPDATE SET {updates}, updated_at = EXCLUDED.updated_at")
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (user_id, *[clean[c] for c in cols], _utcnow()))
        conn.commit()
    finally:
        release(conn)
    return get_prefs(user_id)


def reset_prefs(user_id):
    """Forget this user's settings entirely (back to prefs.DEFAULTS).

    Deleting the row rather than writing today's defaults into it matters: if a
    default changes later it should reach an account that never expressed a
    preference, and a row full of the old defaults would freeze the old look in
    place forever.
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_prefs WHERE user_id = %s", (user_id,))
        conn.commit()
    finally:
        release(conn)
    return get_prefs(user_id)


def list_screens(user_id):
    """The user's saved filter presets, newest first."""
    return _rows(
        "SELECT id, name, kind, page, query, created_at FROM saved_screens "
        "WHERE user_id = %s ORDER BY id DESC", (user_id,))


def get_screen(screen_id):
    return _user_row("SELECT * FROM saved_screens WHERE id = %s", (screen_id,))


def create_screen(user_id, name, kind, page, query):
    """Save a preset. Returns the new row, or None if the name is already taken.

    None rather than an exception because "you already have a preset with this
    name" is something the user acts on, not a failure: the caller turns it into
    a 409 carrying that sentence.
    """
    conn = get_db()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO saved_screens (user_id, name, kind, page, query, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
                (user_id, name, kind, page, query or "", _utcnow()))
            row = dict(cur.fetchone())
        conn.commit()
        return row
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return None
    finally:
        release(conn)


def delete_screen(screen_id):
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM saved_screens WHERE id = %s", (screen_id,))
            gone = bool(cur.rowcount)
        conn.commit()
        return gone
    finally:
        release(conn)


def count_screens(user_id):
    row = _one("SELECT COUNT(*) AS n FROM saved_screens WHERE user_id = %s", (user_id,))
    return int(row["n"]) if row else 0


# ===========================================================================
# نقشهٔ بازار و نبض بازار — market map (heat map) and breadth
# ---------------------------------------------------------------------------
# Both read the SAME cached gainer rows every other screen reads, plus one extra
# query for the last session's volume/value. Nothing here adds a materialized
# view: the percentages already exist, and the only thing missing was a measure
# of SIZE — a heat map whose tiles are all the same size ranks nothing, because
# a ۵٪ move in a symbol nobody traded is not the same event as a ۵٪ move in the
# most-traded symbol of the day.
# ===========================================================================
def last_session(kind, as_of=None):
    """{ticker: {"jdate","value","volume","chg"}} for the most recent session
    at/at-or-before `as_of`.

    `chg` is the ONE-DAY change, which nothing else in this module reports:
    PERIODS starts at five trading days. It is computed here from the two most
    recent bars instead of being added to PERIODS on purpose — a new period key
    changes the column list of mv_market_gainer_*, and until those views were
    rebuilt every market page would fail on a missing column. This query touches
    no view at all, so it is safe to add to a running deployment.

    The window is 30 calendar days rather than the two years the gainer scan
    uses: the answer needs two rows per ticker, and a two-year slice would read
    ~۵۰۰ برابر the rows to find them. Thirty days clears the longest تعطیلات
    (نوروز) with room to spare, so the second bar is always inside it.
    """
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    if as_of is None:
        as_of = latest_date(kind)
    if as_of is None:
        return {}

    def build():
        hi = _date_for(kind, as_of, "hi")
        if hi is None:
            return {}
        rows = _rows(
            f"""
            WITH ranked AS (
                SELECT ticker, j_date, adj_final::float8 AS v,
                       COALESCE(volume, 0)::float8 AS vol,
                       COALESCE(value, 0)::float8   AS val,
                       ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
                FROM {tbl}
                WHERE adj_final > 0 AND date <= %s AND date >= %s::date - INTERVAL '30 days'
            )
            SELECT ticker,
                   MAX(j_date) FILTER (WHERE rn = 1) AS jdate,
                   MAX(v)      FILTER (WHERE rn = 1) AS last_v,
                   MAX(v)      FILTER (WHERE rn = 2) AS prev_v,
                   MAX(vol)    FILTER (WHERE rn = 1) AS volume,
                   MAX(val)    FILTER (WHERE rn = 1) AS value
            FROM ranked
            WHERE rn <= 2
            GROUP BY ticker
            """, (hi, hi))
        out = {}
        for r in rows:
            last_v, prev_v = r["last_v"], r["prev_v"]
            out[r["ticker"]] = {
                "jdate": r["jdate"],
                "value": r["value"] or 0.0,
                "volume": r["volume"] or 0.0,
                # None (not 0.0) when there is no previous bar: a symbol whose
                # first ever session is this one has no change to report, and
                # «۰٪» would be a claim the data does not support.
                "chg": ((last_v - prev_v) / prev_v * 100.0) if (prev_v and last_v) else None,
            }
        return out

    return cache.get_or_set("session", ("__session__", kind, as_of), build)


def market_map(kind, as_of=None, period="p20", market=None, sector=None, etf_type=None):
    """Rows for نقشهٔ بازار: every symbol with its group, its return over
    `period` (or the one-session change when `period` is 'd1') and the traded
    value that sizes its tile.

    Returns (rows, as_of, groups). `groups` aggregates the same rows by
    گروه (sector) for stocks / نوع for ETFs — the map draws a box per group, and
    the group average is what makes a sector rotation visible at a glance.

    That average is weighted by traded value, not by symbol count: a reading of
    "how did این گروه do" that counts a symbol with ۱۰۰ میلیون تومان of turnover
    the same as one with ۱۰۰ میلیارد describes a market nobody traded in.
    """
    rows, as_of = market_gainer(kind, as_of=as_of, market=market, sector=sector,
                                etf_type=etf_type)
    session = last_session(kind, as_of)
    key = None if period == "d1" else period

    out = []
    for r in rows:
        s = session.get(r["ticker"]) or {}
        chg = s.get("chg") if key is None else r.get(key)
        group = (r.get("sector") if kind == "stock" else r.get("type")) or "سایر"
        out.append({
            "id": r["id"], "ticker": r["ticker"], "name": r["name"],
            "group": group, "market": r.get("market"),
            "sub_sector": r.get("sub_sector"),
            "latest": r["latest"], "chg": chg,
            "value": s.get("value") or 0.0, "volume": s.get("volume") or 0.0,
        })

    groups = {}
    for r in out:
        g = groups.setdefault(r["group"], {"group": r["group"], "value": 0.0,
                                           "count": 0, "up": 0, "down": 0,
                                           "_w": 0.0, "_wv": 0.0})
        g["count"] += 1
        g["value"] += r["value"]
        if r["chg"] is not None:
            g["up" if r["chg"] >= 0 else "down"] += 1
            # Weighted by traded value, but never by zero: a group whose symbols
            # all had a quiet session must still report an average, so a symbol
            # with no turnover falls back to a weight of 1.
            w = r["value"] or 1.0
            g["_w"] += w
            g["_wv"] += w * r["chg"]
    for g in groups.values():
        g["avg"] = (g["_wv"] / g["_w"]) if g["_w"] else None
        del g["_w"], g["_wv"]

    glist = sorted(groups.values(), key=lambda g: -g["value"])
    return out, as_of, glist


def market_breadth(kind, as_of=None, period="p20"):
    """«نبض بازار» — how many symbols advanced, declined and stood still over
    `period` ('d1' = the last session), plus the extremes and the top groups.

    Breadth is the one figure that says whether a green headline was the whole
    market or three heavyweight symbols carrying it — precisely the question a
    list sorted by return cannot answer, which is why every professional market
    site leads with it.
    """
    rows, as_of, groups = market_map(kind, as_of=as_of, period=period)
    known = [r for r in rows if r["chg"] is not None]
    ranked = sorted(known, key=lambda r: -r["chg"])
    weight = sum((r["value"] or 1.0) for r in known)
    return {
        "as_of": as_of,
        "period": period,
        "kind": kind,
        "total": len(rows),
        "measured": len(known),
        "up": sum(1 for r in known if r["chg"] > 0),
        "down": sum(1 for r in known if r["chg"] < 0),
        "flat": sum(1 for r in known if r["chg"] == 0),
        # Value-weighted: the market's return, not the average symbol's return.
        "avg": (sum((r["value"] or 1.0) * r["chg"] for r in known) / weight) if known else None,
        "median": (sorted(r["chg"] for r in known)[len(known) // 2]) if known else None,
        "total_value": sum(r["value"] for r in rows),
        "best": ranked[:5],
        "worst": ranked[-5:][::-1],
        "groups": groups[:12],
    }
