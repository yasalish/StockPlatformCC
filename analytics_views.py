"""
analytics_views.py — تحلیل‌های بازار به‌صورت ویوی مادی‌شده
The market-wide analytics as PostgreSQL MATERIALIZED VIEWs.

Why. Every market page used to recompute its numbers from the raw price table on
each request: db._gainer() window-functioned a two-year slice of all 8.1M rows,
and the strategy / filter / score scans pulled 300 bars per ticker and evaluated
the indicators in Python. The prices only change once a day, so all of it is now
computed once at refresh time and read back as a plain indexed SELECT.

Every view here reproduces db.py's maths BIT-EXACTLY — not approximately. Three
places where that took real care, all verified value-by-value against db.py:

  * `power(x, 0.5)`, never `sqrt(x)`. db.py writes `var ** 0.5`, and CPython's
    pow() and postgres' sqrt() disagree by 1 ULP on some inputs.
  * Neumaier compensated summation wherever db.py calls the builtin sum() over
    FLOATS (the MACD signal seed, and _stdev's two sums). CPython >= 3.12 gives
    float sum() Neumaier compensation (gh-100425), so neither SUM() nor a naive
    left-to-right fold reproduces it.
  * Rounding stays in Python. The score view stores the raw composite; db.py
    applies round(x, 1) at read time, because python rounds half-to-even on the
    binary double while postgres rounds half-up on the decimal expansion.

SMA and Bollinger use plain window aggregates even though db.py slides its sums
incrementally. That is safe *for this data specifically*: every adjusted OHLC
value is a whole number below 2.3e6, so sums of <=200 of them (and of their
squares) are exactly representable in float8 and the summation order cannot
matter. If fractional prices ever land in these tables that assumption dies —
db.verify_analytics() is what will tell you.

The as_of baked into every view is MAX(j_date) at refresh time. db.py falls back
to the old live computation when a caller asks for a different (historical) date.
"""

# The base view keeps 800 bars/ticker: enough for _perf_prices' 720-day window
# (rn <= 721). rn_c <= rn always, so it covers the filter scan's ranking too.
MAX_RN = 800
# db.py's three scans all fetch `WHERE rn <= 300`.
SCAN_BARS = 300

TBL = {"stock": ("stockpricehistory", "stocks"), "etf": ("etfpricehistory", "etf")}

# Reference-table projections, mirroring the SELECTs inside db.py.
META_GAIN = {
    "stock": "SELECT stockid AS id, ticker, name, market, sector, sub_sector, "
             "NULL::text AS type FROM stocks",
    "etf":   "SELECT id, ticker, name, NULL::text AS market, NULL::text AS sector, "
             "NULL::text AS sub_sector, type FROM etf",
}
META_SCAN = {
    "stock": "SELECT stockid AS id, ticker, name, sector AS grp, sub_sector AS sub FROM stocks",
    "etf":   "SELECT id, ticker, name, type AS grp, NULL::text AS sub FROM etf",
}


# --------------------------------------------------------------------------
# small SQL helpers
# --------------------------------------------------------------------------
def _cutoff_sql(col, years):
    """SQL twin of db._cutoff(): same month-day, year - `years`, zero-padded."""
    return (f"lpad((substring({col},1,4)::int - {years})::text, 4, '0') "
            f"|| substring({col},5)")


def cal(kind):
    """The trading calendar: one row per distinct (j_date, date).

    ~6k rows, and the only place j_date is still COMPARED. The window bounds are
    defined in Jalali (db._cutoff: same month-day, N years back) but the per-row
    filtering runs on the real `date` column, so the bounds are resolved through
    this table once per view build. That substitution is exact because
    verify_schema.py proves j_date <-> date is 1:1 and order-preserving."""
    price, _ = TBL[kind]
    return f"""
CREATE MATERIALIZED VIEW mv_cal_{kind} AS
SELECT DISTINCT j_date, date FROM {price}
"""


def _bounds(kind, years):
    """(as_of, cutoff) on the DATE axis, derived from the Jalali definition."""
    jcut = _cutoff_sql(f"(SELECT MAX(j_date) FROM mv_cal_{kind})", years)
    return (f"""
    SELECT (SELECT MAX(date) FROM mv_cal_{kind}) AS as_of,
           (SELECT MIN(date) FROM mv_cal_{kind}
             WHERE j_date >= {jcut}) AS cutoff""")


def _pct(base, latest="b.latest"):
    """SQL twin of db._pct(cur, base) — NULL when base is falsy (NULL or 0)."""
    return (f"CASE WHEN {base} IS NOT NULL AND {base} <> 0 "
            f"THEN ({latest} - {base}) / {base} * 100.0 END")


def _truthy(e):
    """python's `if x` on a float: False for 0.0 as well as for None."""
    return f"({e} IS NOT NULL AND {e} <> 0)"


def _at(col, k):
    """python px[-1-k]  ->  the row where i = n - k."""
    return f"MAX({col}) FILTER (WHERE i = n - {k})"


def _neumaier(cte, src, val, order, limit=None):
    """Recursive Neumaier fold of `val` over `src`, per ticker, in `order`."""
    lim = f" AND s.{order} <= {limit}" if limit else ""
    return f"""
{cte} AS (
    SELECT ticker, {order}, {val} AS s, 0.0::float8 AS c FROM {src} WHERE {order} = 1
    UNION ALL
    SELECT a.ticker, s.{order}, a.s + s.{val},
           a.c + CASE WHEN abs(a.s) >= abs(s.{val})
                      THEN (a.s - (a.s + s.{val})) + s.{val}
                      ELSE (s.{val} - (a.s + s.{val})) + a.s END
    FROM {cte} a JOIN {src} s ON s.ticker = a.ticker AND s.{order} = a.{order} + 1{lim}
)"""


# --------------------------------------------------------------------------
# layer 1 — raw bars + all-time stats
# --------------------------------------------------------------------------
def bars(kind):
    price, _ = TBL[kind]
    return f"""
CREATE MATERIALIZED VIEW mv_bars_{kind} AS
WITH w AS ({_bounds(kind, 4)}
), ranked AS (
    SELECT p.ticker, p.j_date, p.date,
           ROW_NUMBER() OVER (PARTITION BY p.ticker ORDER BY p.date DESC) AS rn,
           CASE WHEN p.adj_close > 0 THEN ROW_NUMBER() OVER (
                PARTITION BY p.ticker, (p.adj_close > 0) ORDER BY p.date DESC)
           END AS rn_c,
           p.adj_open::float8  AS o, p.adj_high::float8 AS h,
           p.adj_low::float8   AS l, p.adj_close::float8 AS c,
           p.adj_final::float8 AS v
    FROM {price} p, w
    WHERE p.adj_final > 0 AND p.date <= w.as_of AND p.date >= w.cutoff
)
SELECT ticker, j_date, date, rn, rn_c, o, h, l, c, v FROM ranked WHERE rn <= {MAX_RN}
"""


def alltime(kind):
    price, ref = TBL[kind]
    return f"""
CREATE MATERIALIZED VIEW mv_alltime_{kind} AS
WITH w AS (SELECT MAX(date) AS as_of FROM mv_cal_{kind}),
agg AS (
    SELECT p.ticker, MIN(p.adj_final::float8) AS mn, MAX(p.adj_final::float8) AS mx
    FROM {price} p, w
    WHERE p.adj_final > 0 AND p.date <= w.as_of
    GROUP BY p.ticker
),
firsts AS (
    SELECT s.ticker, f.fv
    FROM {ref} s CROSS JOIN w
    JOIN LATERAL (
        SELECT p.adj_final::float8 AS fv FROM {price} p
        WHERE p.ticker = s.ticker AND p.adj_final > 0 AND p.date <= w.as_of
        ORDER BY p.date ASC LIMIT 1) f ON true
)
SELECT COALESCE(a.ticker, f.ticker) AS ticker, a.mn, a.mx, f.fv AS first_v
FROM agg a FULL OUTER JOIN firsts f ON f.ticker = a.ticker
"""


# --------------------------------------------------------------------------
# layer 2a — the gainer tables (db._gainer)
# --------------------------------------------------------------------------
def gainer(kind, periods, name):
    price, _ = TBL[kind]
    picks = ",\n           ".join(
        f"MAX(v) FILTER (WHERE rn = {p['n'] + 1}) AS raw_{p['key']}" for p in periods)
    pcts = ",\n       ".join(f"{_pct('b.raw_' + p['key'])} AS {p['key']}" for p in periods)
    return f"""
CREATE MATERIALIZED VIEW {name}_{kind} AS
WITH lim AS ({_bounds(kind, 2)}
), base AS (
    SELECT ticker,
           MAX(j_date) FILTER (WHERE rn = 1) AS ldate,
           MAX(v)      FILTER (WHERE rn = 1) AS latest,
           {picks}
    FROM mv_bars_{kind}, lim
    WHERE date <= lim.as_of AND date >= lim.cutoff
    GROUP BY ticker
    HAVING MAX(v) FILTER (WHERE rn = 1) IS NOT NULL
)
SELECT m.id, m.ticker, m.name, m.market, m.sector, m.sub_sector, m.type,
       b.latest, b.ldate,
       {pcts}
FROM ({META_GAIN[kind]}) m
JOIN base b ON b.ticker = m.ticker
"""


def perf(kind, perf_periods):
    price, _ = TBL[kind]
    picks, cols = [], []
    for p in perf_periods:
        k, rn = p["key"], p["n"] + 1
        picks.append(f"MAX(v) FILTER (WHERE rn = {rn})  AS g_{k}")
        picks.append(f"MAX(v) FILTER (WHERE rn <= {rn}) AS cmax_{k}")
        picks.append(f"MIN(v) FILTER (WHERE rn <= {rn}) AS fmin_{k}")
        cols.append(f"{_pct('b.g_' + k)} AS {k}_gain")
        cols.append(f"{_pct('b.cmax_' + k)} AS {k}_ceil")
        cols.append(f"{_pct('b.fmin_' + k)} AS {k}_floor")
    picks_sql = ",\n           ".join(picks)
    cols_sql = ",\n       ".join(cols)
    return f"""
CREATE MATERIALIZED VIEW mv_perf_prices_{kind} AS
WITH lim AS ({_bounds(kind, 4)}
), base AS (
    SELECT ticker,
           MAX(v) FILTER (WHERE rn = 1) AS latest,
           {picks_sql}
    FROM mv_bars_{kind}, lim
    WHERE date <= lim.as_of AND date >= lim.cutoff
    GROUP BY ticker
    HAVING MAX(v) FILTER (WHERE rn = 1) IS NOT NULL
)
SELECT b.ticker, b.latest,
       {cols_sql},
       {_pct('a.first_v')} AS first_gain,
       {_pct('a.mx')}      AS first_ceil,
       {_pct('a.mn')}      AS first_floor
FROM base b
LEFT JOIN mv_alltime_{kind} a ON a.ticker = b.ticker
"""


# --------------------------------------------------------------------------
# layer 2b — the indicator kernel (db._sma/_ema/_rsi/_macd/_boll_series)
# --------------------------------------------------------------------------
def _rsi_value(ag, al):
    """db.py: 100.0 if al == 0 else (0.0 if ag == 0 else 100 - 100/(1 + ag/al))"""
    return (f"CASE WHEN {al} = 0 THEN 100.0 WHEN {ag} = 0 THEN 0.0 "
            f"ELSE 100 - 100 / (1 + {ag} / {al}) END")


def kernel(kind):
    price, _ = TBL[kind]
    return f"""
CREATE MATERIALIZED VIEW mv_ind_{kind} AS
WITH RECURSIVE
lim AS ({_bounds(kind, 2)}
),
win AS (
    SELECT b.ticker, b.j_date, b.o, b.h, b.l, b.c, b.v,
           ROW_NUMBER() OVER (PARTITION BY b.ticker ORDER BY b.rn DESC) AS i,
           COUNT(*)     OVER (PARTITION BY b.ticker)                    AS n
    FROM mv_bars_{kind} b, lim
    WHERE b.rn <= {SCAN_BARS} AND b.date >= lim.cutoff AND b.date <= lim.as_of
),
simple AS (
    SELECT w.*,
           CASE WHEN i >= 20  THEN SUM(v) OVER (PARTITION BY ticker ORDER BY i
                ROWS BETWEEN 19  PRECEDING AND CURRENT ROW) / 20.0  END AS sma20,
           CASE WHEN i >= 50  THEN SUM(v) OVER (PARTITION BY ticker ORDER BY i
                ROWS BETWEEN 49  PRECEDING AND CURRENT ROW) / 50.0  END AS sma50,
           CASE WHEN i >= 200 THEN SUM(v) OVER (PARTITION BY ticker ORDER BY i
                ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) / 200.0 END AS sma200,
           CASE WHEN i >= 20  THEN SUM(v)     OVER (PARTITION BY ticker ORDER BY i
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) / 20.0 END AS bmean,
           CASE WHEN i >= 20  THEN SUM(v * v) OVER (PARTITION BY ticker ORDER BY i
                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) / 20.0 END AS bms
    FROM win w
),
ema_seed AS (
    SELECT ticker, 12::bigint AS i, SUM(v) / 12.0 AS e12, NULL::float8 AS e26
    FROM win WHERE i <= 12 GROUP BY ticker HAVING COUNT(*) = 12
    UNION ALL
    SELECT ticker, 26::bigint, NULL::float8, SUM(v) / 26.0
    FROM win WHERE i <= 26 GROUP BY ticker HAVING COUNT(*) = 26
),
e12 AS (
    SELECT ticker, i, e12 AS ema FROM ema_seed WHERE e12 IS NOT NULL
    UNION ALL
    SELECT w.ticker, w.i, w.v * (2.0/13.0) + p.ema * (1 - 2.0/13.0)
    FROM e12 p JOIN win w ON w.ticker = p.ticker AND w.i = p.i + 1
),
e26 AS (
    SELECT ticker, i, e26 AS ema FROM ema_seed WHERE e26 IS NOT NULL
    UNION ALL
    SELECT w.ticker, w.i, w.v * (2.0/27.0) + p.ema * (1 - 2.0/27.0)
    FROM e26 p JOIN win w ON w.ticker = p.ticker AND w.i = p.i + 1
),
macd AS (
    SELECT a.ticker, a.i, a.ema - b.ema AS m,
           ROW_NUMBER() OVER (PARTITION BY a.ticker ORDER BY a.i) AS j
    FROM e12 a JOIN e26 b ON b.ticker = a.ticker AND b.i = a.i
),
-- signal = EMA(9) of the COMPACTED macd list. Its seed is sum(vals[:9])/9 over
-- FLOATS, so it needs CPython's Neumaier compensation (see module docstring).
seedacc AS (
    SELECT ticker, j, m AS s, 0.0::float8 AS c FROM macd WHERE j = 1
    UNION ALL
    SELECT a.ticker, m.j, a.s + m.m,
           a.c + CASE WHEN abs(a.s) >= abs(m.m)
                      THEN (a.s - (a.s + m.m)) + m.m
                      ELSE (m.m - (a.s + m.m)) + a.s END
    FROM seedacc a JOIN macd m ON m.ticker = a.ticker AND m.j = a.j + 1 AND m.j <= 9
),
sig_seed AS (
    SELECT ticker, 9::bigint AS j, (s + c) / 9.0 AS s FROM seedacc WHERE j = 9
),
sig AS (
    SELECT ticker, j, s FROM sig_seed
    UNION ALL
    SELECT m.ticker, m.j, m.m * (2.0/10.0) + p.s * (1 - 2.0/10.0)
    FROM sig p JOIN macd m ON m.ticker = p.ticker AND m.j = p.j + 1
),
chg AS (
    SELECT ticker, i, v - LAG(v) OVER (PARTITION BY ticker ORDER BY i) AS ch FROM win
),
rsi14_seed AS (
    SELECT ticker, 15::bigint AS i,
           SUM(GREATEST(ch, 0.0)) / 14.0 AS ag, SUM(GREATEST(-ch, 0.0)) / 14.0 AS al
    FROM chg WHERE i BETWEEN 2 AND 15 GROUP BY ticker HAVING COUNT(*) = 14
),
rsi14 AS (
    SELECT ticker, i, ag, al FROM rsi14_seed
    UNION ALL
    SELECT c.ticker, c.i,
           (p.ag * 13 + GREATEST(c.ch, 0.0)) / 14.0,
           (p.al * 13 + GREATEST(-c.ch, 0.0)) / 14.0
    FROM rsi14 p JOIN chg c ON c.ticker = p.ticker AND c.i = p.i + 1
),
rsi2_seed AS (
    SELECT ticker, 3::bigint AS i,
           SUM(GREATEST(ch, 0.0)) / 2.0 AS ag, SUM(GREATEST(-ch, 0.0)) / 2.0 AS al
    FROM chg WHERE i BETWEEN 2 AND 3 GROUP BY ticker HAVING COUNT(*) = 2
),
rsi2 AS (
    SELECT ticker, i, ag, al FROM rsi2_seed
    UNION ALL
    SELECT c.ticker, c.i,
           (p.ag * 1 + GREATEST(c.ch, 0.0)) / 2.0,
           (p.al * 1 + GREATEST(-c.ch, 0.0)) / 2.0
    FROM rsi2 p JOIN chg c ON c.ticker = p.ticker AND c.i = p.i + 1
)
SELECT s.ticker, s.i, s.n, s.j_date, s.o, s.h, s.l, s.c, s.v,
       s.sma20, s.sma50, s.sma200,
       CASE WHEN s.bms - s.bmean * s.bmean > 0
            THEN s.bmean + 2.0 * power(s.bms - s.bmean * s.bmean, 0.5)
            WHEN s.bmean IS NOT NULL THEN s.bmean END AS boll_up,
       CASE WHEN s.bms - s.bmean * s.bmean > 0
            THEN s.bmean - 2.0 * power(s.bms - s.bmean * s.bmean, 0.5)
            WHEN s.bmean IS NOT NULL THEN s.bmean END AS boll_lo,
       md.m AS macd, sg.s AS macd_sig,
       CASE WHEN md.m IS NOT NULL AND sg.s IS NOT NULL THEN md.m - sg.s END AS macd_hist,
       {_rsi_value('r14.ag', 'r14.al')} AS rsi14,
       {_rsi_value('r2.ag', 'r2.al')}   AS rsi2
FROM simple s
LEFT JOIN macd  md  ON md.ticker  = s.ticker AND md.i = s.i
LEFT JOIN sig   sg  ON sg.ticker  = s.ticker AND sg.j = md.j
LEFT JOIN rsi14 r14 ON r14.ticker = s.ticker AND r14.i = s.i
LEFT JOIN rsi2  r2  ON r2.ticker  = s.ticker AND r2.i  = s.i
"""


# --------------------------------------------------------------------------
# layer 3 — the three scans
# --------------------------------------------------------------------------
def _crosses(kind):
    """Per-bar crossing flags. db._cross_* skip a step when any operand is None,
    which falls out for free: NULL comparisons yield NULL and bool_or drops them.
    _boll_bounce is the exception — it ACCEPTS a None rsi, hence the explicit
    (rsi14 IS NULL OR rsi14 < 40)."""
    return f"""
feat AS (
    SELECT ticker, n, i, v, o, h, l, c, sma20, sma50, sma200,
           boll_up, boll_lo, macd, macd_sig, rsi14, rsi2,
           LAG(sma20)    OVER w AS p_sma20,
           LAG(sma50)    OVER w AS p_sma50,
           LAG(sma200)   OVER w AS p_sma200,
           LAG(macd)     OVER w AS p_macd,
           LAG(macd_sig) OVER w AS p_macd_sig,
           LAG(rsi14)    OVER w AS p_rsi14,
           LAG(v)        OVER w AS p_v,
           LAG(boll_up)  OVER w AS p_boll_up
    FROM mv_ind_{kind}
    WINDOW w AS (PARTITION BY ticker ORDER BY i)
),
flags AS (
    SELECT f.*,
           (p_sma50 <= p_sma200   AND sma50 > sma200)    AS x_50_200,
           (p_sma20 <= p_sma50    AND sma20 > sma50)     AS x_20_50,
           (p_macd  <= p_macd_sig AND macd > macd_sig)   AS x_macd_sig,
           (p_macd  <= 0.0 AND 0.0 < macd)               AS x_macd_0,
           (p_rsi14 <= 30  AND 30  < rsi14)              AS x_rsi_30,
           (p_rsi14 <= 50  AND 50  < rsi14)              AS x_rsi_50,
           (p_v <= p_boll_up AND v > boll_up)            AS x_px_bollup,
           (boll_lo IS NOT NULL AND v <= boll_lo
             AND (rsi14 IS NULL OR rsi14 < 40))          AS boll_touch
    FROM feat f
)"""


def strategy(kind):
    return f"""
CREATE MATERIALIZED VIEW mv_strategy_{kind} AS
WITH {_crosses(kind)},
agg AS (
    SELECT ticker, n,
           {_at('v', 0)} AS close, {_at('v', 3)} AS v_m4, {_at('v', 5)} AS v_m6,
           {_at('v', 21)} AS v_m22, {_at('v', 60)} AS v_m61, {_at('v', 252)} AS v_m253,
           {_at('sma20', 0)} AS sma20, {_at('sma50', 0)} AS sma50,
           {_at('sma200', 0)} AS sma200,
           {_at('sma50', 20)} AS sma50_m21, {_at('sma200', 20)} AS sma200_m21,
           {_at('macd', 0)} AS macd, {_at('macd_sig', 0)} AS macd_sig,
           {_at('rsi14', 0)} AS rsi, {_at('rsi2', 0)} AS rsi2,
           {_at('boll_up', 0)} AS bu, {_at('boll_lo', 0)} AS bl,
           {_at('boll_up', 3)} AS bu_m4, {_at('boll_lo', 3)} AS bl_m4,
           MAX(v) FILTER (WHERE i >= GREATEST(1, n - 239)) AS hi_52w,
           -- lookback L  ->  python range(max(1, n-L), n)  ->  i in [max(2, n-L+1), n]
           bool_or(x_50_200)    FILTER (WHERE i >= GREATEST(2, n - 14)) AS c_golden,
           bool_or(x_20_50)     FILTER (WHERE i >= GREATEST(2, n - 9))  AS c_2050,
           bool_or(x_macd_sig)  FILTER (WHERE i >= GREATEST(2, n - 4))  AS c_macdsig,
           bool_or(x_macd_0)    FILTER (WHERE i >= GREATEST(2, n - 4))  AS c_macd0,
           bool_or(x_rsi_30)    FILTER (WHERE i >= GREATEST(2, n - 4))  AS c_rsi30,
           bool_or(x_rsi_50)    FILTER (WHERE i >= GREATEST(2, n - 4))  AS c_rsi50,
           bool_or(x_px_bollup) FILTER (WHERE i >= GREATEST(2, n - 2))  AS c_bollup,
           -- _boll_bounce uses range(max(0, n-L), n), one position earlier
           bool_or(boll_touch)  FILTER (WHERE i >= GREATEST(1, n - 4))  AS c_bolltouch
    FROM flags GROUP BY ticker, n
),
joined AS (
    SELECT a.*, m.id, m.name, m.grp AS "group", m.sub AS sub_group
    FROM agg a JOIN ({META_SCAN[kind]}) m ON m.ticker = a.ticker
    WHERE a.n >= 30                      -- db.py: `if len(px) < 30: continue`
),
sig AS (
    SELECT j.*,
           CASE WHEN n >= 253 AND v_m253 > 0 THEN v_m22 / v_m253 - 1 END AS mom,
           ARRAY_REMOVE(ARRAY[
             -- every _cross_* ALSO guards on the series being above its
             -- counterpart RIGHT NOW, not merely having crossed in the window
             CASE WHEN COALESCE(c_golden,false) AND sma50 IS NOT NULL
                       AND sma200 IS NOT NULL AND sma50 > sma200
                       AND {_truthy('sma200')} AND close > sma200      THEN 'golden' END,
             CASE WHEN COALESCE(c_2050,false) AND sma20 IS NOT NULL
                       AND sma50 IS NOT NULL AND sma20 > sma50
                       AND {_truthy('sma50')} AND close > sma50        THEN 'sma_20_50' END,
             CASE WHEN COALESCE(c_macdsig,false) AND macd IS NOT NULL
                       AND macd_sig IS NOT NULL AND macd > macd_sig
                       AND rsi IS NOT NULL AND rsi > 50                THEN 'macd_rsi' END,
             CASE WHEN COALESCE(c_macd0,false)
                       AND macd IS NOT NULL AND macd > 0.0             THEN 'macd_zero' END,
             CASE WHEN COALESCE(c_rsi30,false)
                       AND rsi IS NOT NULL AND rsi > 30                THEN 'rsi_bounce' END,
             CASE WHEN COALESCE(c_rsi50,false)
                       AND rsi IS NOT NULL AND rsi > 50                THEN 'rsi_50' END,
             CASE WHEN bl IS NOT NULL AND close > bl
                       AND COALESCE(c_bolltouch,false)                 THEN 'boll' END,
             CASE WHEN bu IS NOT NULL AND bu_m4 IS NOT NULL
                       AND COALESCE(c_bollup,false) AND close > bu
                       AND (bu - bl) > (bu_m4 - bl_m4)                 THEN 'boll_breakout' END,
             CASE WHEN {_truthy('sma50')} AND {_truthy('sma200')}
                       AND close > sma50 AND sma50 > sma200
                       AND sma50_m21 IS NOT NULL
                       AND sma50 > sma50_m21                           THEN 'uptrend' END,
             CASE WHEN {_truthy('sma200')} AND close > sma200
                       AND sma200_m21 IS NOT NULL
                       AND sma200 > sma200_m21                         THEN 'above_200' END,
             CASE WHEN n > 66 AND v_m61 > 0 AND (close / v_m61 - 1) > 0.05
                       AND close > v_m6                                THEN 'roc' END,
             CASE WHEN n >= 120 AND hi_52w > 0 AND close >= 0.97 * hi_52w
                       AND close > v_m6                                THEN 'high_52w' END,
             CASE WHEN {_truthy('sma200')} AND close > sma200
                       AND rsi2 IS NOT NULL AND rsi2 < 10              THEN 'rsi2' END,
             CASE WHEN n >= 253 AND v_m253 > 0
                       AND (close / v_m253 - 1) > 0                    THEN 'abs_mom' END
           ], NULL) AS signals
    FROM joined j
),
-- Jegadeesh-Titman top decile across the graded universe.
-- db.py: moms = sorted(...); thr = moms[int(0.9 * (len(moms) - 1))]
ranked AS (
    SELECT mom, ROW_NUMBER() OVER (ORDER BY mom) AS rk, COUNT(*) OVER () AS cnt
    FROM sig WHERE mom IS NOT NULL
),
thr AS (
    SELECT mom AS t FROM ranked WHERE cnt >= 20 AND rk = floor(0.9 * (cnt - 1))::int + 1
)
SELECT s.id, s.ticker, s.name, s."group", s.sub_group, s.close AS latest, s.rsi,
       CASE WHEN s.mom IS NOT NULL AND (SELECT t FROM thr) IS NOT NULL
                 AND s.mom >= (SELECT t FROM thr)
            THEN s.signals || 'xsec_mom'::text ELSE s.signals END AS signals
FROM sig s
"""


def filters(kind):
    return f"""
CREATE MATERIALIZED VIEW mv_filter_{kind} AS
WITH {_crosses(kind)},
agg AS (
    SELECT ticker, n,
           {_at('v', 0)} AS close, {_at('v', 1)} AS v_m2, {_at('v', 5)} AS v_m6,
           {_at('o', 0)} AS o1, {_at('h', 0)} AS h1, {_at('l', 0)} AS l1, {_at('c', 0)} AS c1,
           {_at('o', 1)} AS o2, {_at('h', 1)} AS h2, {_at('l', 1)} AS l2, {_at('c', 1)} AS c2,
           {_at('o', 2)} AS o3, {_at('h', 2)} AS h3, {_at('l', 2)} AS l3, {_at('c', 2)} AS c3,
           {_at('rsi14', 0)} AS rsi, {_at('macd', 0)} AS macd,
           {_at('macd_sig', 0)} AS macd_sig, {_at('sma200', 0)} AS sma200,
           {_at('boll_up', 0)} AS bu, {_at('boll_lo', 0)} AS bl
    FROM flags GROUP BY ticker, n
),
j AS (
    SELECT a.*, m.id, m.name, m.grp AS "group", m.sub AS sub_group
    FROM agg a JOIN ({META_SCAN[kind]}) m ON m.ticker = a.ticker
    WHERE a.n >= 30
),
e AS (
    SELECT j.*, abs(c1 - o1) AS b1, abs(c2 - o2) AS b2, abs(c3 - o3) AS b3,
           h1 - l1 AS r1, (o2 + c2) / 2.0 AS mid2, (o3 + c3) / 2.0 AS mid3,
           (n >= 6 AND v_m2 < v_m6) AS down_ctx,
           (n >= 6 AND v_m2 > v_m6) AS up_ctx
    FROM j
)
SELECT id, ticker, name, "group", sub_group, close AS latest, rsi,
       ARRAY_REMOVE(ARRAY[
         CASE WHEN n >= 2 AND c2 < o2 AND c1 > o1 AND o1 <= c2 AND c1 >= o2
                   AND b1 > b2                                   THEN 'bull_engulf' END,
         CASE WHEN n >= 2 AND c2 > o2 AND c1 < o1 AND o1 >= c2 AND c1 <= o2
                   AND b1 > b2                                   THEN 'bear_engulf' END,
         CASE WHEN n >= 2 AND c2 < o2 AND c1 > o1 AND o1 < c2
                   AND c1 > mid2 AND c1 < o2                     THEN 'piercing' END,
         CASE WHEN n >= 2 AND c2 > o2 AND c1 < o1 AND o1 > c2
                   AND c1 < mid2 AND c1 > o2                     THEN 'dark_cloud' END,
         CASE WHEN r1 > 0 AND b1 <= 0.35 * r1
                   AND (LEAST(o1, c1) - l1) >= 2 * b1
                   AND (h1 - GREATEST(o1, c1)) <= b1 AND down_ctx THEN 'hammer' END,
         CASE WHEN r1 > 0 AND b1 <= 0.35 * r1
                   AND (h1 - GREATEST(o1, c1)) >= 2 * b1
                   AND (LEAST(o1, c1) - l1) <= b1 AND up_ctx      THEN 'shooting_star' END,
         CASE WHEN r1 > 0 AND b1 <= 0.1 * r1                      THEN 'doji' END,
         CASE WHEN n >= 3 AND c3 < o3 AND b2 <= 0.5 * b3 AND c1 > o1
                   AND c1 > mid3 AND GREATEST(o2, c2) <= c3       THEN 'morning_star' END,
         CASE WHEN n >= 3 AND c3 > o3 AND b2 <= 0.5 * b3 AND c1 < o1
                   AND c1 < mid3 AND LEAST(o2, c2) >= c3          THEN 'evening_star' END,
         CASE WHEN n >= 3 AND c1 > o1 AND c2 > o2 AND c3 > o3
                   AND c1 > c2 AND c2 > c3 AND o1 > o2 AND o2 > o3 THEN 'three_white' END,
         CASE WHEN n >= 3 AND c1 < o1 AND c2 < o2 AND c3 < o3
                   AND c1 < c2 AND c2 < c3 AND o1 < o2 AND o2 < o3 THEN 'three_black' END,
         CASE WHEN rsi IS NOT NULL AND rsi < 30                    THEN 'rsi_oversold' END,
         CASE WHEN rsi IS NOT NULL AND rsi > 70                    THEN 'rsi_overbought' END,
         CASE WHEN macd IS NOT NULL AND macd_sig IS NOT NULL
                   AND macd > macd_sig                             THEN 'macd_bull' END,
         CASE WHEN macd IS NOT NULL AND macd_sig IS NOT NULL
                   AND NOT (macd > macd_sig)                       THEN 'macd_bear' END,
         CASE WHEN {_truthy('sma200')} AND close > sma200          THEN 'above_sma200' END,
         CASE WHEN {_truthy('sma200')} AND NOT (close > sma200)    THEN 'below_sma200' END,
         CASE WHEN bu IS NOT NULL AND bl IS NOT NULL AND (bu - bl) > 0
                   AND close <= bl + 0.05 * (bu - bl)              THEN 'boll_lower' END,
         CASE WHEN bu IS NOT NULL AND bl IS NOT NULL AND (bu - bl) > 0
                   AND close >= bu - 0.05 * (bu - bl)              THEN 'boll_upper' END
       ], NULL) AS matches
FROM e
"""


def score(kind, weights):
    w = weights
    return f"""
CREATE MATERIALIZED VIEW mv_score_{kind} AS
WITH RECURSIVE
base AS (
    SELECT ticker, i, n, v, sma50, sma200, rsi14, macd_hist,
           LAG(v) OVER (PARTITION BY ticker ORDER BY i) AS pv,
           MAX(v) OVER (PARTITION BY ticker ORDER BY i
                        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS peak_before
    FROM mv_ind_{kind}
),
-- db._daily_returns skips a step when the PREVIOUS close is not > 0
rets AS (
    SELECT ticker, ret, ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY i) AS k
    FROM (SELECT ticker, i, v / pv - 1 AS ret FROM base WHERE i > 1 AND pv > 0) z
),
rcnt AS (SELECT ticker, COUNT(*) AS k_n FROM rets GROUP BY ticker),
{_neumaier('macc', 'rets', 'ret', 'k')},
rmean AS (
    SELECT m.ticker, (m.s + m.c) / c.k_n AS mean, c.k_n
    FROM macc m JOIN rcnt c ON c.ticker = m.ticker AND m.k = c.k_n
),
dev AS (
    SELECT r.ticker, r.k, (r.ret - m.mean) * (r.ret - m.mean) AS d
    FROM rets r JOIN rmean m ON m.ticker = r.ticker
),
{_neumaier('dacc', 'dev', 'd', 'k')},
sd AS (
    SELECT d.ticker,
           CASE WHEN m.k_n >= 2 THEN power((d.s + d.c) / (m.k_n - 1), 0.5) END AS sd
    FROM dacc d JOIN rmean m ON m.ticker = d.ticker AND d.k = m.k_n
),
-- db._max_drawdown: note the `elif` — a bar that sets a NEW peak contributes
-- no drawdown on that iteration, it only moves the peak
dd AS (
    SELECT ticker,
           GREATEST(0.0, COALESCE(MAX(CASE
               WHEN COALESCE(peak_before, v) > 0 AND NOT (v > COALESCE(peak_before, v))
               THEN 1 - v / COALESCE(peak_before, v) END), 0.0)) AS mdd
    FROM base GROUP BY ticker
),
agg AS (
    SELECT b.ticker, b.n,
           MAX(v)         FILTER (WHERE i = n)       AS close,
           MAX(sma50)     FILTER (WHERE i = n)       AS sma50,
           MAX(sma200)    FILTER (WHERE i = n)       AS sma200,
           MAX(sma50)     FILTER (WHERE i = n - 20)  AS sma50_prev,
           MAX(rsi14)     FILTER (WHERE i = n)       AS rsi,
           MAX(macd_hist) FILTER (WHERE i = n)       AS macd_hist,
           MAX(v)         FILTER (WHERE i = n - 60)  AS v61,
           MAX(v)         FILTER (WHERE i = n - 120) AS v121,
           MAX(v) FILTER (WHERE i >= GREATEST(1, n - 239)) AS hi240,
           MIN(v) FILTER (WHERE i >= GREATEST(1, n - 239)) AS lo240
    FROM base b GROUP BY b.ticker, b.n
),
j AS (
    SELECT a.*, mt.id, mt.name, mt.grp AS "group", mt.sub AS sub_group,
           s.sd * power(252, 0.5) AS vol, dd.mdd
    FROM agg a
    JOIN ({META_SCAN[kind]}) mt ON mt.ticker = a.ticker
    LEFT JOIN sd s ON s.ticker = a.ticker
    LEFT JOIN dd   ON dd.ticker = a.ticker
    WHERE a.n >= 30
),
buckets AS (
    SELECT j.*,
           CASE WHEN sma50 IS NOT NULL OR sma200 IS NOT NULL THEN
             LEAST(100.0, GREATEST(0.0,
               50.0
               + CASE WHEN sma200 IS NULL THEN 0 WHEN close > sma200 THEN 20 ELSE -20 END
               + CASE WHEN sma50  IS NULL THEN 0 WHEN close > sma50  THEN 12 ELSE -12 END
               + CASE WHEN sma50 IS NULL OR sma200 IS NULL THEN 0
                      WHEN sma50 > sma200 THEN 10 ELSE -10 END
               + CASE WHEN sma50 IS NULL OR sma50_prev IS NULL THEN 0
                      WHEN sma50 > sma50_prev THEN 8 ELSE -8 END))
           END AS trend,
           CASE WHEN n > 61  AND v61  > 0 THEN close / v61  - 1 END AS r3,
           CASE WHEN n > 121 AND v121 > 0 THEN close / v121 - 1 END AS r6,
           CASE WHEN n >= 60 THEN
             CASE WHEN hi240 > lo240 THEN (close - lo240) / (hi240 - lo240) ELSE 0.5 END
           END AS range_pos,
           CASE WHEN rsi IS NULL THEN NULL
                WHEN rsi >= 80 THEN 22 WHEN rsi >= 70 THEN 42
                WHEN rsi >= 55 THEN 80 WHEN rsi >= 45 THEN 66
                WHEN rsi >= 35 THEN 50 WHEN rsi >= 25 THEN 44
                ELSE 40 END::float8 AS rsi_score,
           (SELECT CASE WHEN COUNT(*) = 0 THEN NULL ELSE SUM(x) / COUNT(*) END
            FROM (VALUES
              (CASE WHEN vol IS NULL THEN NULL
                    WHEN vol <= 0.30 THEN 90 WHEN vol <= 0.45 THEN 72
                    WHEN vol <= 0.60 THEN 55 WHEN vol <= 0.80 THEN 40
                    WHEN vol <= 1.10 THEN 28 ELSE 18 END::float8),
              (CASE WHEN mdd IS NULL THEN NULL
                    WHEN mdd <= 0.15 THEN 90 WHEN mdd <= 0.30 THEN 72
                    WHEN mdd <= 0.45 THEN 55 WHEN mdd <= 0.60 THEN 40
                    WHEN mdd <= 0.75 THEN 28 ELSE 18 END::float8)
            ) t(x) WHERE x IS NOT NULL) AS risk
    FROM j
),
mo AS (
    SELECT b.*,
           CASE WHEN r3 IS NOT NULL OR r6 IS NOT NULL THEN
             LEAST(100.0, GREATEST(0.0,
               50.0
               + CASE WHEN r3 IS NULL THEN 0
                      ELSE LEAST(25.0, GREATEST(-25.0, r3 * 150)) END
               + CASE WHEN r6 IS NULL THEN 0
                      ELSE LEAST(15.0, GREATEST(-15.0, r6 * 60)) END
               + CASE WHEN macd_hist IS NULL THEN 0
                      WHEN macd_hist > 0 THEN 8 ELSE -8 END))
           END AS momentum,
           CASE WHEN range_pos IS NULL THEN NULL
                WHEN range_pos >= 0.9
                THEN LEAST(100.0, GREATEST(0.0,
                       LEAST(100.0, GREATEST(0.0, 30 + range_pos * 60)) - 8))
                ELSE LEAST(100.0, GREATEST(0.0, 30 + range_pos * 60)) END AS rng
    FROM buckets b
),
-- composite: python accumulates num/den in SCORE_WEIGHTS insertion order
-- (trend, momentum, rsi, range, risk); `fundamental` has weight 0 and is skipped
comp AS (
    SELECT mo.*,
           ((((CASE WHEN trend IS NULL THEN 0.0 ELSE 0.0 + trend * {w['trend']} END)
              + CASE WHEN momentum IS NULL THEN 0.0 ELSE momentum * {w['momentum']} END)
              + CASE WHEN rsi_score IS NULL THEN 0.0 ELSE rsi_score * {w['rsi']} END)
              + CASE WHEN rng IS NULL THEN 0.0 ELSE rng * {w['range']} END)
              + CASE WHEN risk IS NULL THEN 0.0 ELSE risk * {w['risk']} END AS num,
           ((((CASE WHEN trend IS NULL THEN 0.0 ELSE 0.0 + {w['trend']} END)
              + CASE WHEN momentum IS NULL THEN 0.0 ELSE {w['momentum']} END)
              + CASE WHEN rsi_score IS NULL THEN 0.0 ELSE {w['rsi']} END)
              + CASE WHEN rng IS NULL THEN 0.0 ELSE {w['range']} END)
              + CASE WHEN risk IS NULL THEN 0.0 ELSE {w['risk']} END AS den
    FROM mo
)
SELECT id, ticker, name, "group", sub_group, close AS latest,
       CASE WHEN den <> 0 THEN num / den ELSE 50.0 END AS composite,
       trend, momentum, risk, rsi, range_pos
FROM comp
"""


# --------------------------------------------------------------------------
# the catalogue, in dependency order
# --------------------------------------------------------------------------
def all_views(periods, calc_periods, perf_periods, weights):
    """[(view_name, create_ddl, unique_index_columns)] in REFRESH order.

    Dependencies: mv_bars -> mv_ind -> the three scans; mv_bars (+ mv_alltime)
    -> the gainer / perf tables. Refreshing in list order therefore always sees
    freshly-rebuilt inputs.
    """
    out = []
    for kind in ("stock", "etf"):
        out.append((f"mv_cal_{kind}", cal(kind), "j_date"))
    for kind in ("stock", "etf"):
        out.append((f"mv_bars_{kind}", bars(kind), "ticker, rn"))
        out.append((f"mv_alltime_{kind}", alltime(kind), "ticker"))
    for kind in ("stock", "etf"):
        out.append((f"mv_ind_{kind}", kernel(kind), "ticker, i"))
    for kind in ("stock", "etf"):
        out.append((f"mv_market_gainer_{kind}",
                    gainer(kind, periods, "mv_market_gainer"), "ticker, id"))
        out.append((f"mv_period_gainer_{kind}",
                    gainer(kind, calc_periods, "mv_period_gainer"), "ticker, id"))
        out.append((f"mv_perf_prices_{kind}", perf(kind, perf_periods), "ticker"))
    for kind in ("stock", "etf"):
        out.append((f"mv_strategy_{kind}", strategy(kind), "ticker, id"))
        out.append((f"mv_filter_{kind}", filters(kind), "ticker, id"))
        out.append((f"mv_score_{kind}", score(kind, weights), "ticker, id"))
    return out
