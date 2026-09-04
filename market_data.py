"""
market_data.py — داده‌های تازهٔ بازار: شاخص، حقیقی/حقوقی، دیده‌بان، دلار

The read side of the four datasets added on top of the daily price history.
`tse_fetch.py` writes them; this module owns their schema and every query the
new pages ask.

WHY A NEW MODULE RATHER THAN MORE db.py

db.py is the price history and everything derived from it, and it is already
170 KB. These four datasets share none of that machinery: they have their own
tables, their own freshness (one is a live snapshot, three are back-fillable
history), and none of them feeds the materialized analytics. Putting them here
keeps "what is a price" and "what is a queue" from growing into each other.

WHAT IS COLLECTED

  ri_history          Get_RI_History     per symbol, back-fillable
  index_history       the 11 index fns   11 market indices + 40 sector indices
  usd_rial            Get_USD_RIAL       one series
  market_snapshot     Get_MarketWatch    the WHOLE market in one request
  order_book          Get_MarketWatch    five depths per symbol, latest only
  queue_history       (ours, see below)  end-of-session queue, per symbol-day
  intraday_orderbook  (ours, see below)  the full five-level tape
  intraday_trades     (ours, see below)  every executed trade
  shareholders        Get_ShareHoldersInfo   holders above 1%, latest only

TWO SPEEDS, AND WHY BOTH EXIST

The queue values (BQ_Value, SQ_Value, BQPC, SQPC) are collected TWICE, and that
is deliberate rather than redundant:

  · market_snapshot gets them for the WHOLE market in one ~5-second request, but
    only from the moment the feature is switched on — there is no way to ask
    Get_MarketWatch about last Tuesday.
  · queue_history gets them for ONE symbol at a time, one request per session,
    but reaches as far back as TSETMC keeps the intraday tape.

So the snapshot is the standing nightly job and the history is the tool you
point at a symbol you care about. Where both cover the same session they agree —
they are derived from the same numbers at the same instant of the day.

The three intraday datasets are ONE REQUEST PER SYMBOL-DAY. For a single symbol
that is fast (a session's tick tape is ~2 seconds); across the market it is
thousands of requests, which is why market.HEAVY_KINDS makes the update form
insist on a symbol before it will start one.
"""
import functools as _functools
import inspect as _inspect
import os

import db
import cache


# ---------------------------------------------------------------------------
# Caching market-wide reads
# ---------------------------------------------------------------------------
# Everything in this module reads the SAME market for every user — the numbers
# depend on the trading session, never on who is asking — so it belongs in the
# shared analytics cache alongside db.py's scans. Several functions here were
# not in it, and profiling the running app found them costing whole seconds per
# page (see freshness_cached and index_rows for the two worst).
#
# The decorator builds the cache key from the function's BOUND arguments, with
# defaults applied, so money_flow('stock') and money_flow(kind='stock') share
# one entry instead of computing the same rows twice.
#
# Invalidation is the same as everything else: cache.get_or_set namespaces on
# the global version, and db.clear_caches() INCRs it when data changes. Nothing
# here needs its own TTL beyond the default.

def _cache_key_part(v):
    """Make one argument usable in a cache key.

    Lists and sets are sorted into tuples: a caller passing ["a","b"] and one
    passing ["b","a"] want the same rows, and an unsorted key would compute
    them twice. Anything exotic falls back to its repr, which is stable enough
    for a key and cannot crash the call it is meant to speed up.
    """
    if isinstance(v, (list, set, frozenset)):
        return tuple(sorted(str(x) for x in v))
    if isinstance(v, (str, int, float, bool, type(None), tuple)):
        return v
    return repr(v)


def _cached(namespace, ttl=None, bypass_if=None):
    """Wrap a market-wide read in the shared cache.

    `bypass_if` takes the bound arguments and returns True to skip the cache
    entirely — used where an argument would blow up the keyspace, e.g. a
    free-text search term a user can type without limit.
    """
    def deco(fn):
        sig = _inspect.signature(fn)

        @_functools.wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            if bypass_if is not None and bypass_if(bound.arguments):
                return fn(*args, **kwargs)
            key = tuple(_cache_key_part(v) for v in bound.arguments.values())
            return cache.get_or_set(namespace, key,
                                    lambda: fn(*args, **kwargs), ttl=ttl)

        # The undecorated function, for a caller that must not be served a
        # cached answer. freshness() keeps its own explicit split for the same
        # reason; this is the generic version of it.
        wrapper.uncached = fn
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# شِما
#
# Every table is keyed so a re-fetch REPLACES rather than duplicates, the same
# property tse_fetch.store() gives the price tables. A back-fill that is run
# twice must be indistinguishable from one that is run once — under a retrying
# task queue "twice" is normal, not hypothetical.
# ---------------------------------------------------------------------------
SCHEMA = [
    # ---- حقیقی و حقوقی ---------------------------------------------------
    #
    # One row per symbol per session. `kind` is carried so a job can be scoped
    # to سهام or صندوق‌ها, but the KEY is (ticker, date): a ticker belongs to
    # exactly one kind, and keying on the pair would let the same symbol be
    # stored twice under two spellings of its kind.
    """
    CREATE TABLE IF NOT EXISTS ri_history (
        kind       TEXT        NOT NULL,
        entity_id  INTEGER,
        ticker     TEXT        NOT NULL,
        j_date     VARCHAR(10) NOT NULL,
        date       DATE        NOT NULL,
        weekday    VARCHAR(12),
        no_buy_r   BIGINT, no_buy_i   BIGINT, no_sell_r  BIGINT, no_sell_i  BIGINT,
        vol_buy_r  BIGINT, vol_buy_i  BIGINT, vol_sell_r BIGINT, vol_sell_i BIGINT,
        val_buy_r  BIGINT, val_buy_i  BIGINT, val_sell_r BIGINT, val_sell_i BIGINT,
        PRIMARY KEY (ticker, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ri_kind_date ON ri_history (kind, date DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ri_date ON ri_history (date DESC)",

    # ---- شاخص‌ها ----------------------------------------------------------
    #
    # One table for all of them, market and sector alike, distinguished by
    # index_key. Eleven near-identical tables — one per finpy function — would
    # make "compare شاخص کل with شاخص هم‌وزن" an eleven-way UNION.
    """
    CREATE TABLE IF NOT EXISTS index_history (
        index_key  TEXT        NOT NULL,
        name       TEXT,
        j_date     VARCHAR(10) NOT NULL,
        date       DATE        NOT NULL,
        weekday    VARCHAR(12),
        open       DOUBLE PRECISION,
        high       DOUBLE PRECISION,
        low        DOUBLE PRECISION,
        close      DOUBLE PRECISION,
        adj_close  DOUBLE PRECISION,
        volume     BIGINT,
        PRIMARY KEY (index_key, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_index_date ON index_history (date DESC)",

    # ---- دلار آزاد --------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS usd_rial (
        j_date  VARCHAR(10) NOT NULL,
        date    DATE        NOT NULL PRIMARY KEY,
        weekday VARCHAR(12),
        open    DOUBLE PRECISION,
        high    DOUBLE PRECISION,
        low     DOUBLE PRECISION,
        close   DOUBLE PRECISION
    )
    """,

    # ---- دیده‌بان بازار ----------------------------------------------------
    #
    # Keyed on (ticker, j_date), so re-running the snapshot during a session
    # OVERWRITES that session's row rather than appending a second one. That is
    # what makes a 09:30 snapshot and an 18:00 snapshot of the same day one row
    # holding the later state, and it is why `captured_at` is stored: the row
    # for today is only end-of-day data if it was captured after the close.
    """
    CREATE TABLE IF NOT EXISTS market_snapshot (
        ticker      TEXT        NOT NULL,
        j_date      VARCHAR(10) NOT NULL,
        date        DATE        NOT NULL,
        captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        trade_type  TEXT,
        board_time  TEXT,
        open        DOUBLE PRECISION,
        high        DOUBLE PRECISION,
        low         DOUBLE PRECISION,
        close       DOUBLE PRECISION,
        final       DOUBLE PRECISION,
        pct_close   DOUBLE PRECISION,
        pct_final   DOUBLE PRECISION,
        day_ul      DOUBLE PRECISION,
        day_ll      DOUBLE PRECISION,
        value       BIGINT,
        volume      BIGINT,
        no          BIGINT,
        bq_value    BIGINT, sq_value BIGINT, bqpc BIGINT, sqpc BIGINT,
        vol_buy_r   BIGINT, vol_buy_i  BIGINT, vol_sell_r BIGINT, vol_sell_i BIGINT,
        no_buy_r    BIGINT, no_buy_i   BIGINT, no_sell_r  BIGINT, no_sell_i  BIGINT,
        name        TEXT,
        market      TEXT,
        sector      TEXT,
        share_no    NUMERIC,
        base_vol    NUMERIC,
        market_cap  NUMERIC,
        eps         DOUBLE PRECISION,
        PRIMARY KEY (ticker, j_date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_ms_date ON market_snapshot (date DESC)",
    "CREATE INDEX IF NOT EXISTS ix_ms_jdate ON market_snapshot (j_date)",

    # ---- عمق بازار (دفتر سفارش) -------------------------------------------
    #
    # LATEST ONLY, five rows per symbol. Keeping a history of it would mean
    # ~7,000 rows per capture and would be a different product (a tape); what
    # the board needs is "what does the queue look like right now".
    """
    CREATE TABLE IF NOT EXISTS order_book (
        ticker      TEXT      NOT NULL,
        depth       SMALLINT  NOT NULL,
        captured_at TIMESTAMPTZ NOT NULL,
        j_date      VARCHAR(10),
        day_ul      DOUBLE PRECISION,
        day_ll      DOUBLE PRECISION,
        sell_no     INTEGER,
        sell_vol    BIGINT,
        sell_price  DOUBLE PRECISION,
        buy_price   DOUBLE PRECISION,
        buy_vol     BIGINT,
        buy_no      INTEGER,
        PRIMARY KEY (ticker, depth)
    )
    """,

    # ---- سابقهٔ ارزش صف --------------------------------------------------
    #
    # One row per symbol per session: the state of the queue at the moment the
    # market closed. Derived from the intraday order-book tape (the last quote
    # at or before 12:30:00), which is the only place it exists.
    #
    # This is the SLOW one — one request pair per symbol-day — and it is the
    # reason market_snapshot exists as well. The two answer the same question
    # from opposite ends: this one can reach BACK, the snapshot only goes
    # forward but covers the whole market in seconds. Both are kept.
    """
    CREATE TABLE IF NOT EXISTS queue_history (
        kind       TEXT        NOT NULL,
        entity_id  INTEGER,
        ticker     TEXT        NOT NULL,
        j_date     VARCHAR(10) NOT NULL,
        date       DATE        NOT NULL,
        weekday    VARCHAR(12),
        board_time TEXT,
        day_ul     DOUBLE PRECISION,
        day_ll     DOUBLE PRECISION,
        value      BIGINT,
        bq_value   BIGINT,
        sq_value   BIGINT,
        bqpc       BIGINT,
        sqpc       BIGINT,
        PRIMARY KEY (ticker, date)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_qh_kind_date ON queue_history (kind, date DESC)",

    # ---- عمق بازار درون‌روز ------------------------------------------------
    #
    # The full five-level order-book TAPE: every change, all day. ~7,000 rows
    # per symbol-day, so this is per-symbol on purpose and the form says so.
    # `seq` is in the key because TSETMC can publish two states in the same
    # second at the same depth, and without it the second would overwrite the
    # first and the tape would silently lose ticks.
    """
    CREATE TABLE IF NOT EXISTS intraday_orderbook (
        ticker     TEXT        NOT NULL,
        j_date     VARCHAR(10) NOT NULL,
        date       DATE        NOT NULL,
        time       TIME        NOT NULL,
        seq        INTEGER     NOT NULL,
        depth      SMALLINT    NOT NULL,
        sell_no    INTEGER, sell_vol BIGINT, sell_price DOUBLE PRECISION,
        buy_price  DOUBLE PRECISION, buy_vol BIGINT, buy_no INTEGER,
        day_ul     DOUBLE PRECISION,
        day_ll     DOUBLE PRECISION,
        PRIMARY KEY (ticker, date, time, seq, depth)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_iob_ticker_date ON intraday_orderbook (ticker, date DESC)",

    # ---- ریز معاملات ------------------------------------------------------
    #
    # Every executed trade. ~35,000 rows for one liquid symbol on one day,
    # measured — so, like the tape above, this is a per-symbol tool and not
    # something to run across the market.
    """
    CREATE TABLE IF NOT EXISTS intraday_trades (
        ticker  TEXT        NOT NULL,
        j_date  VARCHAR(10) NOT NULL,
        date    DATE        NOT NULL,
        time    TIME        NOT NULL,
        seq     INTEGER     NOT NULL,
        volume  BIGINT,
        price   DOUBLE PRECISION,
        -- The exchange cancels erroneous trades. They stay in the tape because
        -- they are part of it, and reads exclude them by default — dropping
        -- them on write would leave no way to tell a quiet session from a
        -- corrected one.
        canceled BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY (ticker, date, time, seq)
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_itr_ticker_date ON intraday_trades (ticker, date DESC)",

    # ---- سهامداران عمده ---------------------------------------------------
    #
    # Holders above 1%, as TSETMC reports them today. A snapshot, not a
    # history: the source page shows only the current standing, so a re-fetch
    # REPLACES a symbol's holders rather than accumulating — which is why the
    # write deletes the symbol's rows first. `captured_at` says how old it is.
    """
    CREATE TABLE IF NOT EXISTS shareholders (
        ticker      TEXT        NOT NULL,
        holder      TEXT        NOT NULL,
        market      TEXT,
        share_no    NUMERIC,
        share_pct   DOUBLE PRECISION,
        changes     NUMERIC,
        captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        j_date      VARCHAR(10),
        PRIMARY KEY (ticker, holder)
    )
    """,
]


#: Columns added to a table AFTER it first shipped, as (table, name, DDL).
#: CREATE TABLE IF NOT EXISTS will not add them to an existing table, so they
#: need an explicit idempotent ALTER — see ensure_tables().
_ADDED_COLUMNS = [
    ("intraday_trades", "canceled", "BOOLEAN NOT NULL DEFAULT FALSE"),
]

_TABLES_READY = False


def ensure_tables():
    """Idempotent; called from app start-up and from the Celery worker, so
    whichever comes up first creates them. The module-level flag only skips the
    round trip within one process — it is not a cache of truth."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    conn = db.get_db()
    try:
        conn.autocommit = True                 # CREATE INDEX commits per statement
        with conn.cursor() as cur:
            for stmt in SCHEMA:
                cur.execute(stmt)
            # CREATE TABLE IF NOT EXISTS does nothing to a table that already
            # exists, so a column added after the first release needs its own
            # ALTER — the same arrangement db._PREF_COLUMNS uses. Keep this list
            # in step with SCHEMA above.
            for table, col, ddl in _ADDED_COLUMNS:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}")
        _TABLES_READY = True
    finally:
        conn.autocommit = False
        db.release(conn)


# ---------------------------------------------------------------------------
# حذف — what «حذف داده» can reach, per dataset
#
# The delete panel used to know only the two price tables, which meant a bad
# حقیقی/حقوقی back-fill or a wrong-year index sweep could be created from the UI
# and only removed with psql. Every dataset that can be WRITTEN from the update
# page can now be deleted from it.
#
# Each entry is (table, ticker column or None, date column or None). A dataset
# with no ticker column ignores the symbol box; one with no date column ignores
# the range and can only be cleared wholesale, which is the truth about a
# snapshot — «صف دیروز» is not something order_book holds.
# ---------------------------------------------------------------------------
DELETABLE = {
    "ri":          ("ri_history",         "ticker", "date",  "حقیقی/حقوقی"),
    "index":       ("index_history",       None,    "date",  "شاخص‌ها"),
    "usd":         ("usd_rial",            None,    "date",  "قیمت دلار"),
    "watch":       ("market_snapshot",    "ticker", "date",  "دیده‌بان بازار"),
    "orderbook":   ("order_book",         "ticker",  None,   "عمق بازار (آخرین عکس)"),
    "queue":       ("queue_history",      "ticker", "date",  "سابقهٔ صف"),
    "intraday_ob": ("intraday_orderbook", "ticker", "date",  "عمق بازار درون‌روز"),
    "intraday_trades": ("intraday_trades", "ticker", "date", "ریز معاملات"),
    "shareholders": ("shareholders",      "ticker",  None,   "سهامداران عمده"),
}


def delete_dataset(dataset, ticker=None, start=None, end=None, all_history=False):
    """Delete rows of one dataset. Returns how many.

    Mirrors db.delete_price_history's contract deliberately, including its one
    real safety property: a date range is REQUIRED unless `all_history` is
    passed explicitly. Missing dates raise rather than being read as "no bounds,
    delete everything" — that inference is how a range delete becomes a table
    wipe on a typo.

    Bounds are compared on the Jalali `j_date` column here rather than resolved
    through the price calendar as db.delete_price_history does. These tables
    have their own calendars — index_history has sessions ri_history does not —
    so translating through the price table's calendar would silently miss rows
    on any date the prices skipped.
    """
    spec = DELETABLE.get(dataset)
    if not spec:
        raise ValueError(f"unknown dataset {dataset!r}")
    table, ticker_col, date_col, _label = spec
    if not all_history and date_col and (not start or not end):
        raise ValueError("a from/to date range is required")
    if not _table_exists(table):
        return 0

    clauses, params = [], []
    if date_col and not all_history:
        clauses += [f"j_date >= %s", f"j_date <= %s"]
        params += [start, end]
    if ticker and ticker_col:
        clauses.append(f"{ticker_col} = %s")
        params.append(ticker)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {table}{where}", params)
            n = cur.rowcount
        conn.commit()
        if n:
            # Same reasoning as db.delete_price_history: the freshness numbers
            # these pages read are cached, and a delete changes them NOW.
            db.clear_cache()
        return n
    finally:
        db.release(conn)


def _table_exists(name):
    r = db._one("SELECT to_regclass(%s) AS t", (f"public.{name}",))
    return bool(r and r["t"])


# ---------------------------------------------------------------------------
# فهرست شاخص‌ها
#
# key → (Persian name, the finpy function that fetches it). The key is what goes
# in index_history.index_key and what a URL carries, so it is ASCII and stable;
# the name is what the page shows.
# ---------------------------------------------------------------------------
MARKET_INDICES = [
    ("cwi",   "شاخص کل",                  "Get_CWI_History"),
    ("ewi",   "شاخص کل هم‌وزن",            "Get_EWI_History"),
    ("cwpi",  "شاخص قیمت وزنی-ارزشی",     "Get_CWPI_History"),
    ("ewpi",  "شاخص قیمت هم‌وزن",          "Get_EWPI_History"),
    ("ffi",   "شاخص سهام شناور آزاد",      "Get_FFI_History"),
    ("mkt1i", "شاخص بازار اول",            "Get_MKT1I_History"),
    ("mkt2i", "شاخص بازار دوم",            "Get_MKT2I_History"),
    ("indi",  "شاخص صنعت",                 "Get_INDI_History"),
    ("act50", "شاخص ۵۰ شرکت فعال‌تر",      "Get_ACT50_History"),
    ("lci30", "شاخص ۳۰ شرکت بزرگ",         "Get_LCI30_History"),
]

INDEX_NAME = {k: n for k, n, _ in MARKET_INDICES}

#: The four the شاخص‌ها page puts at the top. «کل» and «هم‌وزن» together are the
#: single most-read pair on any Iranian market screen — one says what the big
#: caps did, the other what the median symbol did, and the gap between them is
#: the day's actual story.
HEADLINE_INDICES = ("cwi", "ewi", "ffi", "indi")

SECTOR_PREFIX = "sector:"

#: The forty گروه صنعت `Get_SectorIndex_History` can actually resolve.
#:
#: This list is not a preference — it is finpy's own hard-coded lookup table,
#: copied deliberately. The printed guide says the function matches a sector
#: name loosely («بانک», «بانکی», «بانکی‌ها» all work); in the installed build it
#: does an EXACT lookup against these forty strings and, when that misses, falls
#: back to scraping a Google result page for the web-id. Passing the TSE group
#: names off `stocks.sector` («خودرو و ساخت قطعات») therefore misses every time
#: and lands on the scrape, which is both slow and certain to break.
#:
#: The second element maps each one onto the `stocks.sector` spelling where the
#: two clearly denote the same group, so «شاخص صنعت» on a sector page can link
#: to the symbols in it. None means "no confident mapping" — three of the forty
#: (رادیویی, مالی, اداره بازارهای مالی) have no counterpart in this database's
#: group names, and inventing one would silently file symbols under the wrong
#: index.
SECTOR_INDICES = [
    ("زراعت",                "زراعت و خدمات وابسته"),
    ("ذغال سنگ",             "استخراج زغال سنگ"),
    ("کانی فلزی",            "استخراج کانه های فلزی"),
    ("سایر معادن",           "استخراج سایر معادن"),
    ("منسوجات",              "منسوجات"),
    ("محصولات چرمی",         "دباغی، پرداخت چرم و ساخت انواع پاپوش"),
    ("محصولات چوبی",         "محصولات چوبی"),
    ("محصولات کاغذی",        "محصولات کاغذی"),
    ("انتشار و چاپ",         "انتشار، چاپ و تکثیر"),
    ("فرآورده های نفتی",     "فراورده های نفتی، کک و سوخت هسته ای"),
    ("لاستیک",               "لاستیک و پلاستیک"),
    ("فلزات اساسی",          "فلزات اساسی"),
    ("محصولات فلزی",         "ساخت محصولات فلزی"),
    ("ماشین آلات",           "ماشین آلات و تجهیزات"),
    ("دستگاه های برقی",      "ماشین آلات و دستگاه های برقی"),
    ("وسایل ارتباطی",        "ساخت دستگاه ها و وسایل ارتباطی"),
    ("خودرو",                "خودرو و ساخت قطعات"),
    ("قند و شکر",            "قند و شکر"),
    ("چند رشته ای",          "شرکتهای چند رشته ای صنعتی"),
    ("تامین آب، برق و گاز",  "عرضه برق، گاز، بخاروآب گرم"),
    ("غذایی",                "محصولات غذایی و آشامیدنی به جز قند و شکر"),
    ("دارویی",               "مواد و محصولات دارویی"),
    ("شیمیایی",              "محصولات شیمیایی"),
    ("خرده فروشی",           "خرده فروشی،باستثنای وسایل نقلیه موتوری"),
    ("کاشی و سرامیک",        "کاشی و سرامیک"),
    ("سیمان",                "سیمان، آهک و گچ"),
    ("کانی غیر فلزی",        "سایر محصولات کانی غیرفلزی"),
    ("سرمایه گذاری",         "سرمایه گذاریها"),
    ("بانک",                 "بانکها و موسسات اعتباری"),
    ("سایر مالی",            "سایر واسطه گریهای مالی"),
    ("حمل و نقل",            "حمل ونقل، انبارداری و ارتباطات"),
    ("رادیویی",              None),
    ("مالی",                 None),
    ("اداره بازارهای مالی",  None),
    ("انبوه سازی",           "انبوه سازی، املاک و مستغلات"),
    ("رایانه",               "رایانه و فعالیت های وابسته به آن"),
    ("اطلاعات و ارتباطات",   "اطلاعات و ارتباطات"),
    ("فنی مهندسی",           "خدمات فنی و مهندسی"),
    ("استخراج نفت",          "استخراج نفت گاز و خدمات جنبی جز اکتشاف"),
    ("بیمه و بازنشستگی",     "بیمه وصندوق بازنشستگی به جزتامین اجتماعی"),
]

SECTOR_NAMES = [s for s, _ in SECTOR_INDICES]

#: stocks.sector → the sector-index key that covers it, for the cross-link.
SECTOR_OF_GROUP = {group: SECTOR_PREFIX + name
                   for name, group in SECTOR_INDICES if group}


def sector_key(name):
    return SECTOR_PREFIX + str(name).strip()


def is_sector(index_key):
    return str(index_key).startswith(SECTOR_PREFIX)


def index_label(index_key):
    if is_sector(index_key):
        return index_key[len(SECTOR_PREFIX):]
    return INDEX_NAME.get(index_key, index_key)


# ---------------------------------------------------------------------------
# فرادادهٔ تازگی — what the /update page and every new page show as «آخرین داده»
# ---------------------------------------------------------------------------
def latest_jdate(table, where="", params=()):
    if not _table_exists(table):
        return None
    r = db._one(f"SELECT MAX(j_date) d FROM {table} {where}", params)
    return r["d"] if r else None


#: How long the market pages may show a stale «تا تاریخ …» footnote.
#: Sixty seconds against a dataset that changes once a day.
FRESHNESS_TTL = int(os.environ.get("FRESHNESS_TTL", "60"))


def freshness_cached():
    """freshness(), memoised for FRESHNESS_TTL seconds.

    MEASURED, not guessed: freshness() takes ~1.08 s, and /indices, /moneyflow
    and /live each called it once per request. On /live that WAS the page —
    1,200 ms total of which ~1,050 ms was this function and 34 ms was the board
    data it actually displays.

    The docstring below is right that the number must be exact on /update,
    where an operator reads it to decide whether to run a download. It is not
    right for the other three, where the same value is a footnote saying which
    session the table is from. Those three now call this; /update still calls
    freshness() directly and still pays the full cost, which is correct.

    Why it is slow, for whoever tunes it next: the thirteen queries include
    COUNT(*) and COUNT(DISTINCT ticker) over ri_history, intraday_trades and
    intraday_orderbook. In PostgreSQL an unqualified COUNT(*) is a full heap
    scan — on 8.1 M rows that is the whole second. If an exact count is ever
    wanted more cheaply than this, reltuples from pg_class is the usual answer;
    caching was chosen here because these numbers do not need to be exact at
    all on the pages that were paying for them.
    """
    return cache.get_or_set("freshness", ("__all__",), freshness,
                            ttl=FRESHNESS_TTL)


def freshness():
    """{dataset: {'latest': jdate, 'rows': n}} for the /update page.

    Uncached BY DESIGN, and it is what the operator reads to decide whether to
    run anything at all — so /update must keep calling this one. It is not four
    cheap MAX()s as the original comment said: it is thirteen queries and
    several are COUNT(*) over multi-million-row tables, which measures at about
    1.08 seconds. Anything that only needs a "data as of" label should call
    freshness_cached() instead."""
    out = {}
    for key, table in (("ri", "ri_history"), ("index", "index_history"),
                       ("usd", "usd_rial"), ("watch", "market_snapshot"),
                       ("queue", "queue_history"),
                       ("intraday_ob", "intraday_orderbook"),
                       ("intraday_trades", "intraday_trades")):
        if not _table_exists(table):
            out[key] = {"latest": None, "rows": 0}
            continue
        r = db._one(f"SELECT MAX(j_date) d, COUNT(*) n FROM {table}")
        out[key] = {"latest": (r or {}).get("d"), "rows": (r or {}).get("n") or 0}
    if _table_exists("order_book"):
        r = db._one("SELECT MAX(captured_at) t, COUNT(DISTINCT ticker) n FROM order_book")
        out["orderbook"] = {"captured": (r or {}).get("t"), "rows": (r or {}).get("n") or 0}
    else:
        out["orderbook"] = {"captured": None, "rows": 0}
    # سهامداران is a snapshot with no session date, so what matters is how many
    # symbols have one and how stale the newest capture is.
    if _table_exists("shareholders"):
        r = db._one("SELECT MAX(j_date) d, COUNT(DISTINCT ticker) n FROM shareholders")
        out["shareholders"] = {"latest": (r or {}).get("d"),
                               "rows": (r or {}).get("n") or 0}
    else:
        out["shareholders"] = {"latest": None, "rows": 0}
    # The three intraday datasets are per-symbol tools, so "how many symbols do
    # I have this for" is the number an operator actually needs.
    for key, table in (("queue", "queue_history"),
                       ("intraday_ob", "intraday_orderbook"),
                       ("intraday_trades", "intraday_trades")):
        if _table_exists(table):
            r = db._one(f"SELECT COUNT(DISTINCT ticker) n FROM {table}")
            out[key]["symbols"] = (r or {}).get("n") or 0
        else:
            out[key]["symbols"] = 0
    # Per-kind RI latest, because سهام and صندوق‌ها are separate jobs and an
    # operator needs to see which of the two is behind.
    for kind in ("stock", "etf"):
        out[f"ri_{kind}"] = {
            "latest": latest_jdate("ri_history", "WHERE kind = %s", (kind,))}
    return out


# ---------------------------------------------------------------------------
# شاخص‌ها — the /indices page
# ---------------------------------------------------------------------------
#: Trading-day lookbacks the شاخص page reports, mirroring db.PERIODS so a
#: «۲۰ روزه» on the index means the same thing as on a symbol.
INDEX_PERIODS = [("d1", "۱ روزه", 1), ("d5", "۵ روزه", 5), ("d20", "۱ ماهه", 20),
                 ("d60", "۳ ماهه", 60), ("d120", "۶ ماهه", 120),
                 ("d240", "۱ ساله", 240)]


def _pct(cur, base):
    if cur is None or base in (None, 0):
        return None
    return (cur - base) / base * 100.0


# ---------------------------------------------------------------------------
# پول، به واحدی که خوانده می‌شود
#
# TSETMC reports every value in RIALS, and these pages deal in numbers like
# 2,309,378,183,184. Nobody reads that. Iranian market commentary quotes money
# in تومان with a magnitude word attached, so that is what is rendered:
#
#     ۱ همت            = هزار میلیارد تومان = 10^13 ریال
#     ۱ میلیارد تومان                       = 10^10 ریال
#     ۱ میلیون تومان                        = 10^7  ریال
#
# The unit is chosen per value rather than fixed per column, because a column of
# net flows legitimately spans «۳ میلیون تومان» and «۴۲ همت» on the same screen,
# and a fixed unit makes one end of it unreadable.
# ---------------------------------------------------------------------------
_MONEY_UNITS = ((1e13, "همت"), (1e10, "میلیارد تومان"), (1e7, "میلیون تومان"),
                (1e4, "هزار تومان"))


def _mantissa(n, digits):
    """`n` with at most `digits` decimals, trailing zeros removed, in Persian.

    NOT db.to_persian(): that renders every non-integer with exactly two
    decimals — right for a percentage, where «۱.۵۰٪» and «۱.۵٪» should not
    alternate down a column, and wrong in front of a magnitude word, where
    «۱.۵۰ میلیارد» is two digits of precision the unit has already thrown away.
    """
    s = f"{n:,.{digits}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.translate(db._FA_DIGITS)


def rial_short(value, digits=2, signed=False):
    """A rial amount as «۱۲.۳ همت». Returns '' for None so a template can print
    it without a guard; zero is a real answer and prints as «۰»."""
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "−" if v < 0 else ("+" if (signed and v > 0) else "")
    v = abs(v)
    if v == 0:
        return "۰"
    for scale, unit in _MONEY_UNITS:
        if v >= scale:
            n = v / scale
            # Three significant-ish figures: 12.3 همت, but 1.23 همت — a number
            # already in the hundreds does not need decimals to be precise.
            d = 0 if n >= 100 else (1 if n >= 10 else digits)
            return f"{sign}{_mantissa(n, d)} {unit}"
    return f"{sign}{_mantissa(v / 10, 0)} تومان"


def count_short(value):
    """A share/volume count as «۱.۲ میلیارد». Volumes are counts, not money, so
    they get their own scale words and never a تومان."""
    if value is None:
        return ""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    sign = "−" if v < 0 else ""
    v = abs(v)
    for scale, unit in ((1e9, "میلیارد"), (1e6, "میلیون"), (1e3, "هزار")):
        if v >= scale:
            n = v / scale
            d = 0 if n >= 100 else (1 if n >= 10 else 2)
            return f"{sign}{_mantissa(n, d)} {unit}"
    return f"{sign}{_mantissa(v, 0)}"


def index_rows(keys=None, sectors=False):
    """One row per index: latest value, and the return over each INDEX_PERIODS
    window, measured on `adj_close`.

    CACHED, because it is a market-wide aggregate over daily data and it was
    measured at 102 ms for the ten market indices and 493 ms for the forty
    sector ones — on EVERY request to /indices, which asks for both. Nothing in
    it is per-user, so it belongs in the same shared cache as the other
    market-wide scans; the key carries the arguments and the cache version, so
    a data update invalidates it the same way it invalidates everything else.

    The window arithmetic below is unchanged and still anchored on the index's
    own calendar — see the note after this one.

    Anchored on the INDEX's own calendar, which is the market calendar — every
    index in this table is published on exactly the sessions the exchange was
    open, so "۵ روز" is five sessions back from that index's latest bar and
    needs no cross-referencing against the price tables.
    """
    if not _table_exists("index_history"):
        return []
    # A tuple, not the list `keys` may be, because a cache key has to be
    # hashable and stable in order — sorted so that ("cwi","ewi") and
    # ("ewi","cwi") share one entry rather than computing the same rows twice.
    key_part = tuple(sorted(keys)) if keys else None
    return cache.get_or_set(
        "indexrows", (key_part, bool(sectors)),
        lambda: _index_rows_query(keys, sectors))


def _index_rows_query(keys, sectors):
    """The actual query behind index_rows(). Split out only so the cache wrapper
    above stays readable; nothing else should call this."""
    # `adj_close IS NOT NULL` on every branch. A session TSETMC published with no
    # settled index value is not a session a return can be measured against, and
    # letting one in as the newest row makes `value` None — which every card and
    # every percentage on the page would then have to guard individually.
    live = "adj_close IS NOT NULL"
    if keys:
        where, params = f"WHERE {live} AND index_key = ANY(%s)", [list(keys)]
    elif sectors:
        where, params = (f"WHERE {live} AND index_key LIKE %s", [SECTOR_PREFIX + "%"])
    else:
        where, params = (f"WHERE {live} AND index_key NOT LIKE %s", [SECTOR_PREFIX + "%"])

    max_back = max(n for _, _, n in INDEX_PERIODS)
    rows = db._rows(
        f"""
        WITH ranked AS (
            SELECT index_key, name, j_date, date, adj_close, close, volume,
                   ROW_NUMBER() OVER (PARTITION BY index_key ORDER BY date DESC) rn
              FROM index_history
              {where}
        )
        SELECT index_key, name, j_date, date, adj_close, close, volume, rn
          FROM ranked WHERE rn <= %s ORDER BY index_key, rn
        """,
        tuple(params) + (max_back + 1,))

    by_key = {}
    for r in rows:
        by_key.setdefault(r["index_key"], []).append(r)

    out = []
    for key, series in by_key.items():
        head = series[0]                       # rn = 1, the latest session
        rec = {"key": key, "label": index_label(key),
               "sector": is_sector(key),
               "j_date": head["j_date"], "date": head["date"],
               "value": head["adj_close"], "close": head["close"],
               "volume": head["volume"], "bars": len(series)}
        for pid, _, n in INDEX_PERIODS:
            base = series[n] if len(series) > n else None
            rec[pid] = _pct(head["adj_close"], base["adj_close"]) if base else None
        out.append(rec)
    out.sort(key=lambda r: (r["d1"] is None, -(r["d1"] or 0)))
    return out


def index_series(key, bars=240):
    """[(j_date, adj_close)] oldest→newest, for the chart."""
    if not _table_exists("index_history"):
        return []
    rows = db._rows(
        """SELECT j_date, adj_close FROM index_history
            WHERE index_key = %s AND adj_close IS NOT NULL
            ORDER BY date DESC LIMIT %s""", (key, bars))
    return [(r["j_date"], r["adj_close"]) for r in reversed(rows)]


def usd_rows(bars=240):
    """[(j_date, close)] oldest→newest for the dollar chart."""
    if not _table_exists("usd_rial"):
        return []
    rows = db._rows(
        "SELECT j_date, close FROM usd_rial WHERE close > 0 ORDER BY date DESC LIMIT %s",
        (bars,))
    return [(r["j_date"], r["close"]) for r in reversed(rows)]


def usd_summary():
    """Latest dollar rate plus the same period returns the indices carry, so the
    two can be read on one line: «شاخص +۱.۲٪ / دلار −۰.۴٪» is the comparison
    that actually tells a Tehran investor whether the market moved."""
    if not _table_exists("usd_rial"):
        return None
    max_back = max(n for _, _, n in INDEX_PERIODS)
    rows = db._rows(
        "SELECT j_date, date, open, high, low, close FROM usd_rial "
        "WHERE close > 0 ORDER BY date DESC LIMIT %s", (max_back + 1,))
    if not rows:
        return None
    head = rows[0]
    rec = {"j_date": head["j_date"], "date": head["date"], "value": head["close"],
           "high": head["high"], "low": head["low"], "bars": len(rows)}
    for pid, _, n in INDEX_PERIODS:
        rec[pid] = _pct(head["close"], rows[n]["close"]) if len(rows) > n else None
    return rec


# ---------------------------------------------------------------------------
# پول حقیقی و حقوقی — the /moneyflow page
#
# THE FOUR NUMBERS, AND WHY THEY ARE DEFINED THIS WAY
#
#   خالص ورود پول حقیقی = Val_Buy_R − Val_Sell_R
#       Positive means retail bought more (in rials) than it sold, i.e. money
#       moved from حقوقی into حقیقی hands. This is the sign convention every
#       Iranian data vendor uses, and it is the OPPOSITE of "smart money" in the
#       US sense — worth stating, because a filter written with the other
#       convention is silently backwards.
#
#   سرانهٔ خرید حقیقی  = Val_Buy_R  / No_Buy_R      (rials per buying person)
#   سرانهٔ فروش حقیقی  = Val_Sell_R / No_Sell_R
#   قدرت خریدار حقیقی = سرانهٔ خرید ÷ سرانهٔ فروش
#
#       Per-capita is the number that distinguishes "one buyer with conviction"
#       from "four hundred people each buying a lot of ten", which raw volume
#       cannot. Division guarded: a session with no retail buyers has No_Buy_R=0
#       and its per-capita is undefined, not zero — reporting zero would sort
#       every dead symbol to the bottom of «ضعیف‌ترین قدرت خریدار» and bury the
#       real ones.
# ---------------------------------------------------------------------------
FLOW_PERIODS = [(1, "۱ روزه"), (5, "۵ روزه"), (20, "۱ ماهه"), (60, "۳ ماهه")]


def flow_latest_jdate(kind="stock"):
    return latest_jdate("ri_history", "WHERE kind = %s", (kind,))


def _flow_window(kind, days, as_of=None):
    """(lo_date, hi_date, lo_jdate, hi_jdate) covering the last `days` SESSIONS
    present in ri_history for this kind, ending at or before `as_of`.

    Off the RI table's own calendar rather than the price table's: the two can
    legitimately differ by a session while a back-fill is running, and taking
    the window from the table being read is what stops «۵ روزه» silently
    becoming «۴ روزه» in the middle of an update.

    Both calendars come back because both are needed: the Gregorian dates are
    what the queries compare (see db._date_for — `date` is the indexed axis),
    and the Jalali ones are what the page prints. Returning only the first and
    letting the template format it is how «از ۲۰۲۶-۰۸-۲۴» reached a page on
    which every other date is Jalali."""
    rows = db._rows(
        """SELECT DISTINCT date, j_date FROM ri_history
            WHERE kind = %s AND (%s::text IS NULL OR j_date <= %s)
            ORDER BY date DESC LIMIT %s""",
        (kind, as_of, as_of, days))
    if not rows:
        return None, None, None, None
    return (rows[-1]["date"], rows[0]["date"],
            rows[-1]["j_date"], rows[0]["j_date"])


@_cached("moneyflow")
def money_flow(kind="stock", days=5, as_of=None, sector=None, market=None,
               limit=400, order="net_desc"):
    """Market-wide retail/institutional flow over the last `days` sessions.

    Returns one row per symbol with the aggregated rial flows, the per-capita
    pair, buyer power, and the symbol's own price move over the same window so
    the flow can be read against what the price actually did.
    """
    if not _table_exists("ri_history"):
        return {"rows": [], "lo": None, "hi": None}
    lo, hi, j_lo, j_hi = _flow_window(kind, days, as_of)
    if lo is None:
        return {"rows": [], "lo": None, "hi": None}

    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    ref_join = ("LEFT JOIN stocks s ON s.ticker = a.ticker"
                if kind == "stock" else
                "LEFT JOIN etf s ON s.ticker = a.ticker")
    ref_cols = ("s.name, s.sector, s.market, s.sub_sector, s.stockid AS entity_id"
                if kind == "stock" else
                "s.name, NULL::varchar AS sector, s.type AS market, "
                "NULL::varchar AS sub_sector, s.id AS entity_id")

    clauses, params = [], [kind, lo, hi]
    if sector and kind == "stock":
        clauses.append("s.sector = %s")
        params.append(sector)
    if market:
        clauses.append(("s.market = %s" if kind == "stock" else "s.type = %s"))
        params.append(market)
    extra = (" AND " + " AND ".join(clauses)) if clauses else ""

    # ORDER BY is chosen from a fixed map, never interpolated from user input.
    order_sql = _FLOW_ORDER.get(order, _FLOW_ORDER["net_desc"])

    rows = db._rows(
        f"""
        WITH a AS (
            SELECT ticker,
                   SUM(val_buy_r)  AS buy_r,  SUM(val_sell_r) AS sell_r,
                   SUM(val_buy_i)  AS buy_i,  SUM(val_sell_i) AS sell_i,
                   SUM(vol_buy_r)  AS vbuy_r, SUM(vol_sell_r) AS vsell_r,
                   SUM(no_buy_r)   AS nbuy_r, SUM(no_sell_r)  AS nsell_r,
                   COUNT(*)        AS sessions
              FROM ri_history
             WHERE kind = %s AND date >= %s AND date <= %s
             GROUP BY ticker
        ),
        px AS (
            -- The window's two endpoints only. FILTER over a two-date scan is
            -- one index range per date; joining the whole price table and
            -- taking first/last would read a year of bars to use two of them.
            SELECT ticker,
                   MAX(adj_final) FILTER (WHERE date = %s) AS f_hi,
                   MAX(adj_final) FILTER (WHERE date = %s) AS f_lo,
                   MAX(value)     FILTER (WHERE date = %s) AS value_hi
              FROM {price_tbl}
             WHERE date IN (%s, %s)
             GROUP BY ticker
        )
        SELECT a.ticker, {ref_cols},
               a.buy_r, a.sell_r, a.buy_i, a.sell_i,
               a.vbuy_r, a.vsell_r, a.nbuy_r, a.nsell_r, a.sessions,
               px.f_hi, px.f_lo, px.value_hi
          FROM a
          {ref_join}
          LEFT JOIN px ON px.ticker = a.ticker
         WHERE TRUE{extra}
         ORDER BY {order_sql}
         LIMIT %s
        """,
        tuple(params[:3]) + (hi, lo, hi, hi, lo) + tuple(params[3:]) + (limit,))

    out = []
    for r in rows:
        buy_r = float(r["buy_r"] or 0)
        sell_r = float(r["sell_r"] or 0)
        nbuy = float(r["nbuy_r"] or 0)
        nsell = float(r["nsell_r"] or 0)
        pc_buy = buy_r / nbuy if nbuy > 0 else None
        pc_sell = sell_r / nsell if nsell > 0 else None
        out.append({
            "ticker": r["ticker"], "name": r["name"], "sector": r["sector"],
            "market": r["market"], "sub_sector": r["sub_sector"],
            "entity_id": r["entity_id"], "sessions": r["sessions"],
            "net": buy_r - sell_r,
            "buy_r": buy_r, "sell_r": sell_r,
            "buy_i": float(r["buy_i"] or 0), "sell_i": float(r["sell_i"] or 0),
            "vol_buy_r": float(r["vbuy_r"] or 0), "vol_sell_r": float(r["vsell_r"] or 0),
            "no_buy_r": r["nbuy_r"], "no_sell_r": r["nsell_r"],
            "pc_buy": pc_buy, "pc_sell": pc_sell,
            "power": (pc_buy / pc_sell) if (pc_buy and pc_sell) else None,
            "pct": _pct(r["f_hi"], r["f_lo"]),
            "price": r["f_hi"], "value": r["value_hi"],
        })
    return {"rows": out, "lo": j_lo, "hi": j_hi, "days": days}


#: Every sort the money-flow page offers, as SQL. A fixed map rather than string
#: interpolation: `order` arrives from a query string.
_FLOW_ORDER = {
    "net_desc":  "(a.buy_r - a.sell_r) DESC NULLS LAST",
    "net_asc":   "(a.buy_r - a.sell_r) ASC NULLS LAST",
    # Per-capita ratio, computed in SQL so the LIMIT selects the right rows
    # rather than sorting one page of an arbitrary four hundred. NULLIF guards
    # the same division the Python above guards.
    "power_desc": ("((a.buy_r::float8 / NULLIF(a.nbuy_r,0)) / "
                   "NULLIF(a.sell_r::float8 / NULLIF(a.nsell_r,0), 0)) DESC NULLS LAST"),
    "power_asc":  ("((a.buy_r::float8 / NULLIF(a.nbuy_r,0)) / "
                   "NULLIF(a.sell_r::float8 / NULLIF(a.nsell_r,0), 0)) ASC NULLS LAST"),
    "pcbuy_desc": "(a.buy_r::float8 / NULLIF(a.nbuy_r,0)) DESC NULLS LAST",
    "value_desc": "(a.buy_r + a.sell_r) DESC NULLS LAST",
}

FLOW_SORTS = [("net_desc", "بیشترین ورود پول حقیقی"),
              ("net_asc", "بیشترین خروج پول حقیقی"),
              ("power_desc", "بیشترین قدرت خریدار"),
              ("power_asc", "کمترین قدرت خریدار"),
              ("pcbuy_desc", "بیشترین سرانهٔ خرید"),
              ("value_desc", "بیشترین گردش حقیقی")]


def flow_history(kind, ticker, bars=120):
    """One symbol's daily flow, oldest→newest — the panel on the detail page."""
    if not _table_exists("ri_history"):
        return []
    rows = db._rows(
        """SELECT j_date, date, no_buy_r, no_sell_r, vol_buy_r, vol_sell_r,
                  val_buy_r, val_sell_r, val_buy_i, val_sell_i
             FROM ri_history WHERE kind = %s AND ticker = %s
            ORDER BY date DESC LIMIT %s""", (kind, ticker, bars))
    out = []
    for r in reversed(rows):
        nbuy, nsell = float(r["no_buy_r"] or 0), float(r["no_sell_r"] or 0)
        buy, sell = float(r["val_buy_r"] or 0), float(r["val_sell_r"] or 0)
        pc_buy = buy / nbuy if nbuy > 0 else None
        pc_sell = sell / nsell if nsell > 0 else None
        out.append({"j_date": r["j_date"], "net": buy - sell,
                    "buy_r": buy, "sell_r": sell,
                    "pc_buy": pc_buy, "pc_sell": pc_sell,
                    "power": (pc_buy / pc_sell) if (pc_buy and pc_sell) else None})
    return out


@_cached("flowtotals")
def flow_totals(kind="stock", days=1, as_of=None):
    """Whole-market net retail flow over the window — the headline on the page.

    Kept separate from money_flow() rather than summed from its rows: that
    function is LIMITed to what the table shows, and a total computed from a
    truncated list is a wrong number that looks right."""
    if not _table_exists("ri_history"):
        return None
    lo, hi, j_lo, j_hi = _flow_window(kind, days, as_of)
    if lo is None:
        return None
    r = db._one(
        """SELECT SUM(val_buy_r - val_sell_r) net,
                  SUM(val_buy_r) buy_r, SUM(val_sell_r) sell_r,
                  SUM(val_buy_i - val_sell_i) net_i,
                  COUNT(DISTINCT ticker) symbols
             FROM ri_history WHERE kind = %s AND date >= %s AND date <= %s""",
        (kind, lo, hi))
    if not r:
        return None
    r = dict(r)
    r.update({"lo": j_lo, "hi": j_hi, "days": days})
    return r


@_cached("flowsector")
def flow_by_sector(kind="stock", days=5, as_of=None, limit=40):
    """Net retail flow grouped by گروه صنعت — «پول وارد کدام صنعت شد؟», which is
    the question the per-symbol table cannot answer at a glance."""
    if kind != "stock" or not _table_exists("ri_history"):
        return []
    lo, hi, _j_lo, _j_hi = _flow_window(kind, days, as_of)
    if lo is None:
        return []
    rows = db._rows(
        """SELECT s.sector,
                  SUM(r.val_buy_r - r.val_sell_r) net,
                  SUM(r.val_buy_r + r.val_sell_r) turnover,
                  COUNT(DISTINCT r.ticker) symbols
             FROM ri_history r JOIN stocks s ON s.ticker = r.ticker
            WHERE r.kind = %s AND r.date >= %s AND r.date <= %s
              AND s.sector IS NOT NULL AND s.sector <> ''
            GROUP BY s.sector
           HAVING SUM(r.val_buy_r - r.val_sell_r) IS NOT NULL
            ORDER BY net DESC LIMIT %s""",
        (kind, lo, hi, limit))
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# تابلوی زنده — the /live page
# ---------------------------------------------------------------------------
#: The board's sorts, same fixed-map discipline as the flow page.
_BOARD_ORDER = {
    "value_desc": "value DESC NULLS LAST",
    "pct_desc":   "pct_final DESC NULLS LAST",
    "pct_asc":    "pct_final ASC NULLS LAST",
    "bq_desc":    "bq_value DESC NULLS LAST",
    "sq_desc":    "sq_value DESC NULLS LAST",
    "bqpc_desc":  "bqpc DESC NULLS LAST",
    "cap_desc":   "market_cap DESC NULLS LAST",
    "volume_desc": "volume DESC NULLS LAST",
    # «نسبت قیمت به درآمد»: only meaningful where EPS is positive, and a
    # loss-making symbol must not sort to the top of «ارزان‌ترین P/E».
    "pe_asc":     "(CASE WHEN eps > 0 THEN final / eps END) ASC NULLS LAST",
}

BOARD_SORTS = [("value_desc", "بیشترین ارزش معاملات"),
               ("pct_desc", "بیشترین رشد"),
               ("pct_asc", "بیشترین افت"),
               ("bq_desc", "بزرگ‌ترین صف خرید"),
               ("sq_desc", "بزرگ‌ترین صف فروش"),
               ("bqpc_desc", "بیشترین سرانهٔ صف خرید"),
               ("cap_desc", "بزرگ‌ترین ارزش بازار"),
               ("volume_desc", "بیشترین حجم"),
               ("pe_asc", "کمترین P/E")]

#: «وضعیت صف» filter. Queue value is in rials and a stray lot of two shares at
#: the limit price is not a queue, so «صف خرید» means a buy queue worth at least
#: this much — a hundred million rials (ten million tomans).
QUEUE_FLOOR = 100_000_000


def board_latest():
    """(j_date, captured_at) of the newest snapshot, or (None, None)."""
    if not _table_exists("market_snapshot"):
        return None, None
    r = db._one("SELECT j_date, MAX(captured_at) c FROM market_snapshot "
                "GROUP BY j_date ORDER BY j_date DESC LIMIT 1")
    return (r["j_date"], r["c"]) if r else (None, None)


def board(sector=None, market=None, queue=None, q=None, order="value_desc",
          limit=300, j_date=None):
    """The live board, server-filtered and LIMITed.

    Why the limit is not "all of it": the snapshot holds ~1,540 symbols and
    rendering them all is 2 MB of markup and ~40,000 DOM nodes per navigation —
    the same measurement that moved the four heavy tables onto Vue islands. This
    page keeps the server round trip instead and answers a QUESTION («بزرگ‌ترین
    صف خرید», «کمترین P/E») rather than dumping the board, so the default view is
    300 rows that mean something.
    """
    if not _table_exists("market_snapshot"):
        return {"rows": [], "j_date": None, "captured": None, "total": 0}
    jd, captured = board_latest()
    jd = j_date or jd
    if not jd:
        return {"rows": [], "j_date": None, "captured": None, "total": 0}

    clauses, params = ["j_date = %s"], [jd]
    if sector:
        clauses.append("sector = %s")
        params.append(sector)
    if market:
        clauses.append("market = %s")
        params.append(market)
    if queue == "buy":
        clauses.append("bq_value >= %s")
        params.append(QUEUE_FLOOR)
    elif queue == "sell":
        clauses.append("sq_value >= %s")
        params.append(QUEUE_FLOOR)
    if q:
        clauses.append("(ticker ILIKE %s OR name ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    where = " AND ".join(clauses)
    order_sql = _BOARD_ORDER.get(order, _BOARD_ORDER["value_desc"])

    total = (db._one(f"SELECT COUNT(*) n FROM market_snapshot WHERE {where}",
                     tuple(params)) or {}).get("n", 0)
    rows = db._rows(
        f"""SELECT ticker, name, market, sector, trade_type, board_time,
                   open, high, low, close, final, pct_close, pct_final,
                   day_ul, day_ll, value, volume, no,
                   bq_value, sq_value, bqpc, sqpc,
                   vol_buy_r, vol_buy_i, vol_sell_r, vol_sell_i,
                   no_buy_r, no_sell_r, share_no, base_vol, market_cap, eps,
                   CASE WHEN eps > 0 THEN final / eps END AS pe
              FROM market_snapshot
             WHERE {where}
             ORDER BY {order_sql}
             LIMIT %s""",
        tuple(params) + (limit,))
    return {"rows": [dict(r) for r in rows], "j_date": jd, "captured": captured,
            "total": total, "shown": len(rows)}


def board_sectors():
    if not _table_exists("market_snapshot"):
        return []
    jd, _ = board_latest()
    if not jd:
        return []
    return [r["sector"] for r in db._rows(
        "SELECT DISTINCT sector FROM market_snapshot "
        "WHERE j_date = %s AND sector IS NOT NULL AND sector <> '' ORDER BY sector",
        (jd,))]


def board_markets():
    if not _table_exists("market_snapshot"):
        return []
    jd, _ = board_latest()
    if not jd:
        return []
    return [r["market"] for r in db._rows(
        "SELECT DISTINCT market FROM market_snapshot "
        "WHERE j_date = %s AND market IS NOT NULL AND market <> '' ORDER BY market",
        (jd,))]


def board_totals():
    """The board's headline tiles: how much the whole market traded, how many
    symbols are up / down, and how much money is sitting in queues."""
    if not _table_exists("market_snapshot"):
        return None
    jd, captured = board_latest()
    if not jd:
        return None
    r = db._one(
        """SELECT COUNT(*) symbols,
                  SUM(value) value, SUM(volume) volume,
                  SUM(market_cap) cap,
                  COUNT(*) FILTER (WHERE pct_final > 0)  up,
                  COUNT(*) FILTER (WHERE pct_final < 0)  down,
                  COUNT(*) FILTER (WHERE pct_final = 0)  flat,
                  SUM(bq_value) bq, SUM(sq_value) sq,
                  COUNT(*) FILTER (WHERE bq_value >= %s) bq_n,
                  COUNT(*) FILTER (WHERE sq_value >= %s) sq_n,
                  -- Intraday retail net, in rials. The snapshot carries VOLUMES
                  -- rather than values for the حقیقی/حقوقی split, so this is
                  -- priced at the symbol's own settlement — which is what the
                  -- exchange's own board does.
                  SUM((vol_buy_r - vol_sell_r) * final) net_r
             FROM market_snapshot
            WHERE j_date = %s""",
        (QUEUE_FLOOR, QUEUE_FLOOR, jd))
    if not r:
        return None
    out = dict(r)
    out.update({"j_date": jd, "captured": captured})
    return out


def order_book(ticker):
    """Five depths for one symbol, best quote first."""
    if not _table_exists("order_book"):
        return []
    return [dict(r) for r in db._rows(
        """SELECT depth, sell_no, sell_vol, sell_price,
                  buy_price, buy_vol, buy_no, day_ul, day_ll, captured_at
             FROM order_book WHERE ticker = %s ORDER BY depth""", (ticker,))]


# ---------------------------------------------------------------------------
# The intraday datasets, read back
# ---------------------------------------------------------------------------
def queue_history(kind, ticker, bars=60):
    """One symbol's end-of-session queue values, newest last.

    This is the back-fillable twin of the دیده‌بان snapshot: same four numbers,
    but reaching into the past. Where both exist for a session they agree."""
    if not _table_exists("queue_history"):
        return []
    rows = db._rows(
        """SELECT j_date, board_time, day_ul, day_ll, bq_value, sq_value, bqpc, sqpc
             FROM queue_history WHERE kind = %s AND ticker = %s
            ORDER BY date DESC LIMIT %s""", (kind, ticker, bars))
    return [dict(r) for r in reversed(rows)]


def shareholders(ticker):
    """Holders above 1%, largest first, plus how much of the symbol they cover.

    The percentages deliberately do NOT sum to 100: everything below the 1%
    disclosure threshold is absent from the source, and the remainder is the
    float in small hands. `covered` is reported so the page can say so rather
    than leaving a reader to wonder why the column stops at 46%."""
    if not _table_exists("shareholders"):
        return {"rows": [], "covered": None, "captured": None}
    rows = db._rows(
        """SELECT holder, market, share_no, share_pct, changes, captured_at, j_date
             FROM shareholders WHERE ticker = %s
            ORDER BY share_pct DESC NULLS LAST""", (ticker,))
    if not rows:
        return {"rows": [], "covered": None, "captured": None}
    covered = sum(float(r["share_pct"] or 0) for r in rows)
    return {"rows": [dict(r) for r in rows], "covered": covered,
            "captured": rows[0]["captured_at"], "j_date": rows[0]["j_date"]}


def intraday_coverage(ticker):
    """Which sessions this symbol has a tape for, and how big it is.

    The tapes are per-symbol tools fetched one window at a time, so "do I have
    this symbol, and for which days" is the only question worth answering on a
    detail page — rendering 8,000 ticks there would be a different product."""
    out = {}
    for key, table in (("trades", "intraday_trades"),
                       ("orderbook", "intraday_orderbook")):
        if not _table_exists(table):
            out[key] = None
            continue
        r = db._one(
            f"""SELECT COUNT(*) rows, COUNT(DISTINCT date) days,
                       MIN(j_date) lo, MAX(j_date) hi
                  FROM {table} WHERE ticker = %s""", (ticker,))
        out[key] = dict(r) if r and r["rows"] else None
    return out


def trades(ticker, j_date, limit=2000, include_canceled=False):
    """One session's tick tape, oldest first.

    Cancelled trades are excluded by default — the exchange voids erroneous
    ones and summing them double-counts the day — but they are in the table, so
    `include_canceled` can show the tape exactly as TSETMC published it."""
    if not _table_exists("intraday_trades"):
        return []
    where = "" if include_canceled else " AND NOT canceled"
    rows = db._rows(
        f"""SELECT time, seq, volume, price, canceled FROM intraday_trades
             WHERE ticker = %s AND j_date = %s{where}
             ORDER BY time, seq LIMIT %s""", (ticker, j_date, limit))
    return [dict(r) for r in rows]


def fundamentals(ticker):
    """EPS / ارزش بازار / حجم مبنا / تعداد سهام for one symbol, from the newest
    snapshot that HAS them. Not necessarily today's: a symbol suspended for a
    month still has an EPS, and reading only the latest j_date would blank the
    panel for exactly the symbols someone is looking one up for."""
    if not _table_exists("market_snapshot"):
        return None
    r = db._one(
        """SELECT j_date, eps, market_cap, base_vol, share_no, sector, market,
                  day_ul, day_ll, final, bq_value, sq_value, bqpc, sqpc,
                  CASE WHEN eps > 0 THEN final / eps END AS pe
             FROM market_snapshot
            WHERE ticker = %s AND (eps IS NOT NULL OR market_cap IS NOT NULL)
            ORDER BY date DESC LIMIT 1""", (ticker,))
    return dict(r) if r else None
