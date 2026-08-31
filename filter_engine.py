"""
filter_engine.py — «طراحی فیلتر»: the node-graph filter designer's back end.

WHAT THIS IS

The platform already ships nineteen fixed technical filters (db.FILTERS) and
sixteen fixed strategies (db.STRATEGIES). Both are Python written by us, so a
user who wants «کندل پوشای صعودی، اما فقط وقتی حجم بالای میانگین ۲۰ روزه است»
has to ask for a code change. This module lets them *draw* that instead: a
directed graph of small typed nodes — price fields, indicators, arithmetic,
comparisons, crossings and boolean logic — that ends in one «خروجی فیلتر» node.

The graph is DATA, never code. It arrives as JSON, is validated against the
catalogue below, and is walked by a fixed interpreter. There is no eval(), no
import, and no way for a saved filter to do anything but read the price panel it
is handed.

HOW IT EVALUATES

Everything is a SERIES aligned to the symbol's bars, oldest→newest, with None
for "not computable yet" (the warm-up window of a moving average, a division by
zero, a missing bar). A comparison turns two number series into a boolean
series; the output node asks whether that boolean is true within the last
`within` bars. Scalars (the «عدد ثابت» node, and every numeric parameter) are
carried unexpanded and broadcast only when they meet a series, so a graph made
entirely of constants costs nothing per bar.

That single rule — one value kind, one length — is what keeps the interpreter
under 200 lines and makes every node composable with every other node. It is
also why `shift` is a parameter on the price and indicator nodes rather than a
node type: `close-1` in the reference product is one chip, and making the user
wire a separate shift node for it would triple the size of every real graph.

PERFORMANCE

One market-wide run is ~800 symbols × ~400 bars × the graph. The three things
that keep it in the low seconds:

  * the price panel is loaded once per (kind, as_of, bars) and cached in Redis,
    exactly like db._filter_scan_full's — a re-run while the user tweaks a
    threshold never touches PostgreSQL again;
  * `bars_needed()` walks the graph BEFORE the query and asks for only as many
    bars as the deepest lookback actually requires, bucketed so the cache is
    shared. A graph of `close > open` reads 120 bars, not 800;
  * results are memoised per node id inside one symbol, so an Ichimoku wired
    into four comparisons is computed once, not four times.
"""
import logging
import math
import os
import re
from collections import deque

import cache
import db

log = logging.getLogger(__name__)

#: Hard ceilings. A graph is user input arriving over HTTP; these are what stop
#: a hand-edited payload from turning one request into an hour of CPU.
MAX_NODES = 160
MAX_EDGES = 320
MAX_PERIOD = 500          # the largest `n` any window parameter may take
MAX_WITHIN = 60           # «در N کندل اخیر»
MAX_ROWS = 3000           # rows returned to the browser

#: Bar-count buckets. bars_needed() rounds up into one of these so that two
#: graphs with similar depth share one cached price panel.
#:
#: The last two exist only for «تایم فریم». A monthly SMA-۲۰ is twenty MONTHS,
#: which is ~۴۴۰ daily bars before its first valid value; without a bucket that
#: deep the series would still be warming up on the last candle and the filter
#: would quietly match nothing.
_BAR_BUCKETS = (150, 300, 500, 800, 1300)


# ---------------------------------------------------------------------------
# «تایم فریم» — the time frames a block may be computed on
#
# The whole platform stores DAILY bars, so weekly and monthly are built by
# resampling rather than by a second table. That is not a shortcut: on this
# exchange a weekly bar IS the aggregate of that week's sessions, and deriving
# it means it can never disagree with the daily data the rest of the app shows.
#
# The week starts on SATURDAY, because the Tehran exchange trades Saturday to
# Wednesday and an ISO (Monday) week would cut every trading week in half and
# put Saturday's session in with the PREVIOUS week's Sunday-to-Wednesday. The
# month is the JALALI month off `j_date`, not the Gregorian one — «تیر» is a
# month a user can reason about; «July» straddles two of them.
# ---------------------------------------------------------------------------
#: `short` is what the CHIP prints; `l` is what the inspector's dropdown says.
#: Daily prints nothing at all, because it is the default and a badge on every
#: box in the graph saying «روزانه» is a badge that means nothing.
TIMEFRAMES = [
    {"v": "D", "l": "روزانه", "short": ""},
    {"v": "W", "l": "هفتگی", "short": "هفتگی"},
    {"v": "M", "l": "ماهانه", "short": "ماهانه"},
]

#: Daily bars per frame bar. Used by bars_needed() to widen the read window: a
#: «دوره ۲۰» on the weekly frame is twenty weeks of history, not twenty days.
#: Five sessions a week on this exchange, ~۲۲ a Jalali month.
_TF_BARS = {"D": 1, "W": 5, "M": 22}

#: The bar column that says which frame bucket a daily bar belongs to.
_TF_KEY = {"W": "wk", "M": "mo"}

#: How each price column collapses when several daily bars become one frame bar.
_TF_AGG = {"o": "first", "h": "max", "l": "min", "c": "last", "f": "last",
           "v": "sum", "val": "sum", "cnt": "sum"}


# ---------------------------------------------------------------------------
# Series primitives
#
# Every one of these takes and returns a list the same length as the bars, with
# None where the value does not exist. Keeping that invariant absolute is what
# lets the interpreter zip any two values together without a length check.
# ---------------------------------------------------------------------------
def _shift(s, k):
    """`s` delayed by k bars: out[i] = s[i-k]. k is never negative — a filter
    that reads bars ahead of the one it is standing on would backtest
    beautifully and lose money, so the catalogue's `shift` minimum is 0."""
    if k <= 0:
        return list(s)
    if k >= len(s):
        return [None] * len(s)
    return [None] * k + list(s[:-k])


def _pair(a, b):
    """Iterate two series together, skipping any bar where either is None."""
    for x, y in zip(a, b):
        yield (x, y)


def _binary(a, b, fn):
    out = []
    for x, y in zip(a, b):
        if x is None or y is None:
            out.append(None)
            continue
        try:
            out.append(fn(x, y))
        except (ZeroDivisionError, ValueError, OverflowError):
            out.append(None)
    return out


def _unary(a, fn):
    out = []
    for x in a:
        if x is None:
            out.append(None)
            continue
        try:
            out.append(fn(x))
        except (ZeroDivisionError, ValueError, OverflowError):
            out.append(None)
    return out


def _roll(s, n, op):
    """Rolling window of width n, in ONE pass over the series.

    The obvious `for i: fn(s[i-n+1:i+1])` is O(n·w), and w is routinely 240 («سقف
    یک‌ساله») — that alone was five seconds of a market-wide run. MAX/MIN use a
    monotonic deque, the rest run on incremental sums, so every operator here is
    O(n) regardless of the window.

    A None anywhere in the window makes the output None, which is what the
    warm-up of an upstream indicator has to produce: the window restarts from the
    bar after it rather than silently averaging a shorter history."""
    m = len(s)
    out = [None] * m
    if n < 1:
        return out
    if op == "MEDIAN":                       # no incremental form worth the code
        for i in range(n - 1, m):
            w = s[i - n + 1:i + 1]
            if None not in w:
                out[i] = _median(w)
        return out
    if op == "RANGE":                        # two O(n) passes beat one O(n·w)
        hi, lo = _roll(s, n, "MAX"), _roll(s, n, "MIN")
        return [(a - b) if (a is not None and b is not None) else None
                for a, b in zip(hi, lo)]

    dq = deque()                             # indices, monotonic for MAX/MIN
    run = run2 = 0.0
    wl = 0                                   # left edge of the live window
    ismax, ismin = op == "MAX", op == "MIN"
    for i in range(m):
        x = s[i]
        if x is None:                        # the window cannot span a hole
            dq.clear()
            run = run2 = 0.0
            wl = i + 1
            continue
        if ismax or ismin:
            while dq and ((s[dq[-1]] <= x) if ismax else (s[dq[-1]] >= x)):
                dq.pop()
            dq.append(i)
            wl = max(wl, i - n + 1)
        else:
            run += x
            run2 += x * x
            while i - wl + 1 > n:
                o = s[wl]
                run -= o
                run2 -= o * o
                wl += 1
        if dq and dq[0] < wl:
            dq.popleft()
        if i - wl + 1 < n:
            continue
        if ismax or ismin:
            out[i] = s[dq[0]]
        elif op == "RANGE":
            out[i] = None                      # filled by the second pass below
        elif op == "SUM":
            out[i] = run
        elif op == "AVG":
            out[i] = run / n
        elif op in ("STDEV", "VAR"):
            mean = run / n
            var = run2 / n - mean * mean
            if var <= 0:
                # Catastrophic cancellation, not a negative variance: a window of
                # identical prices makes run2/n and mean² equal to within a float
                # ulp, and sqrt() of the −1e−9 that comes out raises. A flat
                # window HAS zero spread, so that is the answer.
                out[i] = 0.0
            else:
                out[i] = var if op == "VAR" else math.sqrt(var)
    return out


def _stdev(w):
    m = sum(w) / len(w)
    var = sum((x - m) ** 2 for x in w) / len(w)
    return math.sqrt(var) if var > 0 else 0.0


def _median(w):
    q = sorted(w)
    h = len(q) // 2
    return q[h] if len(q) % 2 else (q[h - 1] + q[h]) / 2.0


# ---------------------------------------------------------------------------
# Indicators that db.py only exposes as a LAST value
#
# db._atr_last / db._adx_last answer "what is it now", which is all the fixed
# scans ever needed. A designed filter can compare an indicator to its own value
# twenty bars ago, so these are the series forms. They are deliberately here and
# not in db.py: db's versions are load-bearing for the cached market scans and
# the materialised views, and a refactor of them to share code with these would
# put a new code path under the platform's hottest query for no user-visible
# gain.
# ---------------------------------------------------------------------------
def _true_range(H, L, C):
    tr = [None] * len(C)
    if C:
        tr[0] = H[0] - L[0]
    for i in range(1, len(C)):
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
    return tr


def _rma_series(s, n):
    """Wilder's smoothing — the running average behind ATR, ADX and RSI.

    Seeds from the first n values that actually exist rather than from s[:n]:
    the DX series this is applied to has holes wherever true range was zero (a
    symbol frozen at its limit price, which on this exchange is common), and a
    seed that averaged a None would raise on exactly those symbols."""
    out = [None] * len(s)
    seed = []
    acc = None
    for i, x in enumerate(s):
        if acc is None:
            if x is None:
                continue
            seed.append(x)
            if len(seed) == n:
                acc = sum(seed) / n
                out[i] = acc
            continue
        if x is not None:
            acc = (acc * (n - 1) + x) / n
        out[i] = acc                     # a hole holds the last value, not None
    return out


def _atr_series(H, L, C, n):
    """ATR at every bar.

    Bar 0 is dropped rather than seeded with its own high−low: db._atr_last does
    the same, and if the two disagree the ATR a designed filter compares against
    is not the ATR printed on the security page. The difference decays to ~1e-7
    after a few hundred bars, which is exactly the kind of discrepancy nobody
    finds and everybody argues about."""
    return [None] + _rma_series(_true_range(H, L, C)[1:], n)


def _adx_series(H, L, C, n):
    """(adx, +DI, −DI) at every bar, Wilder-smoothed."""
    m = len(C)
    none = [None] * m
    if m <= n + 1:
        return none, list(none), list(none)
    tr, pdm, ndm = [0.0] * m, [0.0] * m, [0.0] * m
    for i in range(1, m):
        up, dn = H[i] - H[i - 1], L[i - 1] - L[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        ndm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1]))
    trS, pS, nS = _rma_series(tr[1:], n), _rma_series(pdm[1:], n), _rma_series(ndm[1:], n)
    trS, pS, nS = [None] + trS, [None] + pS, [None] + nS
    pdi, ndi, dx = [None] * m, [None] * m, [None] * m
    for i in range(m):
        if trS[i]:
            pdi[i] = 100.0 * pS[i] / trS[i]
            ndi[i] = 100.0 * nS[i] / trS[i]
            tot = pdi[i] + ndi[i]
            dx[i] = (100.0 * abs(pdi[i] - ndi[i]) / tot) if tot else 0.0
    return _rma_series(dx, n), pdi, ndi


def _ichimoku(H, L, C, tenkan_n, kijun_n, span_n):
    """Tenkan / Kijun / Senkou A / Senkou B, with the two cloud lines DISPLACED
    FORWARD by kijun_n bars — i.e. spana[i] is the value plotted at bar i, built
    from the bar kijun_n ago. That is what «قیمت بالای ابر» has to compare
    against; the undisplaced value would put the cloud in the wrong place by a
    month and quietly change every Ichimoku filter's meaning.

    Chikou is intentionally absent: it is close displaced BACKWARD, so at the
    last bar it does not exist. The reference product builds that comparison out
    of a plain `close-۲۶` price node, and so can this one."""
    def mid(n):
        # (highest high + lowest low) / 2 over n bars, on the O(n) rolling
        # window rather than a slice per bar — the span-B line is 52 bars wide
        # and a per-bar max() over it was the slowest node in the catalogue.
        hh, ll = _roll(H, n, "MAX"), _roll(L, n, "MIN")
        return [((a + b) / 2.0) if (a is not None and b is not None) else None
                for a, b in zip(hh, ll)]

    tenkan, kijun = mid(tenkan_n), mid(kijun_n)
    span_a = [((t + k) / 2.0) if (t is not None and k is not None) else None
              for t, k in zip(tenkan, kijun)]
    span_b = mid(span_n)
    return tenkan, kijun, _shift(span_a, kijun_n), _shift(span_b, kijun_n)


def _psar_series(H, L, step, cap):
    """Wilder's Parabolic SAR. Returns the stop level at every bar."""
    m = len(H)
    out = [None] * m
    if m < 3:
        return out
    up = H[1] >= H[0]
    sar = L[0] if up else H[0]
    ep = H[0] if up else L[0]
    af = step
    for i in range(1, m):
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, L[i - 1], L[max(0, i - 2)])
            if L[i] < sar:                       # flip to a downtrend
                up, sar, ep, af = False, ep, L[i], step
            elif H[i] > ep:
                ep, af = H[i], min(cap, af + step)
        else:
            sar = max(sar, H[i - 1], H[max(0, i - 2)])
            if H[i] > sar:                       # flip to an uptrend
                up, sar, ep, af = True, ep, H[i], step
            elif L[i] < ep:
                ep, af = L[i], min(cap, af + step)
        out[i] = sar
    return out


def _supertrend(H, L, C, n, mult):
    """(line, direction) — direction is +1 while the trend is up, −1 down."""
    m = len(C)
    atr = _atr_series(H, L, C, n)
    line, direction = [None] * m, [None] * m
    up_prev = dn_prev = None
    trend = 1
    for i in range(m):
        if i == 0 or atr[i] is None:
            continue                      # C[i-1] at i=0 would wrap to the END
        mid = (H[i] + L[i]) / 2.0
        up, dn = mid - mult * atr[i], mid + mult * atr[i]
        if up_prev is not None:
            up = max(up, up_prev) if C[i - 1] > up_prev else up
            dn = min(dn, dn_prev) if C[i - 1] < dn_prev else dn
            trend = 1 if C[i] > dn_prev else (-1 if C[i] < up_prev else trend)
        line[i] = up if trend == 1 else dn
        direction[i] = float(trend)
        up_prev, dn_prev = up, dn
    return line, direction


def _obv_series(C, V):
    out = [None] * len(C)
    if not C:
        return out
    acc = 0.0
    out[0] = 0.0
    for i in range(1, len(C)):
        v = V[i] or 0.0
        acc += v if C[i] > C[i - 1] else (-v if C[i] < C[i - 1] else 0.0)
        out[i] = acc
    return out


def _mfi_series(H, L, C, V, n):
    m = len(C)
    out = [None] * m
    tp = [(H[i] + L[i] + C[i]) / 3.0 for i in range(m)]
    pos = [0.0] * m
    neg = [0.0] * m
    for i in range(1, m):
        flow = tp[i] * (V[i] or 0.0)
        if tp[i] > tp[i - 1]:
            pos[i] = flow
        elif tp[i] < tp[i - 1]:
            neg[i] = flow
    # Running sums, not a slice per bar: the slice form measured 3.8 s for one
    # MFI chip across the market, which is slower than the whole rest of a
    # typical graph put together.
    sp = sn = 0.0
    for i in range(1, m):
        sp += pos[i]
        sn += neg[i]
        if i > n:
            sp -= pos[i - n]
            sn -= neg[i - n]
        if i >= n:
            out[i] = 100.0 if sn == 0 else 100.0 - 100.0 / (1.0 + sp / sn)
    return out


# ---------------------------------------------------------------------------
# The second wave of indicators
#
# Everything below is a SERIES, aligned to the bars and padded with None through
# its warm-up, exactly like the block above. They are grouped the way the palette
# groups them — averages, oscillators, trend, channels, volume — so that adding
# one means finding the right paragraph rather than the right line.
#
# Nothing here reads حقیقی/حقوقی (individual vs institutional) flow, order-book
# depth, free float or market capitalisation, because this database does not
# have them: `stockpricehistory` is OHLC + final + volume + value + trade count,
# and that is the whole surface. A «قدرت خریدار حقیقی» block would be a chip
# that always evaluates to nothing, which is worse than its absence.
# ---------------------------------------------------------------------------

def _ema_of(s, n):
    """EMA over a series that may carry leading None — the indicator-of-an-
    indicator case (TRIX, StochRSI's smoothing) that db._ema_series, which
    assumes a clean price list, cannot take."""
    out = [None] * len(s)
    acc = None
    seed = []
    k = 2.0 / (n + 1)
    for i, x in enumerate(s):
        if x is None:
            continue
        if acc is None:
            seed.append(x)
            if len(seed) == n:
                acc = sum(seed) / n
                out[i] = acc
            continue
        acc = x * k + acc * (1 - k)
        out[i] = acc
    return out


def _hma_series(px, n):
    """Hull MA: WMA(2·WMA(n/2) − WMA(n), √n). db._hma_last computes only the
    final value; a designed filter compares it to itself ten bars ago."""
    half = db._wma_series(px, max(1, n // 2))
    full = db._wma_series(px, n)
    raw = [(2 * h - f) if (h is not None and f is not None) else None
           for h, f in zip(half, full)]
    sq = max(1, int(round(math.sqrt(n))))
    return _wma_of(raw, sq)


def _wma_of(s, n):
    """WMA over a series that may carry leading None."""
    out = [None] * len(s)
    denom = n * (n + 1) / 2.0
    for i in range(n - 1, len(s)):
        w = s[i - n + 1:i + 1]
        if any(x is None for x in w):
            continue
        out[i] = sum(w[j] * (j + 1) for j in range(n)) / denom
    return out


def _dema_series(px, n):
    e1 = db._ema_series(px, n)
    e2 = _ema_of(e1, n)
    return [(2 * a - b) if (a is not None and b is not None) else None
            for a, b in zip(e1, e2)]


def _tema_series(px, n):
    e1 = db._ema_series(px, n)
    e2 = _ema_of(e1, n)
    e3 = _ema_of(e2, n)
    return [(3 * a - 3 * b + c) if None not in (a, b, c) else None
            for a, b, c in zip(e1, e2, e3)]


def _vwma_series(px, vol, n):
    out = [None] * len(px)
    pv = vv = 0.0
    for i, (p, v) in enumerate(zip(px, vol)):
        v = v or 0.0
        pv += p * v
        vv += v
        if i >= n:
            ov = vol[i - n] or 0.0
            pv -= px[i - n] * ov
            vv -= ov
        if i >= n - 1:
            out[i] = (pv / vv) if vv else None
    return out


def _roc_series(px, n):
    prev = _shift(px, n)
    return _binary(px, prev, lambda x, y: (x - y) / y * 100.0 if y else None)


def _trix_series(px, n):
    e3 = _ema_of(_ema_of(db._ema_series(px, n), n), n)
    prev = _shift(e3, 1)
    return _binary(e3, prev, lambda x, y: (x - y) / y * 100.0 if y else None)


def _cmo_series(px, n):
    """Chande Momentum Oscillator: (up − down) / (up + down) × 100 over n bars."""
    m = len(px)
    out = [None] * m
    up = [0.0] * m
    dn = [0.0] * m
    for i in range(1, m):
        d = px[i] - px[i - 1]
        up[i] = d if d > 0 else 0.0
        dn[i] = -d if d < 0 else 0.0
    su = sd = 0.0
    for i in range(1, m):
        su += up[i]
        sd += dn[i]
        if i > n:
            su -= up[i - n]
            sd -= dn[i - n]
        if i >= n:
            tot = su + sd
            out[i] = ((su - sd) / tot * 100.0) if tot else 0.0
    return out


def _stochrsi_series(px, n, k_n, d_n):
    """Stochastic of RSI — %K and %D. Sharper than either alone, and the reason
    it needs `_roll` over a holed series: RSI is None through its own warm-up."""
    rsi = db._rsi_series(px, n)
    hi = _roll(rsi, n, "MAX")
    lo = _roll(rsi, n, "MIN")
    raw = [None] * len(px)
    for i in range(len(px)):
        if rsi[i] is None or hi[i] is None or lo[i] is None:
            continue
        raw[i] = 50.0 if hi[i] == lo[i] else (rsi[i] - lo[i]) / (hi[i] - lo[i]) * 100.0
    k = db._sma_of(raw, k_n)
    return k, db._sma_of(k, d_n)


def _uo_series(H, L, C):
    """Ultimate Oscillator at every bar. db._uo_last answers only for the last
    one; the three running sums here make the whole series O(n)."""
    m = len(C)
    out = [None] * m
    bp = [0.0] * m
    tr = [0.0] * m
    for i in range(1, m):
        bp[i] = C[i] - min(L[i], C[i - 1])
        tr[i] = max(H[i], C[i - 1]) - min(L[i], C[i - 1])
    sums = {p: [0.0, 0.0] for p in (7, 14, 28)}
    for i in range(1, m):
        for p, acc in sums.items():
            acc[0] += bp[i]
            acc[1] += tr[i]
            if i > p:
                acc[0] -= bp[i - p]
                acc[1] -= tr[i - p]
        if i >= 28:
            a = [(sums[p][0] / sums[p][1]) if sums[p][1] else 0.0 for p in (7, 14, 28)]
            out[i] = 100.0 * (4 * a[0] + 2 * a[1] + a[2]) / 7.0
    return out


def _aroon_series(H, L, n):
    """(up, down) — how recently the n-bar high and low happened, as 0..100."""
    m = len(H)
    up = [None] * m
    dn = [None] * m
    for i in range(n, m):
        w_h = H[i - n:i + 1]
        w_l = L[i - n:i + 1]
        up[i] = (n - (n - w_h.index(max(w_h)))) / n * 100.0
        dn[i] = (n - (n - w_l.index(min(w_l)))) / n * 100.0
    return up, dn


def _vortex_series(H, L, C, n):
    """(+VI, −VI). Crossings of the two are the signal."""
    m = len(C)
    pvm = [0.0] * m
    nvm = [0.0] * m
    tr = _true_range(H, L, C)
    for i in range(1, m):
        pvm[i] = abs(H[i] - L[i - 1])
        nvm[i] = abs(L[i] - H[i - 1])
    p = [None] * m
    q = [None] * m
    sp = sn = st = 0.0
    for i in range(1, m):
        sp += pvm[i]
        sn += nvm[i]
        st += tr[i] or 0.0
        if i > n:
            sp -= pvm[i - n]
            sn -= nvm[i - n]
            st -= tr[i - n] or 0.0
        if i >= n and st:
            p[i] = sp / st
            q[i] = sn / st
    return p, q


def _linreg_series(px, n):
    """(slope, endpoint value, R²) of the least-squares line over n bars.

    The slope is the honest answer to «روند صعودی است؟» — steeper than a moving
    average can express — and R² says how much of a trend it really is, which is
    what stops a sideways saw-tooth from reading as a rally.
    """
    m = len(px)
    slope = [None] * m
    value = [None] * m
    r2 = [None] * m
    if n < 2:
        return slope, value, r2
    sx = n * (n - 1) / 2.0                       # Σx for x = 0…n−1
    sxx = (n - 1) * n * (2 * n - 1) / 6.0        # Σx²
    denom = n * sxx - sx * sx
    if not denom:
        return slope, value, r2

    # Three running sums instead of four passes per window. Sliding the window
    # by one shifts every x down by one, which is exactly
    #     Sxy' = Sxy − (Sy − y_out) + (n−1)·y_in
    # and Syy carries the R² without a second walk. Measured: 4.1 s → 0.2 s for
    # one chip across the market, and this is a block people put on every graph.
    sy = sxy = syy = 0.0
    for i in range(m):
        y = px[i]
        if i >= n:
            out_y = px[i - n]
            sxy = sxy - (sy - out_y) + (n - 1) * y
            sy = sy - out_y + y
            syy = syy - out_y * out_y + y * y
        else:
            sxy += i * y
            sy += y
            syy += y * y
        if i < n - 1:
            continue
        bb = (n * sxy - sx * sy) / denom
        aa = (sy - bb * sx) / n
        slope[i] = bb
        value[i] = aa + bb * (n - 1)
        ss_tot = syy - sy * sy / n
        ss_res = syy - aa * sy - bb * sxy
        r2[i] = (1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else 1.0
    return slope, value, r2


def _keltner_series(H, L, C, src, n, mult):
    """EMA centre, ATR envelope. Reacts to true range where Bollinger reacts to
    closing-price dispersion — which is why the pair of them is the squeeze."""
    mid = db._ema_series(src, n)
    atr = _atr_series(H, L, C, n)
    up = [(m + mult * a) if (m is not None and a is not None) else None
          for m, a in zip(mid, atr)]
    lo = [(m - mult * a) if (m is not None and a is not None) else None
          for m, a in zip(mid, atr)]
    return up, mid, lo


def _donchian_series(H, L, n):
    up = _roll(H, n, "MAX")
    lo = _roll(L, n, "MIN")
    mid = [((a + b) / 2.0) if (a is not None and b is not None) else None
           for a, b in zip(up, lo)]
    return up, mid, lo


def _cmf_series(H, L, C, V, n):
    """Chaikin Money Flow — where in each bar's range it closed, weighted by
    volume, summed over n bars. Positive is accumulation."""
    m = len(C)
    mfv = [0.0] * m
    for i in range(m):
        rng = H[i] - L[i]
        mult = ((C[i] - L[i]) - (H[i] - C[i])) / rng if rng else 0.0
        mfv[i] = mult * (V[i] or 0.0)
    out = [None] * m
    sf = sv = 0.0
    for i in range(m):
        sf += mfv[i]
        sv += V[i] or 0.0
        if i >= n:
            sf -= mfv[i - n]
            sv -= V[i - n] or 0.0
        if i >= n - 1:
            out[i] = (sf / sv) if sv else 0.0
    return out


def _ad_series(H, L, C, V):
    """Accumulation / Distribution line — CMF's running, unbounded cousin."""
    out = [None] * len(C)
    acc = 0.0
    for i in range(len(C)):
        rng = H[i] - L[i]
        mult = ((C[i] - L[i]) - (H[i] - C[i])) / rng if rng else 0.0
        acc += mult * (V[i] or 0.0)
        out[i] = acc
    return out


def _force_series(C, V, n):
    """Elder's Force Index, EMA-smoothed: price change × volume."""
    raw = [None] * len(C)
    for i in range(1, len(C)):
        raw[i] = (C[i] - C[i - 1]) * (V[i] or 0.0)
    return _ema_of(raw, n) if n > 1 else raw


def _vwap_series(H, L, C, V, n):
    """Rolling VWAP over n bars on the typical price.

    Rolling, not session-anchored: this database has one row per DAY, so an
    intraday anchor has nothing to anchor to. «VWAP ۲۰ روزه» is the honest
    daily-bar reading of it.
    """
    m = len(C)
    out = [None] * m
    pv = vv = 0.0
    tp = [(H[i] + L[i] + C[i]) / 3.0 for i in range(m)]
    for i in range(m):
        v = V[i] or 0.0
        pv += tp[i] * v
        vv += v
        if i >= n:
            ov = V[i - n] or 0.0
            pv -= tp[i - n] * ov
            vv -= ov
        if i >= n - 1:
            out[i] = (pv / vv) if vv else None
    return out


def _heikin_series(O, H, L, C):
    """Heikin-Ashi candles. The smoothing is the point: HA turns a choppy series
    into runs of one colour, which is what makes «سه کندل سبز پیاپی» mean
    something on a market that gaps at the limit every other day."""
    m = len(C)
    ho = [None] * m
    hc = [None] * m
    hh = [None] * m
    hl = [None] * m
    for i in range(m):
        hc[i] = (O[i] + H[i] + L[i] + C[i]) / 4.0
        ho[i] = (O[i] + C[i]) / 2.0 if i == 0 else (ho[i - 1] + hc[i - 1]) / 2.0
        hh[i] = max(H[i], ho[i], hc[i])
        hl[i] = min(L[i], ho[i], hc[i])
    return ho, hh, hl, hc


def _pivot_series(H, L, C, mode):
    """Classic / Fibonacci floor pivots from the PREVIOUS bar, so the levels at
    bar i are the ones a trader would have had on the screen during bar i."""
    m = len(C)
    keys = ("pp", "r1", "r2", "r3", "s1", "s2", "s3")
    out = {k: [None] * m for k in keys}
    for i in range(1, m):
        h, l, c = H[i - 1], L[i - 1], C[i - 1]
        rng = h - l
        pp = (h + l + c) / 3.0
        if mode == "fib":
            r1, r2, r3 = pp + 0.382 * rng, pp + 0.618 * rng, pp + 1.0 * rng
            s1, s2, s3 = pp - 0.382 * rng, pp - 0.618 * rng, pp - 1.0 * rng
        else:
            r1, s1 = 2 * pp - l, 2 * pp - h
            r2, s2 = pp + rng, pp - rng
            r3, s3 = h + 2 * (pp - l), l - 2 * (h - pp)
        for k, v in zip(keys, (pp, r1, r2, r3, s1, s2, s3)):
            out[k][i] = v
    return out


# ---------------------------------------------------------------------------
# Candlestick patterns, as SERIES
#
# db._eval_filters() answers "does this pattern hold on the last bar", which is
# all the fixed /filters page ever needed. A designed filter asks a different
# question — «در N کندل اخیر» — so every pattern here is evaluated at every bar.
# The shapes are transcribed from that function deliberately rather than
# reinvented: a «کندل پوشای صعودی» must mean the same thing on both pages.
# ---------------------------------------------------------------------------
_CANDLE_PATTERNS = (
    ("bull_engulf",   "کندل پوشای صعودی"),
    ("bear_engulf",   "کندل پوشای نزولی"),
    ("hammer",        "چکش"),
    ("inv_hammer",    "چکش وارونه"),
    ("shooting_star", "ستارهٔ ثاقب"),
    ("hanging_man",   "مرد آویزان"),
    ("doji",          "دوجی"),
    ("spinning",      "فرفره"),
    ("marubozu_bull", "ماروبوزو صعودی"),
    ("marubozu_bear", "ماروبوزو نزولی"),
    ("piercing",      "پوشش نافذ"),
    ("dark_cloud",    "ابر سیاه پوشاننده"),
    ("harami_bull",   "هارامی صعودی"),
    ("harami_bear",   "هارامی نزولی"),
    ("morning_star",  "ستارهٔ صبحگاهی"),
    ("evening_star",  "ستارهٔ شامگاهی"),
    ("three_white",   "سه سرباز سفید"),
    ("three_black",   "سه کلاغ سیاه"),
    ("inside",        "کندل درونی (Inside Bar)"),
    ("outside",       "کندل بیرونی (Outside Bar)"),
    ("gap_up",        "شکاف صعودی"),
    ("gap_down",      "شکاف نزولی"),
    ("bull_bar",      "کندل سبز"),
    ("bear_bar",      "کندل قرمز"),
)


def _candle_series(O, H, L, C, F, pattern):
    m = len(C)
    out = [False] * m
    if m < 1:
        return out

    def body(i):
        return abs(C[i] - O[i])

    def rng(i):
        return H[i] - L[i]

    for i in range(m):
        two = i >= 1
        three = i >= 2
        # The trend the reversal patterns need. db._eval_filters reads it on the
        # settlement price four bars before the signal; same here, per bar.
        down_ctx = i >= 5 and F[i - 1] < F[i - 5]
        up_ctx = i >= 5 and F[i - 1] > F[i - 5]
        r0 = rng(i)
        b0 = body(i)
        hit = False

        if pattern == "bull_bar":
            hit = C[i] > O[i]
        elif pattern == "bear_bar":
            hit = C[i] < O[i]
        elif pattern == "doji":
            hit = r0 > 0 and b0 <= 0.1 * r0
        elif pattern == "spinning":
            if r0 > 0:
                up_sh = H[i] - max(O[i], C[i])
                lo_sh = min(O[i], C[i]) - L[i]
                hit = b0 <= 0.35 * r0 and up_sh >= b0 and lo_sh >= b0
        elif pattern == "marubozu_bull":
            hit = r0 > 0 and C[i] > O[i] and b0 >= 0.9 * r0
        elif pattern == "marubozu_bear":
            hit = r0 > 0 and C[i] < O[i] and b0 >= 0.9 * r0
        elif pattern in ("hammer", "inv_hammer", "shooting_star", "hanging_man"):
            if r0 > 0:
                up_sh = H[i] - max(O[i], C[i])
                lo_sh = min(O[i], C[i]) - L[i]
                small = b0 <= 0.35 * r0
                if pattern == "hammer":
                    hit = small and lo_sh >= 2 * b0 and up_sh <= b0 and down_ctx
                elif pattern == "hanging_man":
                    hit = small and lo_sh >= 2 * b0 and up_sh <= b0 and up_ctx
                elif pattern == "inv_hammer":
                    hit = small and up_sh >= 2 * b0 and lo_sh <= b0 and down_ctx
                else:
                    hit = small and up_sh >= 2 * b0 and lo_sh <= b0 and up_ctx
        elif two and pattern == "bull_engulf":
            hit = (C[i - 1] < O[i - 1] and C[i] > O[i]
                   and O[i] <= C[i - 1] and C[i] >= O[i - 1]
                   and body(i) > body(i - 1))
        elif two and pattern == "bear_engulf":
            hit = (C[i - 1] > O[i - 1] and C[i] < O[i]
                   and O[i] >= C[i - 1] and C[i] <= O[i - 1]
                   and body(i) > body(i - 1))
        elif two and pattern == "piercing":
            mid = (O[i - 1] + C[i - 1]) / 2.0
            hit = (C[i - 1] < O[i - 1] and C[i] > O[i] and O[i] < C[i - 1]
                   and C[i] > mid and C[i] < O[i - 1])
        elif two and pattern == "dark_cloud":
            mid = (O[i - 1] + C[i - 1]) / 2.0
            hit = (C[i - 1] > O[i - 1] and C[i] < O[i] and O[i] > C[i - 1]
                   and C[i] < mid and C[i] > O[i - 1])
        elif two and pattern == "harami_bull":
            hit = (C[i - 1] < O[i - 1] and C[i] > O[i]
                   and max(O[i], C[i]) <= O[i - 1] and min(O[i], C[i]) >= C[i - 1]
                   and body(i) < body(i - 1))
        elif two and pattern == "harami_bear":
            hit = (C[i - 1] > O[i - 1] and C[i] < O[i]
                   and max(O[i], C[i]) <= C[i - 1] and min(O[i], C[i]) >= O[i - 1]
                   and body(i) < body(i - 1))
        elif two and pattern == "inside":
            hit = H[i] <= H[i - 1] and L[i] >= L[i - 1]
        elif two and pattern == "outside":
            hit = H[i] > H[i - 1] and L[i] < L[i - 1]
        elif two and pattern == "gap_up":
            hit = L[i] > H[i - 1]
        elif two and pattern == "gap_down":
            hit = H[i] < L[i - 1]
        elif three and pattern == "morning_star":
            b1 = body(i - 2)
            mid1 = (O[i - 2] + C[i - 2]) / 2.0
            hit = (C[i - 2] < O[i - 2] and body(i - 1) <= 0.5 * b1 and C[i] > O[i]
                   and C[i] > mid1 and max(O[i - 1], C[i - 1]) <= C[i - 2])
        elif three and pattern == "evening_star":
            b1 = body(i - 2)
            mid1 = (O[i - 2] + C[i - 2]) / 2.0
            hit = (C[i - 2] > O[i - 2] and body(i - 1) <= 0.5 * b1 and C[i] < O[i]
                   and C[i] < mid1 and min(O[i - 1], C[i - 1]) >= C[i - 2])
        elif three and pattern == "three_white":
            hit = (C[i] > O[i] and C[i - 1] > O[i - 1] and C[i - 2] > O[i - 2]
                   and C[i] > C[i - 1] > C[i - 2] and O[i] > O[i - 1] > O[i - 2])
        elif three and pattern == "three_black":
            hit = (C[i] < O[i] and C[i - 1] < O[i - 1] and C[i - 2] < O[i - 2]
                   and C[i] < C[i - 1] < C[i - 2] and O[i] < O[i - 1] < O[i - 2])

        out[i] = bool(hit)
    return out


def _barssince_series(flags):
    """How many bars since the condition was last true (0 = this bar)."""
    out = [None] * len(flags)
    last = None
    for i, f in enumerate(flags):
        if f:
            last = i
        out[i] = float(i - last) if last is not None else None
    return out


def _streak_series(flags):
    """How many bars in a row, ending here, the condition has held."""
    out = [0.0] * len(flags)
    run = 0
    for i, f in enumerate(flags):
        run = run + 1 if f else 0
        out[i] = float(run)
    return out


def _percentile_series(s, n):
    """Where the current value sits inside its own n-bar range, 0..100.

    «موقعیت در بازه» — 100 is a fresh n-bar high, 0 a fresh low. It answers
    "near the top of the year" in one block instead of three.
    """
    hi = _roll(s, n, "MAX")
    lo = _roll(s, n, "MIN")
    out = [None] * len(s)
    for i in range(len(s)):
        if s[i] is None or hi[i] is None or lo[i] is None:
            continue
        span = hi[i] - lo[i]
        out[i] = 100.0 if span == 0 else (s[i] - lo[i]) / span * 100.0
    return out


# ---------------------------------------------------------------------------
# Resampling — «تایم فریم»
#
# Three functions and one rule: a frame is a RE-INDEXING of the daily bars, and
# every value crossing the boundary is translated by the same index map.
#
#   _frame_index   daily bar → frame bar number
#   _to_frame      a daily-aligned series → one value per frame bar
#   _from_frame    a frame-aligned series → back onto the daily bars
#
# _from_frame is a hold, not an interpolation: the weekly SMA is the same number
# on every session of that week, which is exactly what a weekly chart shows. It
# is also why the LAST frame bar is a partial one — the current week so far —
# and that is the honest answer for a screener run on a Tuesday, not a bug. A
# filter that must only see completed weeks says so with «برگشت به عقب ۱».
# ---------------------------------------------------------------------------
def _frame_index(keys):
    """(idx, count) where idx[i] is the frame bar daily bar i belongs to.

    `keys` is the bucket column (week number, Jalali month number). A new frame
    starts whenever it changes; gaps — a symbol suspended for three weeks — cost
    nothing, because frames are numbered by ARRIVAL, not by the bucket value.
    Numbering by the bucket value would leave empty frame bars in the middle of
    every halted symbol, and every moving average would then be averaging holes.
    """
    idx = [0] * len(keys)
    count = 0
    prev = None
    for i, k in enumerate(keys):
        if k != prev:
            count += 1
            prev = k
        idx[i] = count - 1
    return idx, count


def _resample_col(series, idx, count, how):
    """One daily column collapsed onto `count` frame bars."""
    acc = [None] * count
    for i, v in enumerate(series):
        g = idx[i]
        cur = acc[g]
        if cur is None or how == "last":
            acc[g] = v
        elif how == "max":
            if v > cur:
                acc[g] = v
        elif how == "min":
            if v < cur:
                acc[g] = v
        elif how == "sum":
            acc[g] = cur + v
        # "first" keeps whatever the first bar of the group already put there
    return [0.0 if v is None else v for v in acc]


class _FrameBars(dict):
    """The resampled panel, built ONE COLUMN AT A TIME on first access.

    A monthly RSI reads `final` and nothing else, but the loaded panel carries
    whatever every OTHER block in the graph asked for. Resampling all of them
    for all eight hundred symbols was ten seconds of a run that does two: the
    columns nobody reads cost exactly as much as the one that matters, and there
    is no way for _frame_ctx() to know which is which — but there is no need to,
    because `bars["f"]` says so at the moment it is asked.

    A dict subclass rather than a wrapper object so that every existing reader —
    `b["h"]`, `b.get("v")`, `ohlc()` — keeps working untouched."""

    __slots__ = ("_daily", "_idx", "_count")

    def __init__(self, daily, idx, count):
        super().__init__()
        self._daily = daily
        self._idx = idx
        self._count = count

    def __missing__(self, col):
        src = self._daily.get(col)
        if src is None:
            raise KeyError(col)
        val = _resample_col(src, self._idx, self._count, _TF_AGG.get(col, "last"))
        self[col] = val
        return val

    def get(self, col, default=None):
        # dict.get never calls __missing__, and «حجم» is read through .get on
        # purpose (it is absent for a graph that does not touch volume).
        try:
            return self[col]
        except KeyError:
            return default


def _to_frame(val, idx, count):
    """A daily-aligned value as seen on the frame: the LAST daily value of each
    frame bar. Constants and text do not have a time axis and pass through."""
    kind, payload = val
    if kind in ("const", "text"):
        return val
    acc = [None] * count
    for i, v in enumerate(payload):
        acc[idx[i]] = v
    return (kind, acc)


def _from_frame(val, idx):
    """A frame-aligned value back on the daily bars — held across the frame."""
    kind, payload = val
    if kind in ("const", "text"):
        return val
    return (kind, [payload[g] for g in idx])


def _shift_val(val, k):
    """«برگشت به عقب» applied to one output. A constant has no history to walk
    back into, so it is returned untouched rather than blanked."""
    kind, payload = val
    if kind in ("const", "text") or not k:
        return val
    return (kind, _shift(payload, k))


# ---------------------------------------------------------------------------
# Swing levels — «خط روند اصلی» / «سطح حمایت و مقاومت»
# ---------------------------------------------------------------------------
#: How many confirmed pivots on each side stay in memory. Six is enough to hold
#: the levels a chart reader would actually have drawn, and it is also the whole
#: cost control: the scan below runs once per bar per symbol, so a memory of six
#: is a few million comparisons for a market-wide run and a memory of sixty is
#: most of a minute.
_SWING_MEMORY = 6


def _swing_levels(H, L, C, k):
    """(resistance, support) — the nearest confirmed swing high above the close
    and the nearest confirmed swing low below it, at every bar.

    A swing high at bar i is a high that is the highest of the 2k+1 bars centred
    on it. It is therefore only KNOWN at bar i+k — you cannot tell a peak from a
    pause until k more bars have printed — and this walks the series honouring
    that delay. Reading it without the delay is the classic backtest that buys
    every top, so the levels here are always at least k bars old.
    """
    n = len(C)
    res = [None] * n
    sup = [None] * n
    highs, lows = [], []
    for t in range(n):
        i = t - k                                  # the bar that is confirmed now
        if i - k >= 0:
            hi, lo = H[i], L[i]
            if all(H[j] <= hi for j in range(i - k, i + k + 1)):
                highs.append(hi)
                del highs[:-_SWING_MEMORY]
            if all(L[j] >= lo for j in range(i - k, i + k + 1)):
                lows.append(lo)
                del lows[:-_SWING_MEMORY]
        px = C[t]
        above = [h for h in highs if h > px]
        below = [x for x in lows if x < px]
        res[t] = min(above) if above else None
        sup[t] = max(below) if below else None
    return res, sup


# ---------------------------------------------------------------------------
# «فرمول‌نویسی» — the formula block's expression language
#
# A recursive-descent parser over a fixed grammar, evaluated SERIES-WISE by the
# same combinators every other block uses. There is no eval(), no compile() and
# no name lookup that escapes the tables below — the parser can only ever build
# the five node shapes it knows, and the evaluator can only ever call a function
# out of _FN. A formula is data, exactly like the rest of a graph.
#
#   grammar   expr  := sum (('>'|'<'|'>='|'<='|'=='|'!=') sum)?
#             sum   := term (('+'|'-') term)*
#             term  := unary (('*'|'/'|'%') unary)*
#             unary := ('-'|'+') unary | power
#             power := atom ('^' unary)?          — right-associative
#             atom  := number | name '(' args ')' | name | '(' expr ')'
#
# A comparison yields 1 or 0 rather than a separate boolean kind. That is the
# whole reason it exists: `if(close>final, 1, 0)` is the shape people write, and
# without a comparison the `if` this language already had could never be given a
# condition. Non-chaining on purpose — `a<b<c` reads as a range and does not
# mean one in any C-family language, so it is a syntax error here rather than a
# silent `(a<b)<c`.
# ---------------------------------------------------------------------------
#: Everything a formula may name that is not one of the four wired inputs or a
#: price field. The price fields are the SAME names the «داده قیمت» block
#: offers, so a user who has learned `final` on one block has learned it on both.
_FORMULA_CONSTS = {"pi": math.pi, "e": math.e}


def _f_pow(x, y):
    # Guarded exactly like the «محاسبات» block's: 0**-1 raises and (-8)**0.5 is
    # a complex number, which would poison every comparison downstream.
    return (x ** y) if (x > 0 or float(y).is_integer()) else None


_FN = {
    "abs": (1, abs),
    "sqrt": (1, lambda x: math.sqrt(x) if x >= 0 else None),
    "ln": (1, lambda x: math.log(x) if x > 0 else None),
    "log": (1, lambda x: math.log10(x) if x > 0 else None),
    "log10": (1, lambda x: math.log10(x) if x > 0 else None),
    "exp": (1, lambda x: math.exp(x) if x < 700 else None),
    "round": (1, lambda x: float(round(x))),
    "floor": (1, lambda x: float(math.floor(x))),
    "ceil": (1, lambda x: float(math.ceil(x))),
    "sign": (1, lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)),
    "min": (2, min),
    "max": (2, max),
    "pow": (2, _f_pow),
    "if": (3, lambda c, x, y: x if c else y),
}

_FORMULA_OPS = {
    "+": lambda x, y: x + y,
    "-": lambda x, y: x - y,
    "*": lambda x, y: x * y,
    "/": lambda x, y: x / y if y else None,
    "%": lambda x, y: math.fmod(x, y) if y else None,
    "^": _f_pow,
    ">": lambda x, y: 1.0 if x > y else 0.0,
    "<": lambda x, y: 1.0 if x < y else 0.0,
    ">=": lambda x, y: 1.0 if x >= y else 0.0,
    "<=": lambda x, y: 1.0 if x <= y else 0.0,
    "==": lambda x, y: 1.0 if x == y else 0.0,
    "!=": lambda x, y: 1.0 if x != y else 0.0,
}

#: Comparisons, lowest precedence and non-associative.
_FORMULA_CMP = (">=", "<=", "==", "!=", ">", "<")

#: A hand-typed formula is user input arriving over HTTP; these are the ceilings
#: that stop `((((...))))` or a thousand-term sum from turning one request into
#: a stack overflow or a minute of CPU.
_FORMULA_MAX_LEN = 240
_FORMULA_MAX_NODES = 120

#: Two-character operators FIRST — `>=` must not tokenise as `>` then `=`.
_TOKEN = re.compile(
    r"\s*(?:(\d+\.?\d*|\.\d+)|([A-Za-z_][A-Za-z_0-9]*)"
    r"|(\*\*|>=|<=|==|!=|[-+*/%^(),<>]))")


def _tokenise(src):
    out, i = [], 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            if not src[i:].strip():
                break
            raise GraphError("در فرمول، نویسهٔ نامعتبر: «%s»" % src[i])
        i = m.end()
        num, name, op = m.groups()
        if num is not None:
            out.append(("num", float(num)))
        elif name is not None:
            out.append(("name", name.lower()))
        else:
            out.append(("op", "^" if op == "**" else op))
    return out


class _Parser:
    """One pass, no backtracking. Every production counts the nodes it builds so
    a pathological input is refused while it is being read rather than after."""

    def __init__(self, toks):
        self.t = toks
        self.i = 0
        self.count = 0

    def _peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def _eat(self, kind, val=None):
        k, v = self._peek()
        if k != kind or (val is not None and v != val):
            raise GraphError("فرمول ناقص است — «%s» انتظار می‌رفت." % (val or kind))
        self.i += 1
        return v

    def _node(self, node):
        self.count += 1
        if self.count > _FORMULA_MAX_NODES:
            raise GraphError("فرمول بیش از حد پیچیده است.")
        return node

    def parse(self):
        if not self.t:
            raise GraphError("فرمول خالی است.")
        node = self.expr()
        if self.i != len(self.t):
            raise GraphError("انتهای فرمول قابل خواندن نیست.")
        return node

    def expr(self):
        node = self.sum()
        if self._peek()[0] == "op" and self._peek()[1] in _FORMULA_CMP:
            op = self._eat("op")
            node = self._node(("bin", op, node, self.sum()))
        return node

    def sum(self):
        node = self.term()
        while self._peek()[0] == "op" and self._peek()[1] in ("+", "-"):
            op = self._eat("op")
            node = self._node(("bin", op, node, self.term()))
        return node

    def term(self):
        node = self.unary()
        while self._peek()[0] == "op" and self._peek()[1] in ("*", "/", "%"):
            op = self._eat("op")
            node = self._node(("bin", op, node, self.unary()))
        return node

    def unary(self):
        if self._peek()[0] == "op" and self._peek()[1] in ("-", "+"):
            op = self._eat("op")
            inner = self.unary()
            return inner if op == "+" else self._node(("bin", "-", ("num", 0.0), inner))
        return self.power()

    def power(self):
        base = self.atom()
        if self._peek() == ("op", "^"):
            self._eat("op")
            return self._node(("bin", "^", base, self.unary()))
        return base

    def atom(self):
        kind, val = self._peek()
        if kind == "num":
            self._eat("num")
            return self._node(("num", val))
        if kind == "op" and val == "(":
            self._eat("op")
            node = self.expr()
            self._eat("op", ")")
            return node
        if kind == "name":
            self._eat("name")
            if self._peek() == ("op", "("):
                self._eat("op")
                args = []
                if self._peek() != ("op", ")"):
                    args.append(self.expr())
                    while self._peek() == ("op", ","):
                        self._eat("op")
                        args.append(self.expr())
                self._eat("op", ")")
                spec = _FN.get(val)
                if spec is None:
                    raise GraphError("در فرمول، تابع ناشناخته: «%s»" % val)
                if len(args) != spec[0]:
                    raise GraphError("تابع «%s» باید %d ورودی بگیرد." % (val, spec[0]))
                return self._node(("call", val, args))
            return self._node(("var", val))
        raise GraphError("فرمول ناقص است.")


#: Parsing is pure and the same handful of formulas are re-parsed for every one
#: of eight hundred symbols, so the AST is cached by its source text.
_FORMULA_CACHE = {}


def compile_formula(src):
    """Source text → AST, raising GraphError with a Persian message on anything
    the language does not accept. Public because verify_designer.py tests it."""
    src = (src or "").strip()
    if not src:
        raise GraphError("فرمول خالی است.")
    if len(src) > _FORMULA_MAX_LEN:
        raise GraphError("فرمول بیش از %d نویسه است." % _FORMULA_MAX_LEN)
    hit = _FORMULA_CACHE.get(src)
    if hit is None:
        if len(_FORMULA_CACHE) > 256:
            _FORMULA_CACHE.clear()
        hit = _FORMULA_CACHE[src] = _Parser(_tokenise(src)).parse()
    return hit


def formula_names(node, out=None):
    """Every variable a compiled formula reads — what fields_needed() asks so
    that `close*volume` loads two columns and not eight."""
    out = set() if out is None else out
    if node[0] == "var":
        out.add(node[1])
    elif node[0] == "bin":
        formula_names(node[2], out)
        formula_names(node[3], out)
    elif node[0] == "call":
        for a in node[2]:
            formula_names(a, out)
    return out


def _eval_formula(node, env, n):
    """The AST as a series of length n. Every leaf becomes a series first, so
    one code path covers `a*2` and `2*3` and nothing has to ask which."""
    kind = node[0]
    if kind == "num":
        return [node[1]] * n
    if kind == "var":
        name = node[1]
        if name in _FORMULA_CONSTS:
            return [_FORMULA_CONSTS[name]] * n
        got = env(name)
        if got is None:
            raise GraphError("در فرمول، «%s» شناخته نشد یا وصل نیست." % name)
        return got
    if kind == "bin":
        return _binary(_eval_formula(node[2], env, n),
                       _eval_formula(node[3], env, n), _FORMULA_OPS[node[1]])
    arity, fn = _FN[node[1]]
    args = [_eval_formula(a, env, n) for a in node[2]]
    if arity == 1:
        return _unary(args[0], fn)
    out = [None] * n
    for i in range(n):
        vals = [a[i] for a in args]
        if any(v is None for v in vals):
            continue
        try:
            out[i] = fn(*vals)
        except (ZeroDivisionError, ValueError, OverflowError):
            out[i] = None
    return out


# ---------------------------------------------------------------------------
# The node catalogue
#
# This list IS the palette the browser draws, the validator the run endpoint
# applies, and the documentation. Adding a node means adding one entry here and
# one branch in _eval_node() — nothing in the front end, which builds its whole
# left-hand palette from /api/designer/catalog.
#
# `title` is the chip caption, rendered client-side:
#     {p}    → the parameter's value           ("close", "20")
#     {~p}   → "" when the value is 0, else "-<value>"   (close, close-1)
# ---------------------------------------------------------------------------
#: The palette's top level. `sub` on each node below is the second level — with
#: ~70 blocks a flat «اندیکاتور» shelf is a wall of thirty names, and the thing
#: a user is looking for («یک میانگین متحرک») is a shelf, not a search term.
CATEGORIES = [
    {"key": "price",     "label": "داده قیمت",   "color": "#7fd4ef"},
    {"key": "indicator", "label": "اندیکاتور",   "color": "#ffd85e"},
    {"key": "candle",    "label": "الگوی کندلی", "color": "#c9b6f0"},
    {"key": "math",      "label": "محاسبات",     "color": "#7fe3a0"},
    {"key": "stat",      "label": "آماری",       "color": "#ffc46b"},
    {"key": "compare",   "label": "مقایسه",      "color": "#f7dcac"},
    {"key": "cross",     "label": "تقاطع",       "color": "#ffe07a"},
    {"key": "logic",     "label": "منطق",        "color": "#f3bd86"},
    {"key": "symbol",    "label": "اطلاعات نماد", "color": "#a9e3dc"},
    # A paper tone, unlike every other swatch: «توضیحات» is the only block on
    # this shelf and it has to read as a note without breaking the rule that a
    # chip's colour is its shelf's colour.
    {"key": "general",   "label": "عمومی",       "color": "#e4dfd2"},
    {"key": "output",    "label": "خروجی",       "color": "#5fdccb"},
]

#: The price fields a «داده قیمت» node can read. The Persian half of each label
#: is the one that matters: on the Tehran exchange «پایانی» (adj_final) is the
#: settlement price every percentage on this platform is computed from, and
#: «آخرین معامله» (adj_close) is the last trade — mixing them up is the single
#: most common error in a TSE filter.
PRICE_FIELDS = [
    {"v": "close",  "l": "close — آخرین معامله"},
    {"v": "final",  "l": "final — پایانی"},
    {"v": "open",   "l": "open — بازگشایی"},
    {"v": "high",   "l": "high — بیشترین"},
    {"v": "low",    "l": "low — کمترین"},
    {"v": "volume", "l": "vol — حجم"},
    {"v": "value",  "l": "value — ارزش معاملات"},
    {"v": "count",  "l": "count — تعداد معاملات"},
    {"v": "hl2",    "l": "hl2 — میانگین سقف و کف"},
    {"v": "hlc3",   "l": "hlc3 — قیمت معمول"},
    {"v": "ohlc4",  "l": "ohlc4 — میانگین کندل"},
    {"v": "pct",    "l": "pct — درصد تغییر پایانی"},
    {"v": "range",  "l": "range — دامنهٔ کندل (سقف−کف)"},
    {"v": "body",   "l": "body — اندازهٔ بدنه"},
    {"v": "ushadow", "l": "ushadow — سایهٔ بالا"},
    {"v": "lshadow", "l": "lshadow — سایهٔ پایین"},
]

_SRC = {"id": "src", "label": "منبع", "type": "select", "default": "final",
        "options": PRICE_FIELDS}

#: «تایم فریم» and «برگشت به عقب» — the two properties the reference guide puts
#: on EVERY market-data and indicator block. They are defined once here and
#: attached to the whole catalogue in one loop below rather than copied into
#: forty entries, which is what stops the next block anyone adds from quietly
#: shipping without them.
_TF = {"id": "tf", "label": "تایم فریم", "type": "select", "default": "D",
       "options": TIMEFRAMES}

#: One shift, not two. The guide lists «برگشت به عقب» (move the indicator's own
#: line back) and «برگشت به عقب قیمت» (move the price back, then compute)
#: separately; for every indicator in this catalogue those two are the same
#: number, because each one is a translation-invariant function of the price
#: series — SMA(shift(px, k), n) and shift(SMA(px, n), k) are identical lists.
#: Offering both would be two dials that do one thing.
_SH = {"id": "shift", "label": "برگشت به عقب (کندل)", "type": "int",
       "default": 0, "min": 0, "max": MAX_PERIOD}

#: «روش میانگین متحرک» — the guide's four, in the guide's order.
_MA_METHODS = [{"v": "sma", "l": "ساده", "short": "ساده"},
               {"v": "ema", "l": "نمایی", "short": "نمایی"},
               {"v": "smma", "l": "هموار (وایلدر)", "short": "هموار"},
               {"v": "wma", "l": "وزن خطی", "short": "وزنی"}]

_MA_METHOD = {"id": "method", "label": "روش میانگین متحرک", "type": "select",
              "default": "sma", "options": _MA_METHODS}


def _ma_of(px, n, method):
    """The four «روش میانگین متحرک» behind one name.

    Every one of these is the None-TOLERANT variant. db._sma_series and
    db._ema_series assume a clean list of prices and raise on a hole, which is
    fine where they are called from — a price column — and wrong here: this is
    also the smoother inside «استوکاستیک», whose raw %K is None for every bar
    where the n-bar range had not formed yet."""
    if method == "ema":
        return _ema_of(px, n)
    if method == "wma":
        return _wma_of(px, n)
    if method == "smma":
        return _rma_series(px, n)
    return _roll(px, n, "AVG")


def _n(pid, label, default, lo=1, hi=MAX_PERIOD):
    return {"id": pid, "label": label, "type": "int",
            "default": default, "min": lo, "max": hi}


def _f(pid, label, default, lo, hi, step=0.1):
    return {"id": pid, "label": label, "type": "float",
            "default": default, "min": lo, "max": hi, "step": step}


def _out(*ids):
    return [{"id": i, "label": l, "kind": k} for i, l, k in ids]


NODE_TYPES = [
    # ---- داده قیمت -------------------------------------------------------
    {"type": "price", "cat": "price", "sub": "تابلوی قیمت", "label": "داده قیمت",
     "title": "{field}{~shift}",
     "help": "یک ستون از تابلوی قیمت. «شیفت» یعنی همان مقدار در N کندل قبل — "
             "مثلاً close با شیفت ۱ می‌شود close-1.",
     "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [{"id": "field", "label": "فیلد", "type": "select",
                 "default": "close", "options": PRICE_FIELDS},
                _n("shift", "شیفت (کندل قبل)", 0, 0, MAX_PERIOD)]},

    {"type": "const", "cat": "price", "sub": "تابلوی قیمت", "label": "عدد ثابت",
     "title": "{value}",
     "help": "یک عدد ثابت برای مقایسه یا محاسبه.",
     "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_f("value", "مقدار", 0.0, -1e12, 1e12, 1)]},

    {"type": "candlepart", "cat": "price", "sub": "کندل", "label": "جزء کندل",
     "title": "{part}",
     "help": "اندازهٔ بدنه، دامنه یا سایه‌های همین کندل، به‌صورت عدد. «نسبت بدنه» "
             "بدنه تقسیم بر دامنه است (۰ تا ۱) و برای تشخیص کندل‌های بی‌بدنه به کار می‌آید.",
     "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [{"id": "part", "label": "جزء", "type": "select", "default": "body",
                 "options": [{"v": "body", "l": "بدنه"}, {"v": "range", "l": "دامنه"},
                             {"v": "ushadow", "l": "سایهٔ بالا"},
                             {"v": "lshadow", "l": "سایهٔ پایین"},
                             {"v": "bodyratio", "l": "نسبت بدنه به دامنه"},
                             {"v": "pos", "l": "موقعیت بسته‌شدن در کندل ٪"}]},
                _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "heikin", "cat": "price", "sub": "کندل", "label": "هایکین آشی",
     "title": "HA", "inputs": [],
     "outputs": _out(("o", "باز", "num"), ("h", "سقف", "num"),
                     ("l", "کف", "num"), ("c", "بسته", "num")),
     "help": "کندل‌های هایکین‌آشی. نوسان را صاف می‌کند، پس «سه کندل سبز پیاپی» روی "
             "آن معنا دارد؛ روی کندل خام بازاری که هر روز صف می‌شود، ندارد.",
     "params": []},

    {"type": "pivot", "cat": "price", "sub": "سطوح", "label": "نقاط پیوت",
     "title": "Pivot {mode}", "inputs": [],
     "outputs": _out(("pp", "PP", "num"), ("r1", "R1", "num"), ("r2", "R2", "num"),
                     ("r3", "R3", "num"), ("s1", "S1", "num"), ("s2", "S2", "num"),
                     ("s3", "S3", "num")),
     "help": "سطوح حمایت و مقاومت کلاسیک، از کندل قبل — یعنی همان اعدادی که در آن "
             "روز جلوی چشم معامله‌گر بوده است.",
     "params": [{"id": "mode", "label": "روش", "type": "select", "default": "classic",
                 "options": [{"v": "classic", "l": "کلاسیک"}, {"v": "fib", "l": "فیبوناچی"}]}]},

    # ---- اندیکاتورها ------------------------------------------------------
    {"type": "sma", "cat": "indicator", "sub": "میانگین‌ها", "label": "میانگین متحرک",
     "title": "MA {method} {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "میانگین متحرک N دوره روی منبع انتخاب‌شده، با چهار روش: ساده، نمایی، "
             "هموار (وایلدر) و وزن خطی. برای روش‌های دیگر — هال، دوگانه، سه‌گانه و "
             "وزنی حجمی — جعبه‌های جداگانه در همین قفسه هست.",
     "params": [_n("n", "دوره", 20), _MA_METHOD, _SRC]},

    {"type": "ema", "cat": "indicator", "sub": "میانگین‌ها", "label": "میانگین متحرک نمایی",
     "title": "EMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "میانگین متحرک نمایی N دوره.",
     "params": [_n("n", "دوره", 20), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "wma", "cat": "indicator", "sub": "میانگین‌ها", "label": "میانگین متحرک وزنی",
     "title": "WMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "میانگین متحرک وزنی خطی N دوره.",
     "params": [_n("n", "دوره", 20, 1, 200), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "rsi", "cat": "indicator", "sub": "نوسان‌نما", "label": "RSI",
     "title": "RSI {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "شاخص قدرت نسبی (وایلدر). زیر ۳۰ اشباع فروش، بالای ۷۰ اشباع خرید.",
     "params": [_n("n", "دوره", 14, 2, 200), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "macd", "cat": "indicator", "sub": "روند", "label": "MACD",
     "title": "MACD {fast},{slow},{sig} {src}", "inputs": [],
     "outputs": _out(("macd", "MACD", "num"), ("signal", "سیگنال", "num"),
                     ("hist", "هیستوگرام", "num")),
     "help": "همگرایی/واگرایی میانگین‌های متحرک؛ سه خروجی: خط مکدی، خط سیگنال و اختلافشان.",
     "params": [_n("fast", "تند", 12, 1, 200), _n("slow", "کند", 26, 1, 300),
                _n("sig", "سیگنال", 9, 1, 100), _SRC]},

    {"type": "boll", "cat": "indicator", "sub": "کانال و نوسان", "label": "باند بولینگر",
     "title": "Band {method} {n} {src}", "inputs": [],
     "outputs": _out(("upper", "بالا", "num"), ("mid", "میانه", "num"),
                     ("lower", "پایین", "num"), ("width", "پهنا ٪", "num")),
     "help": "باند بولینگر N دوره. «انحراف بالا» و «انحراف پایین» جدا هستند، پس باند "
             "نامتقارن هم می‌شود ساخت. «پهنا» فاصلهٔ دو باند نسبت به میانه بر حسب درصد است.",
     "params": [_n("n", "دوره", 20, 2, 300),
                _f("k", "انحراف بالا", 2.0, 0.1, 10, 0.1),
                _f("kd", "انحراف پایین", 2.0, 0.1, 10, 0.1),
                _MA_METHOD, _SRC]},

    {"type": "stoch", "cat": "indicator", "sub": "نوسان‌نما", "label": "استوکاستیک",
     "title": "Stoch {n},{ks},{ds}", "inputs": [],
     "outputs": _out(("k", "%K", "num"), ("d", "%D", "num")),
     "help": "استوکاستیک آهسته: %K و %D. «هموارسازی K» همان Slowing است.",
     "params": [_n("n", "دورهٔ K", 14, 2, 300), _n("ks", "هموارسازی K (Slowing)", 3, 1, 50),
                _n("ds", "دورهٔ D", 3, 1, 50), _MA_METHOD]},

    {"type": "atr", "cat": "indicator", "sub": "کانال و نوسان", "label": "ATR",
     "title": "ATR {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "میانگین دامنهٔ واقعی — سنجهٔ نوسان.",
     "params": [_n("n", "دوره", 14, 1, 300)]},

    {"type": "adx", "cat": "indicator", "sub": "روند", "label": "ADX",
     "title": "ADX {n}", "inputs": [],
     "outputs": _out(("adx", "ADX", "num"), ("pdi", "+DI", "num"), ("ndi", "−DI", "num")),
     "help": "قدرت روند و جهت آن. ADX بالای ۲۵ یعنی روند قوی.",
     "params": [_n("n", "دوره", 14, 2, 200)]},

    {"type": "cci", "cat": "indicator", "sub": "نوسان‌نما", "label": "CCI",
     "title": "CCI {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_n("n", "دوره", 20, 2, 300)]},

    {"type": "willr", "cat": "indicator", "sub": "نوسان‌نما", "label": "Williams %R",
     "title": "%R {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_n("n", "دوره", 14, 2, 300)]},

    {"type": "mfi", "cat": "indicator", "sub": "نوسان‌نما", "label": "MFI",
     "title": "MFI {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "شاخص جریان نقدینگی — RSI وزن‌دار با حجم.",
     "params": [_n("n", "دوره", 14, 2, 300)]},

    {"type": "obv", "cat": "indicator", "sub": "حجم", "label": "OBV",
     "title": "OBV", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "حجم متوازن — جمع تجمعی حجم با علامت جهت «منبع».",
     "params": [_SRC]},

    {"type": "ao", "cat": "indicator", "sub": "نوسان‌نما", "label": "Awesome Oscillator",
     "title": "AO", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": []},

    {"type": "ichimoku", "cat": "indicator", "sub": "روند", "label": "ایچیموکو",
     "title": "Ichi {tenkan},{kijun},{spanb}", "inputs": [],
     "outputs": _out(("tenkan", "تنکان", "num"), ("kijun", "کیجون", "num"),
                     ("spana", "سنکو A", "num"), ("spanb", "سنکو B", "num")),
     "help": "ابر ایچیموکو. دو خط ابر به اندازهٔ کیجون به جلو منتقل شده‌اند، پس "
             "مقایسهٔ قیمت با آن‌ها همان چیزی است که روی نمودار دیده می‌شود. "
             "برای «چیکو» از نود «داده قیمت» با شیفت ۲۶ استفاده کنید.",
     "params": [_n("tenkan", "تنکان", 9, 1, 200), _n("kijun", "کیجون", 26, 1, 300),
                _n("spanb", "سنکو B", 52, 1, 400)]},

    {"type": "psar", "cat": "indicator", "sub": "روند", "label": "Parabolic SAR",
     "title": "SAR {step},{cap}", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_f("step", "گام", 0.02, 0.001, 0.5, 0.001),
                _f("cap", "بیشینه", 0.2, 0.01, 1.0, 0.01)]},

    {"type": "supertrend", "cat": "indicator", "sub": "روند", "label": "سوپرترند",
     "title": "ST {n},{mult}", "inputs": [],
     "outputs": _out(("line", "خط", "num"), ("dir", "جهت (۱±)", "num")),
     "help": "خروجی «جهت» برابر ۱ در روند صعودی و ۱− در نزولی است.",
     "params": [_n("n", "دورهٔ ATR", 10, 1, 200), _f("mult", "ضریب", 3.0, 0.1, 20, 0.1)]},

    {"type": "stdev", "cat": "indicator", "sub": "کانال و نوسان", "label": "انحراف معیار",
     "title": "STDEV {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_n("n", "دوره", 20, 2, 300), _SRC]},

    {"type": "hma", "cat": "indicator", "sub": "میانگین‌ها", "label": "میانگین متحرک هال",
     "title": "HMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "سریع‌تر از EMA با تأخیر کمتر؛ برای تشخیص زودهنگام تغییر روند.",
     "params": [_n("n", "دوره", 20, 2, 200), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "dema", "cat": "indicator", "sub": "میانگین‌ها", "label": "DEMA",
     "title": "DEMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_n("n", "دوره", 20, 2, 300), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "tema", "cat": "indicator", "sub": "میانگین‌ها", "label": "TEMA",
     "title": "TEMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": [_n("n", "دوره", 20, 2, 300), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "smma", "cat": "indicator", "sub": "میانگین‌ها", "label": "میانگین هموار وایلدر",
     "title": "SMMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "همان هموارسازی‌ای که در دل RSI و ATR است — کندتر و بی‌نوسان‌تر از EMA.",
     "params": [_n("n", "دوره", 14, 2, 300), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "vwma", "cat": "indicator", "sub": "میانگین‌ها", "label": "میانگین وزنی حجمی",
     "title": "VWMA {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "میانگینی که کندل‌های پرحجم را سنگین‌تر می‌شمارد.",
     "params": [_n("n", "دوره", 20, 2, 300), _SRC, _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    {"type": "stochrsi", "cat": "indicator", "sub": "نوسان‌نما", "label": "استوکاستیک RSI",
     "title": "StochRSI {n}", "inputs": [],
     "outputs": _out(("k", "%K", "num"), ("d", "%D", "num")),
     "help": "استوکاستیک روی RSI — تیزتر از هر دو، و برای گرفتن کف و سقف‌های کوتاه‌مدت.",
     "params": [_n("n", "دوره", 14, 2, 200), _n("ks", "هموارسازی K", 3, 1, 50),
                _n("ds", "هموارسازی D", 3, 1, 50), _SRC]},

    {"type": "roc", "cat": "indicator", "sub": "نوسان‌نما", "label": "نرخ تغییر (ROC)",
     "title": "ROC {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "درصد تغییر نسبت به N کندل قبل.",
     "params": [_n("n", "دوره", 12, 1, MAX_PERIOD), _SRC]},

    {"type": "momentum", "cat": "indicator", "sub": "نوسان‌نما", "label": "مومنتوم",
     "title": "MOM {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "اختلاف ساده با N کندل قبل (نه درصد).",
     "params": [_n("n", "دوره", 10, 1, MAX_PERIOD), _SRC]},

    {"type": "trix", "cat": "indicator", "sub": "نوسان‌نما", "label": "TRIX",
     "title": "TRIX {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "درصد تغییر میانگین نمایی سه‌بار هموارشده — نوسان‌های ریز را حذف می‌کند.",
     "params": [_n("n", "دوره", 15, 2, 200), _SRC]},

    {"type": "uo", "cat": "indicator", "sub": "نوسان‌نما", "label": "Ultimate Oscillator",
     "title": "UO", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "ترکیب سه بازهٔ ۷، ۱۴ و ۲۸ کندل در یک نوسان‌نما.",
     "params": []},

    {"type": "cmo", "cat": "indicator", "sub": "نوسان‌نما", "label": "CMO",
     "title": "CMO {n} {src}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "شاخص مومنتوم چاند: از ۱۰۰− تا ۱۰۰+.",
     "params": [_n("n", "دوره", 14, 2, 300), _SRC]},

    {"type": "aroon", "cat": "indicator", "sub": "روند", "label": "آرون",
     "title": "Aroon {n}", "inputs": [],
     "outputs": _out(("up", "Up", "num"), ("dn", "Down", "num"), ("osc", "اسیلاتور", "num")),
     "help": "چند وقت از آخرین سقف و کف N کندله گذشته است، بر حسب ۰ تا ۱۰۰. "
             "Up بالای ۷۰ و Down زیر ۳۰ یعنی روند صعودی تازه.",
     "params": [_n("n", "دوره", 25, 2, 300)]},

    {"type": "vortex", "cat": "indicator", "sub": "روند", "label": "ورتکس",
     "title": "VI {n}", "inputs": [],
     "outputs": _out(("pvi", "+VI", "num"), ("nvi", "−VI", "num")),
     "help": "تقاطع +VI و −VI شروع روند را نشان می‌دهد.",
     "params": [_n("n", "دوره", 14, 2, 300)]},

    {"type": "linreg", "cat": "indicator", "sub": "روند", "label": "رگرسیون خطی",
     "title": "LinReg {n} {src}", "inputs": [],
     "outputs": _out(("slope", "شیب", "num"), ("value", "مقدار خط", "num"),
                     ("r2", "R²", "num")),
     "help": "شیب خط رگرسیون N کندل اخیر، مقدار انتهای خط، و R² که می‌گوید چقدر از "
             "حرکت واقعاً روند بوده است. R² پایین یعنی نوسان بی‌جهت، نه روند.",
     "params": [_n("n", "دوره", 20, 3, 300), _SRC]},

    {"type": "keltner", "cat": "indicator", "sub": "کانال و نوسان", "label": "کانال کلتنر",
     "title": "KC {n},{mult}", "inputs": [],
     "outputs": _out(("upper", "بالا", "num"), ("mid", "میانه", "num"),
                     ("lower", "پایین", "num")),
     "help": "میانگین نمایی با پاکتی به اندازهٔ ATR — برخلاف بولینگر به دامنهٔ واقعی "
             "کندل واکنش نشان می‌دهد، نه به پراکندگی قیمت بسته‌شدن.",
     "params": [_n("n", "دوره", 20, 2, 300), _f("mult", "ضریب ATR", 2.0, 0.1, 10, 0.1), _SRC]},

    {"type": "donchian", "cat": "indicator", "sub": "کانال و نوسان", "label": "کانال دانچیان",
     "title": "DC {n}", "inputs": [],
     "outputs": _out(("upper", "سقف", "num"), ("mid", "میانه", "num"),
                     ("lower", "کف", "num")),
     "help": "بیشترین سقف و کمترین کف N کندل — پایهٔ فیلترهای «شکست».",
     "params": [_n("n", "دوره", 20, 2, MAX_PERIOD)]},

    {"type": "squeeze", "cat": "indicator", "sub": "کانال و نوسان", "label": "فشردگی نوسان",
     "title": "Squeeze {n}", "inputs": [], "outputs": _out(("out", "", "bool")),
     "help": "وقتی باند بولینگر کاملاً داخل کانال کلتنر می‌رود: نوسان جمع شده و "
             "معمولاً پیش از یک حرکت بزرگ است.",
     "params": [_n("n", "دوره", 20, 2, 300), _f("k", "ضریب بولینگر", 2.0, 0.1, 10, 0.1),
                _f("mult", "ضریب کلتنر", 1.5, 0.1, 10, 0.1), _SRC]},

    {"type": "bbpercent", "cat": "indicator", "sub": "کانال و نوسان", "label": "٪B بولینگر",
     "title": "%B {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "جای قیمت بین دو باند: ۰ روی باند پایین، ۱۰۰ روی باند بالا.",
     "params": [_n("n", "دوره", 20, 2, 300), _f("k", "ضریب انحراف", 2.0, 0.1, 10, 0.1), _SRC]},

    {"type": "relvol", "cat": "indicator", "sub": "حجم", "label": "حجم نسبی",
     "title": "RelVol {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "حجم امروز تقسیم بر میانگین حجم N کندل. عدد ۳ یعنی سه برابر حجم معمول — "
             "پرکاربردترین سنجهٔ «ورود پول» که با این داده می‌شود ساخت.",
     "params": [_n("n", "دوره", 20, 2, MAX_PERIOD),
                {"id": "src", "label": "منبع", "type": "select", "default": "volume",
                 "options": [{"v": "volume", "l": "حجم"}, {"v": "value", "l": "ارزش معاملات"},
                             {"v": "count", "l": "تعداد معاملات"}]}]},

    {"type": "vwap", "cat": "indicator", "sub": "حجم", "label": "VWAP غلتان",
     "title": "VWAP {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "میانگین قیمت وزنی حجم روی N کندل. دادهٔ این سامانه روزانه است، پس "
             "VWAP درون‌روزی معنا ندارد و این نسخهٔ غلتان جایگزین درست آن است.",
     "params": [_n("n", "دوره", 20, 2, MAX_PERIOD)]},

    {"type": "cmf", "cat": "indicator", "sub": "حجم", "label": "جریان نقدینگی چایکین",
     "title": "CMF {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "مثبت یعنی جمع‌آوری، منفی یعنی توزیع.",
     "params": [_n("n", "دوره", 20, 2, 300)]},

    {"type": "ad", "cat": "indicator", "sub": "حجم", "label": "خط انباشت/توزیع",
     "title": "A/D", "inputs": [], "outputs": _out(("out", "", "num")),
     "params": []},

    {"type": "force", "cat": "indicator", "sub": "حجم", "label": "شاخص نیرو",
     "title": "Force {n}", "inputs": [], "outputs": _out(("out", "", "num")),
     "help": "تغییر قیمت ضرب در حجم، هموارشده — قدرت پشت هر حرکت.",
     "params": [_n("n", "دورهٔ هموارسازی", 13, 1, 200)]},

    # ---- الگوهای کندلی ----------------------------------------------------
    {"type": "candle", "cat": "candle", "sub": "الگوها", "label": "الگوی کندلی",
     "title": "{pattern}", "inputs": [], "outputs": _out(("out", "", "bool")),
     "help": "همان الگوهای صفحهٔ «فیلترها»، ولی روی هر کندل — پس با «در N کندل اخیر» "
             "کار می‌کند. الگوهای برگشتی (چکش، ستاره) زمینهٔ روند قبلشان را هم چک می‌کنند.",
     "params": [{"id": "pattern", "label": "الگو", "type": "select",
                 "default": "bull_engulf",
                 "options": [{"v": k, "l": lbl} for k, lbl in _CANDLE_PATTERNS]},
                _n("shift", "شیفت", 0, 0, MAX_PERIOD)]},

    # ---- محاسبات ----------------------------------------------------------
    {"type": "math", "cat": "math", "sub": "محاسبات", "label": "محاسبات",
     "title": "a {op} b",
     "help": "چهار عمل اصلی روی دو ورودی. «a C% b» درصد اختلاف a نسبت به b است: "
             "(a−b)÷b×۱۰۰.",
     "inputs": [{"id": "a", "label": "a", "kind": "num"},
                {"id": "b", "label": "b", "kind": "num"}],
     "outputs": _out(("out", "", "num")),
     "params": [{"id": "op", "label": "عملگر", "type": "select", "default": "-",
                 "options": [{"v": "+", "l": "a + b"}, {"v": "-", "l": "a − b"},
                             {"v": "*", "l": "a × b"}, {"v": "/", "l": "a ÷ b"},
                             {"v": "C%", "l": "a C% b — درصد اختلاف"},
                             {"v": "%b", "l": "a %b — درصدی از b"},
                             {"v": "min", "l": "کمینهٔ a و b"},
                             {"v": "max", "l": "بیشینهٔ a و b"},
                             {"v": "^", "l": "a به توان b"}]}]},

    {"type": "unary", "cat": "math", "sub": "محاسبات", "label": "تابع",
     "title": "{op}(a)",
     "inputs": [{"id": "a", "label": "a", "kind": "num"}],
     "outputs": _out(("out", "", "num")),
     "params": [{"id": "op", "label": "تابع", "type": "select", "default": "abs",
                 "options": [{"v": "abs", "l": "قدر مطلق"}, {"v": "neg", "l": "قرینه"},
                             {"v": "sqrt", "l": "جذر"}, {"v": "ln", "l": "لگاریتم طبیعی"},
                             {"v": "log10", "l": "لگاریتم ۱۰"},
                             {"v": "round", "l": "گرد کردن"},
                             {"v": "floor", "l": "کف (رو به پایین)"},
                             {"v": "ceil", "l": "سقف (رو به بالا)"},
                             {"v": "sign", "l": "علامت (۱± یا ۰)"}]},
                _f("scale", "ضرب در", 1.0, -1e6, 1e6, 0.1)]},

    {"type": "ifelse", "cat": "math", "sub": "محاسبات", "label": "اگر / وگرنه",
     "title": "if ? a : b",
     "help": "اگر شرط برقرار باشد a، وگرنه b. برای ساختن مقدارهای شرطی و ستون‌های ترکیبی.",
     "inputs": [{"id": "cond", "label": "شرط", "kind": "bool"},
                {"id": "a", "label": "a", "kind": "num"},
                {"id": "b", "label": "b", "kind": "num"}],
     "outputs": _out(("out", "", "num")), "params": []},

    # ---- آماری (Common Math) ---------------------------------------------
    {"type": "agg", "cat": "stat", "sub": "پنجرهٔ غلتان", "label": "آماری",
     "title": "{op} {n}",
     "help": "محاسبهٔ غلتان روی N کندل اخیرِ ورودی — مثل «بیشترین close در ۲۰۰ کندل».",
     "inputs": [{"id": "a", "label": "a", "kind": "num"}],
     "outputs": _out(("out", "", "num")),
     "params": [{"id": "op", "label": "تابع", "type": "select", "default": "MAX",
                 "options": [{"v": "MAX", "l": "بیشینه"}, {"v": "MIN", "l": "کمینه"},
                             {"v": "SUM", "l": "جمع"}, {"v": "AVG", "l": "میانگین"},
                             {"v": "STDEV", "l": "انحراف معیار"},
                             {"v": "VAR", "l": "واریانس"},
                             {"v": "MEDIAN", "l": "میانه"},
                             {"v": "RANGE", "l": "دامنه (بیشینه−کمینه)"}]},
                _n("n", "تعداد کندل", 20)]},

    {"type": "change", "cat": "stat", "sub": "پنجرهٔ غلتان", "label": "تغییر",
     "title": "{op} {n}",
     "help": "اختلاف یا درصد تغییر ورودی نسبت به N کندل قبل.",
     "inputs": [{"id": "a", "label": "a", "kind": "num"}],
     "outputs": _out(("out", "", "num")),
     "params": [{"id": "op", "label": "نوع", "type": "select", "default": "PCT",
                 "options": [{"v": "PCT", "l": "درصد تغییر"}, {"v": "DIFF", "l": "اختلاف"},
                             {"v": "SHIFT", "l": "مقدار N کندل قبل"}]},
                _n("n", "تعداد کندل", 1)]},

    {"type": "counttrue", "cat": "stat", "sub": "دنباله", "label": "شمارش شرط",
     "title": "COUNT {n}",
     "help": "چند بار از N کندل اخیر شرط ورودی برقرار بوده است.",
     "inputs": [{"id": "a", "label": "شرط", "kind": "bool"}],
     "outputs": _out(("out", "", "num")),
     "params": [_n("n", "تعداد کندل", 5, 1, MAX_PERIOD)]},

    {"type": "percentile", "cat": "stat", "sub": "پنجرهٔ غلتان", "label": "موقعیت در بازه",
     "title": "POS {n}",
     "help": "ورودی کجای بازهٔ N کندل اخیر خودش ایستاده، از ۰ (کف) تا ۱۰۰ (سقف). "
             "«نزدیک سقف یک‌ساله» با یک جعبه، به‌جای سه تا.",
     "inputs": [{"id": "a", "label": "a", "kind": "num"}],
     "outputs": _out(("out", "", "num")),
     "params": [_n("n", "تعداد کندل", 60)]},

    {"type": "streak", "cat": "stat", "sub": "دنباله", "label": "دنبالهٔ شرط",
     "title": "STREAK",
     "help": "چند کندل پشت سر هم، تا همین کندل، شرط برقرار بوده است.",
     "inputs": [{"id": "a", "label": "شرط", "kind": "bool"}],
     "outputs": _out(("out", "", "num")), "params": []},

    {"type": "barssince", "cat": "stat", "sub": "دنباله", "label": "فاصله تا آخرین وقوع",
     "title": "SINCE",
     "help": "چند کندل از آخرین باری که شرط برقرار بوده گذشته است (۰ یعنی همین کندل). "
             "برای «تقاطع طلایی در ۱۰ کندل اخیر رخ داده و قیمت هنوز بالاست».",
     "inputs": [{"id": "a", "label": "شرط", "kind": "bool"}],
     "outputs": _out(("out", "", "num")), "params": []},

    # ---- مقایسه -----------------------------------------------------------
    {"type": "compare", "cat": "compare", "sub": "مقایسه", "label": "مقایسه",
     "title": "a {op} b",
     "inputs": [{"id": "a", "label": "a", "kind": "num"},
                {"id": "b", "label": "b", "kind": "num"}],
     "outputs": _out(("out", "", "bool")),
     "params": [{"id": "op", "label": "عملگر", "type": "select", "default": ">",
                 "options": [{"v": ">", "l": "a > b"}, {"v": "<", "l": "a < b"},
                             {"v": ">=", "l": "a ≥ b"}, {"v": "<=", "l": "a ≤ b"},
                             {"v": "=", "l": "a = b"}, {"v": "!=", "l": "a ≠ b"}]},
                _f("tol", "رواداریِ تساوی ٪", 0.0, 0.0, 100.0, 0.1)]},

    {"type": "between", "cat": "compare", "sub": "مقایسه", "label": "در بازه",
     "title": "{lo} ≤ a ≤ {hi}",
     "inputs": [{"id": "a", "label": "a", "kind": "num"}],
     "outputs": _out(("out", "", "bool")),
     "params": [_f("lo", "از", 0.0, -1e12, 1e12, 1), _f("hi", "تا", 100.0, -1e12, 1e12, 1)]},

    # ---- تقاطع ------------------------------------------------------------
    {"type": "cross", "cat": "cross", "sub": "تقاطع", "label": "تقاطع",
     "title": "{op}",
     "help": "CrossUp یعنی a در همین کندل از پایینِ b به بالای آن رفته است.",
     "inputs": [{"id": "a", "label": "a", "kind": "num"},
                {"id": "b", "label": "b", "kind": "num"}],
     "outputs": _out(("out", "", "bool")),
     "params": [{"id": "op", "label": "جهت", "type": "select", "default": "CrossUp",
                 "options": [{"v": "CrossUp", "l": "CrossUp — عبور به بالا"},
                             {"v": "CrossDn", "l": "CrossDn — عبور به پایین"},
                             {"v": "Cross", "l": "Cross — هر دو جهت"}]}]},

    {"type": "slope", "cat": "cross", "sub": "تقاطع", "label": "روند مقدار",
     "title": "{op} {n}",
     "help": "آیا ورودی نسبت به N کندل قبل صعودی یا نزولی است.",
     "inputs": [{"id": "a", "label": "a", "kind": "num"}],
     "outputs": _out(("out", "", "bool")),
     "params": [{"id": "op", "label": "جهت", "type": "select", "default": "Rising",
                 "options": [{"v": "Rising", "l": "صعودی"}, {"v": "Falling", "l": "نزولی"}]},
                _n("n", "تعداد کندل", 1)]},

    # ---- منطق -------------------------------------------------------------
    {"type": "and", "cat": "logic", "sub": "منطق", "label": "And", "title": "And",
     "help": "همهٔ شرط‌های وصل‌شده باید برقرار باشند. به این ورودی می‌توان چند اتصال وصل کرد.",
     "inputs": [{"id": "in", "label": "", "kind": "bool", "multi": True}],
     "outputs": _out(("out", "", "bool")), "params": []},

    {"type": "or", "cat": "logic", "sub": "منطق", "label": "Or", "title": "Or",
     "help": "دست‌کم یکی از شرط‌های وصل‌شده باید برقرار باشد.",
     "inputs": [{"id": "in", "label": "", "kind": "bool", "multi": True}],
     "outputs": _out(("out", "", "bool")), "params": []},

    {"type": "not", "cat": "logic", "sub": "منطق", "label": "Not", "title": "Not",
     "inputs": [{"id": "a", "label": "", "kind": "bool"}],
     "outputs": _out(("out", "", "bool")), "params": []},

    {"type": "atleast", "cat": "logic", "sub": "منطق", "label": "دست‌کم N شرط",
     "title": "≥ {n} از شرط‌ها",
     "help": "از میان شرط‌های وصل‌شده، دست‌کم N تا برقرار باشد.",
     "inputs": [{"id": "in", "label": "", "kind": "bool", "multi": True}],
     "outputs": _out(("out", "", "bool")),
     "params": [_n("n", "دست‌کم", 2, 1, 32)]},

    # ---- اطلاعات نماد ------------------------------------------------------
    {"type": "symbol", "cat": "symbol", "sub": "اطلاعات نماد", "label": "اطلاعات نماد",
     "title": "{field}",
     "inputs": [], "outputs": _out(("out", "", "text")),
     "params": [{"id": "field", "label": "فیلد", "type": "select", "default": "group",
                 "options": [{"v": "ticker", "l": "نماد"}, {"v": "name", "l": "نام"},
                             {"v": "group", "l": "گروه / نوع صندوق"},
                             {"v": "sub_group", "l": "زیرگروه"},
                             {"v": "panel", "l": "تابلو"},
                             {"v": "market", "l": "بازار"}]}]},

    {"type": "textmatch", "cat": "symbol", "sub": "اطلاعات نماد", "label": "تطبیق متن",
     "title": "{op} «{value}»",
     "inputs": [{"id": "a", "label": "", "kind": "text"}],
     "outputs": _out(("out", "", "bool")),
     "params": [{"id": "op", "label": "شرط", "type": "select", "default": "contains",
                 "options": [{"v": "contains", "l": "شامل باشد"},
                             {"v": "eq", "l": "برابر باشد"},
                             {"v": "ncontains", "l": "شامل نباشد"}]},
                {"id": "value", "label": "متن", "type": "text", "default": ""}]},

    {"type": "inlist", "cat": "symbol", "sub": "اطلاعات نماد", "label": "در فهرست نمادها",
     "title": "در فهرست",
     "help": "فقط نمادهایی که در فهرست نوشته‌اید. نام‌ها را با ویرگول جدا کنید — "
             "برای اجرای فیلتر روی سبد یا دیده‌بان خودتان.",
     "inputs": [], "outputs": _out(("out", "", "bool")),
     "params": [{"id": "value", "label": "نمادها (با ویرگول)", "type": "text", "default": ""}]},

    {"type": "bars", "cat": "symbol", "sub": "اطلاعات نماد", "label": "تعداد کندل موجود",
     "title": "تعداد کندل",
     "help": "چند کندل از این نماد در بازهٔ خوانده‌شده هست. برای کنار گذاشتن نمادهای "
             "تازه‌پذیرش که تاریخچهٔ کافی برای اندیکاتور ندارند.",
     "inputs": [], "outputs": _out(("out", "", "num")), "params": []},

    # ---- خروجی ------------------------------------------------------------
    {"type": "output", "cat": "output", "sub": "خروجی", "label": "خروجی فیلتر",
     "title": "خروجی فیلتر",
     "help": "نقطهٔ پایان گراف. نمادی در نتیجه می‌آید که شرط ورودی در «N کندل اخیر» "
             "آن برقرار باشد (۱ یعنی فقط آخرین کندل).",
     "inputs": [{"id": "in", "label": "", "kind": "bool"}],
     "outputs": [],
     "params": [_n("within", "در N کندل اخیر", 1, 1, MAX_WITHIN),
                # The DEFAULT is «قیمت پایانی» because `f` is already loaded for
                # every graph. Defaulting to traded value reads better on a
                # results table, and it made every filter in the app load an
                # extra column across eight hundred symbols to sort a list the
                # user had not asked to have sorted that way. Opt-in instead.
                {"id": "sort", "label": "مرتب‌سازی جدول", "type": "select",
                 "default": "price",
                 "options": [{"v": "price", "l": "قیمت پایانی (بیشترین اول)"},
                             {"v": "value", "l": "ارزش معاملات (بیشترین اول)"},
                             {"v": "pct", "l": "درصد تغییر (بیشترین اول)"},
                             {"v": "volume", "l": "حجم (بیشترین اول)"},
                             {"v": "ticker", "l": "نام نماد (الفبایی)"}]}]},

    {"type": "column", "cat": "output", "sub": "خروجی", "label": "ستون خروجی",
     "title": "ستون: {label}",
     "help": "مقدار این ورودی در آخرین کندل به‌صورت یک ستون در جدول نتیجه نشان داده می‌شود.",
     "inputs": [{"id": "a", "label": "", "kind": "num"}],
     "outputs": [],
     "params": [{"id": "label", "label": "عنوان ستون", "type": "text", "default": "مقدار"},
                _n("digits", "رقم اعشار", 2, 0, 6),
                {"id": "sort", "label": "مرتب‌سازی جدول", "type": "select", "default": "desc",
                 "options": [{"v": "desc", "l": "نزولی (بزرگ‌ترین اول)"},
                             {"v": "asc", "l": "صعودی (کوچک‌ترین اول)"},
                             {"v": "none", "l": "بدون مرتب‌سازی"}]}]},

    # ---- حد نوسان و سطوح ---------------------------------------------------
    {"type": "pricelimit", "cat": "price", "sub": "سطوح", "label": "سقف و کف مجاز",
     "title": "حد نوسان {band}", "inputs": [],
     "outputs": _out(("up", "سقف مجاز", "num"), ("dn", "کف مجاز", "num"),
                     ("dup", "تا سقف ٪", "num"), ("ddn", "تا کف ٪", "num"),
                     ("buyq", "صف خرید", "bool"), ("sellq", "صف فروش", "bool")),
     "help": "دامنهٔ نوسان مجاز روز، ساخته‌شده از «پایانی» کندل قبل. «خودکار» درصد را "
             "از بازار نماد برمی‌دارد — بورس و فرابورس ۵٪، پایه زرد ۳٪، نارنجی ۲٪، "
             "قرمز ۱٪، صندوق‌ها ۱۰٪ — و اگر سازمان دامنه را عوض کرد عدد را دستی "
             "بگذارید. «صف خرید» یعنی آخرین معامله روی سقف مجاز بسته شده و آن روز "
             "معامله‌ای هم خورده است؛ نمادِ بسته صف حساب نمی‌شود.",
     "params": [{"id": "band", "label": "دامنهٔ نوسان", "type": "select",
                 "default": "auto",
                 "options": [{"v": "auto", "l": "خودکار (از بازار نماد)", "short": "خودکار"},
                             {"v": "1", "l": "۱٪"}, {"v": "2", "l": "۲٪"},
                             {"v": "3", "l": "۳٪"}, {"v": "4", "l": "۴٪"},
                             {"v": "5", "l": "۵٪"}, {"v": "6", "l": "۶٪"},
                             {"v": "7", "l": "۷٪"}, {"v": "10", "l": "۱۰٪"}]}]},

    {"type": "srlevel", "cat": "price", "sub": "سطوح", "label": "حمایت و مقاومت",
     "title": "S/R {k}", "inputs": [],
     "outputs": _out(("res", "مقاومت", "num"), ("sup", "حمایت", "num"),
                     ("dres", "تا مقاومت ٪", "num"), ("dsup", "تا حمایت ٪", "num")),
     "help": "نزدیک‌ترین سقف و کف نوسانیِ تأییدشده، همان خطوطی که یک چارتیست دستی "
             "می‌کشد. «پهنای پیوت» یعنی یک قله باید بالاترین کندل در K کندل چپ و "
             "راستش باشد — و چون قله تا K کندل بعد تأیید نمی‌شود، سطح همیشه دست‌کم "
             "K کندل قدیمی است. سطحی که با دیدن آینده کشیده شود در بک‌تست عالی است "
             "و در بازار بی‌فایده.",
     "params": [_n("k", "پهنای پیوت", 3, 1, 30)]},

    # ---- فرمول‌نویسی --------------------------------------------------------
    {"type": "formula", "cat": "math", "sub": "فرمول", "label": "فرمول‌نویسی",
     "title": "= {expr}",
     "help": "یک رابطهٔ ریاضی به‌جای چند جعبهٔ محاسبات. متغیرها: a b c d (همین چهار "
             "ورودی، هرکدام را وصل نکنید لازم نیست) و نام فیلدهای قیمت — close، "
             "final، open، high، low، volume، value، hl2، hlc3 و بقیهٔ همان‌هایی که "
             "در «داده قیمت» هست. توابع: abs، sqrt، ln، log، exp، round، floor، "
             "ceil، sign، min(x,y)، max(x,y)، pow(x,y) و if(شرط,x,y). "
             "مقایسه‌ها (> < >= <= == !=) عدد ۱ یا ۰ می‌دهند، پس "
             "if(close>final,1,0) کار می‌کند. "
             "مثال: (high-low)/final*100",
     "inputs": [{"id": "a", "label": "a", "kind": "num", "optional": True},
                {"id": "b", "label": "b", "kind": "num", "optional": True},
                {"id": "c", "label": "c", "kind": "num", "optional": True},
                {"id": "d", "label": "d", "kind": "num", "optional": True}],
     "outputs": _out(("out", "", "num")),
     "params": [{"id": "expr", "label": "فرمول ریاضی", "type": "textarea",
                 "default": "(high-low)/final*100"}]},

    # ---- فهرست نمادها ------------------------------------------------------
    {"type": "universe", "cat": "symbol", "sub": "اطلاعات نماد", "label": "فهرست نمادها",
     "title": "فهرست نمادها", "inputs": [], "outputs": _out(("out", "", "bool")),
     "help": "دامنهٔ اجرای فیلتر را به بازار، صنعت و نام نماد محدود می‌کند. هر خانه "
             "را با ویرگول چندتایی بنویسید («فلز, شیمیایی») و هر خانهٔ خالی نادیده "
             "گرفته می‌شود. تطبیق «شامل بودن» است، پس «پایه» هر سه تابلوی پایه را "
             "می‌گیرد. برای کنار گذاشتن یک دسته، از همین جعبه با «Not» استفاده کنید.",
     "params": [{"id": "market", "label": "بازار", "type": "text", "default": ""},
                {"id": "panel", "label": "تابلو", "type": "text", "default": ""},
                {"id": "group", "label": "صنعت / گروه", "type": "text", "default": ""},
                {"id": "tickers", "label": "نمادها (با ویرگول)", "type": "text",
                 "default": ""}]},

    # ---- عمومی -------------------------------------------------------------
    {"type": "note", "cat": "general", "sub": "عمومی", "label": "توضیحات",
     "title": "{text}", "inputs": [], "outputs": [],
     "help": "یادداشتی روی بوم. هیچ محاسبه‌ای نمی‌کند و در نتیجه اثری ندارد — برای "
             "این است که سه ماه بعد بدانید این شاخه چه کاری می‌کرد.",
     "params": [{"id": "text", "label": "متن یادداشت", "type": "textarea",
                 "default": "توضیح این بخش…"}]},

    # ---- خروجی: سیگنال و هشدار ---------------------------------------------
    {"type": "signal", "cat": "output", "sub": "خروجی", "label": "برچسب سیگنال",
     "title": "سیگنال {signal}",
     "help": "ستون «سیگنال» را در جدول نتیجه پر می‌کند. چند تا از این جعبه با "
             "شرط‌های مختلف بگذارید تا یک فیلتر هم «خرید» بدهد و هم «فروش» — "
             "بدون اینکه مجبور شوید دو فیلتر جدا بسازید.",
     "inputs": [{"id": "in", "label": "تریگر", "kind": "bool"}],
     "outputs": [],
     "params": [{"id": "signal", "label": "سیگنال", "type": "select", "default": "buy",
                 "options": [{"v": "buy", "l": "خرید"}, {"v": "sell", "l": "فروش"},
                             {"v": "sbuy", "l": "خرید تعهدی"},
                             {"v": "ssell", "l": "فروش تعهدی"}]}]},

    {"type": "alert", "cat": "output", "sub": "خروجی", "label": "هشدار",
     "title": "هشدار",
     "help": "بعد از هر به‌روزرسانی دادهٔ بازار، این فیلتر خودکار اجرا می‌شود و برای "
             "نمادهای تازه‌ای که شرط را برآورده کرده‌اند در «هشدارها» اعلان می‌سازد. "
             "برای فعال شدن باید فیلتر را ذخیره کرده باشید — هشدار روی پیش‌نویس "
             "بوم اجرا نمی‌شود.",
     "inputs": [{"id": "in", "label": "تریگر", "kind": "bool"}],
     "outputs": [],
     "params": [{"id": "once", "label": "برای هر نماد فقط یک‌بار", "type": "select",
                 "default": "1",
                 "options": [{"v": "1", "l": "بله — تا وقتی دوباره وصل نشده"},
                             {"v": "0", "l": "خیر — هر روزی که شرط برقرار است"}]},
                {"id": "msg", "label": "قالب پیام", "type": "text",
                 "default": "«{ticker}» شرط فیلتر را برآورده کرد."}]},
]

# ---------------------------------------------------------------------------
# «تایم فریم» and «برگشت به عقب», attached to the whole catalogue at once
#
# In the reference guide these two are rows in the property table of every
# market-data and indicator block — not a feature of a chosen few. Writing them
# out forty times would make that true today and false the first time someone
# adds a block and forgets one, so they are attached here from the CATEGORY.
#
# Any hand-written `shift` is dropped first: several blocks already carried one
# under a different label, and two parameters that both mean "go back N bars"
# on the same box is worse than none.
# ---------------------------------------------------------------------------
#: Categories whose blocks are computed ON a time frame. Arithmetic, logic and
#: the output nodes have no time axis of their own — they inherit whatever frame
#: their inputs were computed on, which is why wiring a weekly RSI into a daily
#: comparison does the right thing without the comparison knowing anything.
_FRAMED_CATS = {"price", "indicator", "candle", "stat", "cross"}

#: …minus these. «عدد ثابت» is not a series, «تعداد کندل موجود» is a property of
#: the loaded window, and the daily price band is a daily rule by law — a
#: «سقف مجاز هفتگی» is not a thing that exists.
_UNFRAMED = {"const", "bars", "pricelimit"}

#: …plus this. «فرمول‌نویسی» sits in the math shelf but reads price fields, so a
#: weekly formula is exactly as meaningful as a weekly indicator.
_FRAMED_EXTRA = {"formula"}

FRAMED_TYPES = frozenset(
    {n["type"] for n in NODE_TYPES if n["cat"] in _FRAMED_CATS} - _UNFRAMED
) | _FRAMED_EXTRA

#: Everything framed, plus the price band — «سقف مجاز دیروز» is worth asking for.
SHIFTED_TYPES = FRAMED_TYPES | {"pricelimit"}

for _spec in NODE_TYPES:
    _t = _spec["type"]
    if _t in FRAMED_TYPES or _t in SHIFTED_TYPES:
        _spec["params"] = [p for p in _spec["params"] if p["id"] != "shift"]
    if _t in FRAMED_TYPES:
        _spec["params"] = [_TF] + _spec["params"]
        if "{tf}" not in _spec["title"]:
            _spec["title"] = _spec["title"] + " {tf}"
    if _t in SHIFTED_TYPES:
        _spec["params"] = _spec["params"] + [_SH]
        if "{~shift}" not in _spec["title"]:
            _spec["title"] = _spec["title"] + "{~shift}"
del _spec, _t

#: The blocks that END a graph — «خروجی فیلتر», «ستون خروجی», «برچسب سیگنال»,
#: «هشدار» and the «توضیحات» note. Derived from the catalogue rather than
#: listed, because "has no output port" IS the definition, and every place that
#: has to treat them differently (the interpreter's sink branch, the sweep in
#: verify_designer.py) then picks up the next one for free.
SINK_TYPES = frozenset(n["type"] for n in NODE_TYPES if not n["outputs"])

NODE_BY_TYPE = {n["type"]: n for n in NODE_TYPES}


def catalog():
    """The palette + everything the browser needs to draw and validate a graph."""
    return {"categories": CATEGORIES, "nodes": NODE_TYPES,
            "limits": {"nodes": MAX_NODES, "edges": MAX_EDGES,
                       "period": MAX_PERIOD, "within": MAX_WITHIN}}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class GraphError(ValueError):
    """A graph the interpreter refuses to run. The message is shown to the user
    verbatim, so it is written in Persian and names the offending node."""


def _num(v, default, lo, hi):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):
        return default
    return min(max(x, lo), hi)


def _int(v, default, lo, hi):
    return int(_num(v, default, lo, hi))


def normalise(graph):
    """Validate the incoming JSON and return (nodes_by_id, edges, order).

    Every parameter is clamped to the catalogue's own min/max rather than
    rejected: a slider that arrives as 10**9 is a UI bug or a curious user, and
    silently running the filter at the documented ceiling is friendlier than an
    error the user cannot act on. A structural problem — an unknown node type, a
    cycle, no output — is a real error and is raised."""
    if not isinstance(graph, dict):
        raise GraphError("گراف نامعتبر است.")
    raw_nodes = graph.get("nodes") or []
    raw_edges = graph.get("edges") or []
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise GraphError("گراف نامعتبر است.")
    if len(raw_nodes) > MAX_NODES:
        raise GraphError(f"تعداد نودها بیش از حد مجاز است (بیشینه {MAX_NODES}).")
    if len(raw_edges) > MAX_EDGES:
        raise GraphError(f"تعداد اتصال‌ها بیش از حد مجاز است (بیشینه {MAX_EDGES}).")

    nodes = {}
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            continue
        nid = str(raw.get("id") or "")
        spec = NODE_BY_TYPE.get(raw.get("type"))
        if not nid or spec is None:
            raise GraphError(f"نود ناشناخته در گراف: {raw.get('type')!r}")
        if nid in nodes:
            raise GraphError(f"شناسهٔ تکراری برای نود: {nid}")
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        clean = {}
        for p in spec["params"]:
            v = params.get(p["id"], p["default"])
            if p["type"] == "int":
                clean[p["id"]] = _int(v, p["default"], p["min"], p["max"])
            elif p["type"] == "float":
                clean[p["id"]] = _num(v, p["default"], p["min"], p["max"])
            elif p["type"] == "select":
                allowed = {o["v"] for o in p["options"]}
                clean[p["id"]] = v if v in allowed else p["default"]
            elif p["type"] == "textarea":
                clean[p["id"]] = str(v or "")[:_FORMULA_MAX_LEN]
            else:                                    # text
                clean[p["id"]] = str(v or "")[:120]
        if spec["type"] == "formula":
            # Parsed HERE rather than at evaluation time. A syntax error is
            # something the user typed and can fix, so it has to arrive as the
            # graph's error — not as eight hundred swallowed per-symbol
            # exceptions that show up as "this filter matches nothing".
            compile_formula(clean.get("expr", ""))
        nodes[nid] = {"id": nid, "type": spec["type"], "spec": spec, "params": clean,
                      "x": _num(raw.get("x"), 0, -1e5, 1e5),
                      "y": _num(raw.get("y"), 0, -1e5, 1e5),
                      "ins": {}}

    edges = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            continue
        src, dst = str(raw.get("from") or ""), str(raw.get("to") or "")
        if src not in nodes or dst not in nodes:
            continue                                 # a dangling edge is dropped
        sport = str(raw.get("fromPort") or "out")
        dport = str(raw.get("toPort") or "in")
        sspec, dspec = nodes[src]["spec"], nodes[dst]["spec"]
        if sport not in {o["id"] for o in sspec["outputs"]}:
            continue
        din = next((i for i in dspec["inputs"] if i["id"] == dport), None)
        if din is None:
            continue
        bucket = nodes[dst]["ins"].setdefault(dport, [])
        if bucket and not din.get("multi"):
            raise GraphError(
                f"ورودی «{din['label'] or dport}» در نود «{dspec['label']}» "
                "بیش از یک اتصال دارد.")
        bucket.append((src, sport))
        edges.append({"from": src, "fromPort": sport, "to": dst, "toPort": dport})

    outs = [n for n in nodes.values() if n["type"] == "output"]
    if not outs:
        raise GraphError("گراف باید یک نود «خروجی فیلتر» داشته باشد.")
    if len(outs) > 1:
        raise GraphError("فقط یک نود «خروجی فیلتر» مجاز است.")
    if not outs[0]["ins"].get("in"):
        raise GraphError("نود «خروجی فیلتر» ورودی ندارد — شرط را به آن وصل کنید.")

    order = _topo(nodes)
    _sign(nodes, order)
    return nodes, edges, order, outs[0]


def _sign(nodes, order):
    """Give every node a signature that is equal exactly when two nodes compute
    the same thing from the same inputs, and stamp it on the node.

    This is what makes the reference product's own layouts affordable. Its
    Ichimoku example draws SIX `Ichi ۹,۲۶,۵۲` chips and its candle patterns draw
    `close-1` four times, because one chip per wire reads far better on a canvas
    than one chip with six wires leaving it. Evaluated literally that is six
    Ichimoku computations per symbol; with the signature, evaluate() computes the
    first and hands the same series to the other five.

    Signatures are interned to small ints so the comparison stays cheap and so
    sorting them below can never compare a float against a string."""
    seen = {}
    sig = {}
    for nid in order:
        n = nodes[nid]
        wired = tuple(
            (port, tuple(sorted((sig[src], sport) for src, sport in n["ins"][port])))
            for port in sorted(n["ins"])
        )
        raw = (n["type"], tuple(sorted(n["params"].items())), wired)
        if raw not in seen:
            seen[raw] = len(seen)
        sig[nid] = n["sig"] = seen[raw]


def _topo(nodes):
    """Kahn's algorithm. Raises on a cycle, which is the one structural mistake
    the canvas cannot always prevent (two nodes wired into each other)."""
    indeg = {nid: 0 for nid in nodes}
    children = {nid: [] for nid in nodes}
    for nid, n in nodes.items():
        for bucket in n["ins"].values():
            for src, _ in bucket:
                indeg[nid] += 1
                children[src].append(nid)
    queue = [nid for nid, d in indeg.items() if d == 0]
    order = []
    while queue:
        nid = queue.pop()
        order.append(nid)
        for c in children[nid]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(nodes):
        raise GraphError("گراف حلقه دارد — یک مسیر به خودش بازمی‌گردد.")
    return order


def _own_lookback(t, p):
    """How many bars of ITS OWN FRAME one block needs before its first valid
    value — before «برگشت به عقب» and before the time frame are applied."""
    if t in ("sma", "ema", "wma", "rsi", "stdev", "boll"):
        return p["n"]
    if t == "macd":
        return p["slow"] + p["sig"]
    if t == "stoch":
        return p["n"] + p["ks"] + p["ds"]
    if t in ("atr", "cci", "willr", "mfi", "supertrend", "aroon", "vortex"):
        return p["n"] + 1
    if t == "adx":
        return p["n"] * 2 + 1
    if t == "ichimoku":
        return max(p["tenkan"], p["kijun"], p["spanb"]) + p["kijun"]
    if t == "ao":
        return 34
    if t == "uo":
        return 29
    if t in ("agg", "counttrue", "change", "slope"):
        return p["n"]
    if t in ("hma", "smma", "roc", "momentum", "cmo", "linreg", "keltner",
             "donchian", "squeeze", "bbpercent", "relvol", "vwap", "cmf",
             "force", "percentile", "vwma"):
        return p["n"]
    if t == "dema":
        return p["n"] * 2
    if t in ("tema", "trix"):
        return p["n"] * 3
    if t == "stochrsi":
        return p["n"] * 2 + p["ks"] + p["ds"]
    if t == "candle":
        # The reversal patterns look five bars back for their trend context.
        return 5
    if t == "srlevel":
        # A pivot is confirmed k bars after it prints, and a level is only
        # useful once a few of them exist.
        return p["k"] * 2 + 1 + _SWING_MEMORY * p["k"]
    if t in ("cross", "pivot", "pricelimit"):
        return 1
    return 0


def _raw_bars_needed(nodes, out_node):
    """How many DAILY bars this graph has to read, unbucketed and uncapped.

    A sum of every lookback on the graph, not a max: `SMA 200` of `close-20`
    inside a `MAX 50` window genuinely needs 270 bars of history before its first
    valid value, and returning a symbol as "no match" because the series was
    still warming up would be a silent wrong answer, not a slow one.

    «تایم فریم» multiplies rather than adds: a twenty-period weekly average is
    twenty WEEKS, so it costs a hundred sessions. That is the whole reason the
    bucket list reaches 1300 — a monthly average of any useful length does not
    fit in the 800 that daily-only graphs needed."""
    depth = {}
    for nid in _topo(nodes):
        n = nodes[nid]
        p = n["params"]
        own = (_own_lookback(n["type"], p) + p.get("shift", 0)) \
            * _TF_BARS.get(p.get("tf", "D"), 1)
        parents = [depth.get(src, 0)
                   for bucket in n["ins"].values() for src, _ in bucket]
        depth[nid] = own + (max(parents) if parents else 0)
    return depth.get(out_node["id"], 0) + out_node["params"]["within"] + 5


def bars_needed(nodes, out_node):
    """_raw_bars_needed(), rounded up into a shared cache bucket and capped.

    The cap is REPORTED, not hidden: run() compares the two and tells the user
    when a monthly 200-period average could not be warmed up, because the
    alternative — an empty result table with no explanation — is exactly the
    silent wrong answer this module keeps trying not to produce."""
    need = _raw_bars_needed(nodes, out_node)
    for cap in _BAR_BUCKETS:
        if need <= cap:
            return cap
    return _BAR_BUCKETS[-1]


# ---------------------------------------------------------------------------
# The price panel
# ---------------------------------------------------------------------------
#: The bar columns each node type has to read. Anything not listed here needs
#: only what its `src` parameter names.
_OHLC = ("h", "l", "c")
_NODE_FIELDS = {
    "stoch": _OHLC, "atr": _OHLC, "adx": _OHLC, "cci": _OHLC, "willr": _OHLC,
    "psar": _OHLC, "supertrend": _OHLC, "ichimoku": _OHLC, "ao": ("h", "l"),
    "mfi": ("h", "l", "c", "v"), "obv": ("c", "v"),
    # --- the second wave ---
    "uo": _OHLC, "vortex": _OHLC, "keltner": _OHLC, "squeeze": _OHLC,
    "aroon": ("h", "l"), "donchian": ("h", "l"),
    "heikin": ("o", "h", "l", "c"), "pivot": _OHLC,
    "candlepart": ("o", "h", "l", "c"), "candle": ("o", "h", "l", "c", "f"),
    "vwap": ("h", "l", "c", "v"), "cmf": ("h", "l", "c", "v"),
    "ad": ("h", "l", "c", "v"), "force": ("c", "v"), "vwma": ("v",),
    # --- the guide's blocks ---
    "srlevel": _OHLC, "pricelimit": ("c", "f", "v"),
}
_FIELD_COL = {"open": "o", "high": "h", "low": "l", "close": "c", "final": "f",
              "volume": "v", "value": "val", "count": "cnt",
              "hl2": None, "hlc3": None, "ohlc4": None,
              "pct": None, "range": None, "body": None,
              "ushadow": None, "lshadow": None}
#: A derived field is not a column — it is an expression over columns, and this
#: is what tells fields_needed() which columns to load for it.
_DERIVED = {"hl2": ("h", "l"), "hlc3": ("h", "l", "c"), "ohlc4": ("o", "h", "l", "c"),
            "pct": ("f",), "range": ("h", "l"), "body": ("o", "c"),
            "ushadow": ("o", "h", "c"), "lshadow": ("o", "l", "c")}


def fields_needed(nodes):
    """Which bar columns this graph actually reads.

    Loading all eight for every run was 800 symbols × 8 series × 400 bars of
    Python floats for a graph that usually touches two of them. Narrowing the
    SELECT is the difference between a 2.5 M-cell panel and a 600 K one, and it
    is also what makes the panel small enough to keep in memory between runs."""
    need = {"c", "f"}                    # `c` gates the warm-up test, `f` is «پایانی»
    for n in nodes.values():
        t, p = n["type"], n["params"]
        for col in _NODE_FIELDS.get(t, ()):
            need.add(col)
        src = p.get("field") if t == "price" else p.get("src")
        if src:
            _want(need, src)
        if t == "formula":
            # Only the columns the expression actually names. `close*volume`
            # reads two, not eight — and a formula over wired inputs alone reads
            # none at all.
            for name in formula_names(compile_formula(p.get("expr", ""))):
                _want(need, name)
        # A framed block needs the bucket column that says where its frames
        # begin — and it needs the FULL candle, because resampling a week into
        # one bar is an open/high/low/close operation whatever the block itself
        # ends up reading.
        # A framed block needs the bucket column that says where its frames
        # begin — and nothing else. Resampling is per column, so a weekly RSI on
        # «پایانی» reads `f` and `wk`, exactly as the daily one reads `f`.
        key = _TF_KEY.get(p.get("tf", "D"))
        if key:
            need.add(key)
        # «مرتب‌سازی» on the output block names a column, and it is loaded ONLY
        # when it is asked for: sorting the whole market by traded value should
        # cost one column, not make every filter in the app pay for it.
        if t == "output":
            col = _OUT_SORT_COL.get(p.get("sort", "price"))
            if col:
                need.add(col)
    return tuple(sorted(need))


#: The bar column each «مرتب‌سازی» option on the output block reads. «pct» is
#: computed from `f` alone (this session's settlement against the previous one),
#: which is the same definition every percentage on this platform uses.
_OUT_SORT_COL = {"value": "val", "volume": "v", "price": "f", "pct": "f",
                 "ticker": None}


def _want(need, field):
    """Add the columns one catalogue field name is built from."""
    if field in _DERIVED:
        need.update(_DERIVED[field])
    elif _FIELD_COL.get(field):
        need.add(_FIELD_COL[field])


# The panel is held in THIS PROCESS, not in Redis. Serialising ~600 K floats
# through the cache codec on every run costs more than the query it saves, and a
# 20 MB value per (kind, as_of, bars) is not what a shared Redis is for.
#
# It is stored COLUMN BY COLUMN. The first thing the designer does is turn one
# graph into ten variations, and those variations differ by a threshold, not by
# the data they read — but the moment one of them adds a `high` node, a
# panel-shaped cache keyed on the field set misses completely and re-queries all
# four columns. Keyed per column, adding `high` to a running graph costs exactly
# one column's worth of query, and nothing already loaded is fetched twice.
#
# There is one entry per (kind, as_of), holding the deepest bar count anyone has
# asked for; a shallower request is served by slicing the tail, since bars are
# stored oldest→newest. Two symbol kinds, one trading day: the dict never holds
# more than a handful of columns and is dropped whole when as_of moves.
_PANEL_CACHE = {}
_PANEL_MAX = 2

_COL_SQL = {
    "o": "adj_open", "h": "adj_high", "l": "adj_low", "c": "adj_close",
    "f": "adj_final", "v": "volume", "val": "value", "cnt": '"no"',
    # «تایم فریم». Not prices — bucket NUMBERS, whose only job is to change when
    # a new frame starts.
    #
    # 2000-01-01 was a SATURDAY, so integer-dividing the day offset by seven
    # gives weeks that begin on Saturday — the Tehran trading week. An ISO week
    # would put Saturday's session in with the previous Sunday-to-Wednesday and
    # split every real trading week across two buckets.
    "wk": "((date - DATE '2000-01-01') / 7)",
    # The JALALI month off j_date ('1405-06-04'), not the Gregorian one: «شهریور»
    # is a month a user can reason about, «August» straddles two of them. The
    # regex guard is not decoration — one malformed j_date would otherwise make
    # the whole market-wide query raise on a cast.
    "mo": ("(CASE WHEN j_date ~ '^[0-9]{4}-[0-9]{1,2}' "
           "THEN split_part(j_date, '-', 1)::int * 12 "
           "   + split_part(j_date, '-', 2)::int ELSE 0 END)"),
}


#: The columns that are PRICES. A price of zero is not a price — see the repair
#: in _load_columns below. Volume, value and trade count are excluded on purpose:
#: zero is a perfectly good answer for all three (a session with no trades).
_PRICE_COLS = ("o", "h", "l", "c", "f")


def _load_columns(kind, as_of, bars, cols):
    """{col: {ticker: [float] oldest→newest}} for `cols`, straight from the
    price table.

    WHY THE OHL REPAIR IS HERE

    347 rows in this database carry `adj_open = adj_high = adj_low = 0` with a
    perfectly valid `adj_close` and `adj_final` — the exchange settled the symbol
    but recorded no intraday range. «ثاژن» alone has thirty of them. The panel's
    WHERE clause only requires close and final to be positive, so those bars
    arrive, and the loader used to substitute 0.0 for the missing ones.

    Which is how «شکست کانال دانچیان» reported ثاژن as a breakout: the twenty-bar
    highest high came out as ZERO, and 6,300 > 0 is true. A false positive
    manufactured by the loader, not by the data — the data honestly said "no
    high recorded", and the code answered "the high was zero rials".

    A bar with a settlement price and no range is a FLAT bar, so that is what it
    becomes: open = high = low = the settlement price. That keeps every series a
    clean list of floats — no None for fifteen indicator functions to guard
    against — and makes the range of such a bar zero, which is the truth.
    """
    price_tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    # `f` is fetched whenever any of O/H/L is, because it is what a broken bar is
    # repaired to. It costs one column and is almost always wanted anyway.
    need = list(cols)
    repair = [c for c in ("o", "h", "l") if c in cols]
    if repair and "f" not in need:
        need.append("f")
    select = ", ".join(f'{_COL_SQL[c]}::float8 AS "{c}"' for c in need)
    names = ", ".join(f'"{c}"' for c in need)
    rows = db._rows(
        f"""
        WITH ranked AS (
            SELECT ticker, {select},
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) rn
            FROM {price_tbl}
            WHERE adj_close > 0 AND adj_final > 0 AND date <= %s AND date >= %s
        )
        SELECT ticker, {names} FROM ranked WHERE rn <= %s
        ORDER BY ticker, rn DESC
        """,
        (*db._window(kind, as_of, max(2, bars // 200 + 2)), bars))

    out = {c: {} for c in cols}
    for r in rows:
        t = r["ticker"]
        settle = r.get("f")
        for c in cols:
            v = r[c]
            if c in _PRICE_COLS:
                if v is None or v <= 0:
                    # `settle` is guaranteed positive by the WHERE clause; the
                    # `or v` tail is only for a column loaded without one.
                    v = settle if (settle is not None and settle > 0) else None
                if v is None:
                    v = 0.0                      # nothing left to repair it with
            elif v is None:
                v = 0.0                          # volume / value / count
            out[c].setdefault(t, []).append(v)
    return out


def _panel(kind, as_of, bars, fields):
    """{ticker: {col: series}} for every symbol, with only `fields` loaded."""
    key = (kind, as_of)
    ent = _PANEL_CACHE.get(key)
    if ent is None or ent["bars"] < bars:
        # A deeper request invalidates the shallower store: the extra history is
        # at the FRONT of each series, so it cannot be appended to what is here.
        ent = {"bars": bars, "cols": {}}
        if len(_PANEL_CACHE) >= _PANEL_MAX:
            _PANEL_CACHE.pop(next(iter(_PANEL_CACHE)))
        _PANEL_CACHE[key] = ent

    missing = [f for f in fields if f not in ent["cols"]]
    if missing:
        ent["cols"].update(_load_columns(kind, as_of, ent["bars"], missing))

    depth = ent["bars"]
    store = ent["cols"]
    panel = {}
    for ticker in store[fields[0]]:
        row = {}
        for f in fields:
            series = store[f].get(ticker)
            if series is None:
                row = None
                break
            row[f] = series if len(series) <= bars or depth == bars else series[-bars:]
        if row is not None:
            panel[ticker] = row
    return panel


#: Bump this whenever the SHAPE of a _meta() record changes — a new key, a
#: renamed one, a different source column.
#:
#: It is part of the Redis cache key, and it has to be, because nothing else
#: would invalidate it on a deploy. cache.bump_version() is called from exactly
#: one place — db.clear_cache(), on a data update — so a release that adds a
#: field to this dict would go on being served the OLD dict for up to
#: ANALYTICS_CACHE_TTL (six hours) by every worker. That is how «تابلو» could
#: have shipped, been fixed, and still answered nothing in production.
_META_SHAPE = "v2-panel"


def _meta(kind):
    """Ticker → id / name / group / sub_group / panel / market. Small enough for
    the shared cache, and every page in the app already pays for these queries."""
    def build():
        if kind == "stock":
            rows = db._rows("SELECT stockid AS id, ticker, name, market, panel, sector, "
                            "sub_sector FROM stocks")
            # `panel` is «تابلو» — بازار اول / دوم / پایه زرد و… . It is the
            # single most-used exclusion on this exchange («بازار پایه نباشد»),
            # and it is a column on `stocks` that this dict simply did not carry
            # until the symbol node offered it as an option and returned nothing.
            return {r["ticker"]: {"id": r["id"], "ticker": r["ticker"], "name": r["name"],
                                  "group": r["sector"], "sub_group": r["sub_sector"],
                                  "panel": r["panel"], "market": r["market"]} for r in rows}
        # ETFs have no sector tree and no panel; the keys exist anyway so the two
        # kinds have one shape and _eval_node never has to ask which it is.
        rows = db._rows("SELECT id, ticker, name, type FROM etf")
        return {r["ticker"]: {"id": r["id"], "ticker": r["ticker"], "name": r["name"],
                              "group": r["type"], "sub_group": None, "panel": None,
                              "market": r["type"]} for r in rows}

    return cache.get_or_set("designer_meta", (_META_SHAPE, kind), build)


# ---------------------------------------------------------------------------
# The interpreter
#
# One value is a (kind, payload) pair:
#     ("num",   [float|None] × N)
#     ("bool",  [bool|None]  × N)
#     ("const", float)              — broadcast on demand
#     ("text",  str)
# ---------------------------------------------------------------------------
def _series(val, n):
    kind, payload = val
    if kind == "const":
        return [payload] * n
    if kind == "bool":
        return [None if b is None else (1.0 if b else 0.0) for b in payload]
    if kind == "text":
        return [None] * n
    return payload


def _bools(val, n):
    kind, payload = val
    if kind == "bool":
        return payload
    if kind == "const":
        return [bool(payload)] * n
    if kind == "num":
        return [None if x is None else bool(x) for x in payload]
    return [None] * n


def _src(ctx, field):
    """A price field by its catalogue name, including every derived one.

    The derived fields are here rather than as separate node types because they
    are what a filter says in one breath — «دامنهٔ کندل», «درصد تغییر» — and a
    user should not have to wire a subtraction to ask for the candle's range.
    """
    b = ctx["bars"]
    if field in _DERIVED:
        if field == "hl2":
            return [(x + y) / 2.0 for x, y in zip(b["h"], b["l"])]
        if field == "hlc3":
            return [(x + y + z) / 3.0 for x, y, z in zip(b["h"], b["l"], b["c"])]
        if field == "ohlc4":
            return [(a + x + y + z) / 4.0
                    for a, x, y, z in zip(b["o"], b["h"], b["l"], b["c"])]
        if field == "range":
            return [x - y for x, y in zip(b["h"], b["l"])]
        if field == "body":
            return [abs(x - y) for x, y in zip(b["c"], b["o"])]
        if field == "ushadow":
            return [h - max(o, c) for o, h, c in zip(b["o"], b["h"], b["c"])]
        if field == "lshadow":
            return [min(o, c) - l for o, l, c in zip(b["o"], b["l"], b["c"])]
        # pct — «درصد تغییر» on the settlement price, which is what every
        # percentage on this platform is computed from (see PRICE_FIELDS).
        #
        # The first bar of the window has nothing before it, so its change is
        # 0 — "no change observed", not "unknown". That distinction is not
        # pedantry: EVERY consumer of a source series (db._sma_series,
        # _boll_series, _rsi_series, …) assumes a clean list of floats and
        # raises TypeError on a None. Because run() catches per-symbol
        # exceptions, that raise is invisible — the filter simply matches
        # nothing — which is exactly how «٪B روی درصد تغییر» failed silently for
        # every symbol in the market until a sweep over every dropdown option
        # found 51 swallowed tracebacks.
        f = b["f"]
        prev = _shift(f, 1)
        out = _binary(f, prev, lambda x, y: (x - y) / y * 100.0 if y else None)
        return [0.0 if v is None else v for v in out]
    col = _FIELD_COL[field]
    return b.get(col) or [None] * len(b["c"])


def _cross(a, b, op):
    out = [None] * len(a)
    for i in range(1, len(a)):
        p1, p2, c1, c2 = a[i - 1], b[i - 1], a[i], b[i]
        if None in (p1, p2, c1, c2):
            continue
        up = p1 <= p2 and c1 > c2
        dn = p1 >= p2 and c1 < c2
        out[i] = up if op == "CrossUp" else (dn if op == "CrossDn" else (up or dn))
    return out


#: «دامنهٔ نوسان مجاز» by market, for the price-band block's «خودکار».
#:
#: These are the exchange's published bands, and they are a PARAMETER on the
#: block precisely because the regulator moves them — twice in 1402 alone. A
#: user whose filter suddenly reports no buy queues changes one dropdown; they
#: do not wait for us to ship.
_BAND_BY_MARKET = {"بورس": 5.0, "فرابورس": 5.0, "پایه زرد": 3.0,
                   "پایه نارنجی": 2.0, "پایه قرمز": 1.0}
_BAND_DEFAULT = 5.0
_BAND_ETF = 10.0

#: How close to the limit still counts as sitting on it. Adjusted prices carry a
#: capital-increase factor, and the exchange rounds the limit to whole rials, so
#: an exact equality test would miss most real queues.
_QUEUE_TOL = 0.001

_SIGNAL_LABELS = {"buy": "خرید", "sell": "فروش",
                  "sbuy": "خرید تعهدی", "ssell": "فروش تعهدی"}


def _csv(raw):
    """A comma-separated list the way a user actually pastes one — Latin comma,
    Persian comma, or one per line, because a watchlist copied out of a broker
    arrives in whichever of the three that broker happened to use."""
    raw = (raw or "").replace("،", ",").replace("\n", ",")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _band_pct(mode, ctx):
    if mode != "auto":
        try:
            return float(mode)
        except (TypeError, ValueError):
            return _BAND_DEFAULT
    if ctx.get("kind") == "etf":
        return _BAND_ETF
    market = ((ctx["meta"] or {}).get("market") or "").strip()
    return _BAND_BY_MARKET.get(market, _BAND_DEFAULT)


def _frame_ctx(ctx, tf):
    """(frame ctx, daily→frame index) for this symbol, built at most once.

    Cached ON the ctx, so a graph with nine weekly blocks resamples the panel
    once rather than nine times — which matters, because resampling is the one
    operation here that touches every column of every bar."""
    frames = ctx.setdefault("frames", {})
    hit = frames.get(tf)
    if hit is None:
        keys = ctx["bars"].get(_TF_KEY.get(tf, ""))
        if keys is None:
            # fields_needed() always loads the bucket column for a frame that is
            # actually used, so this is unreachable from run(); it is the safety
            # net for a hand-built ctx in a test, and daily is the honest answer.
            hit = (ctx, list(range(ctx["n"])))
        else:
            idx, count = _frame_index(keys)
            hit = ({"bars": _FrameBars(ctx["bars"], idx, count), "n": count,
                    "meta": ctx["meta"], "kind": ctx.get("kind"), "frames": {}},
                   idx)
        frames[tf] = hit
    return hit


def _eval_in_frame(node, ctx, get):
    """_eval_node(), wrapped in this block's «تایم فریم» and «برگشت به عقب».

    Both properties live HERE rather than inside the forty branches of
    _eval_node, and that is the whole reason they could be added to every block
    at once: a frame is a re-indexing of the inputs and of the outputs, and a
    shift is a translation of the outputs. Neither is anything an indicator has
    to know about, so no indicator was touched to gain them."""
    p = node["params"]
    tf = p.get("tf", "D")
    shift = int(p.get("shift", 0) or 0)

    if tf in _TF_KEY:
        sub_ctx, idx = _frame_ctx(ctx, tf)
        count = sub_ctx["n"]

        def framed(port, all_of=False):
            got = get(port, all_of=all_of)
            if all_of:
                return [_to_frame(v, idx, count) for v in got]
            return None if got is None else _to_frame(got, idx, count)

        out = _eval_node(node, sub_ctx, framed)
        out = {k: _from_frame(v, idx) for k, v in out.items()}
    else:
        out = _eval_node(node, ctx, get)

    if shift:
        out = {k: _shift_val(v, shift) for k, v in out.items()}
    return out


def _memoise(nodes, order, ctx):
    """Every node's outputs for ONE symbol, computed once per distinct chip.

    Shared by evaluate() and explain() so the two can never drift: «چرا این نماد
    آمد؟» has to print the numbers the market-wide run actually used, and the
    surest way to guarantee that is for both to be the same function."""
    memo = {}
    by_sig = {}
    for nid in order:
        node = nodes[nid]
        hit = by_sig.get(node["sig"])
        if hit is not None:                   # an identical chip drawn twice
            memo[nid] = hit
            continue

        def get(port, all_of=False, _node=node):
            bucket = _node["ins"].get(port) or []
            vals = [memo[src].get(sport) for src, sport in bucket
                    if src in memo and memo[src].get(sport) is not None]
            return vals if all_of else (vals[0] if vals else None)

        memo[nid] = by_sig[node["sig"]] = _eval_in_frame(node, ctx, get)
    return memo


def _eval_node(node, ctx, get):
    """Evaluate ONE node. `get(port)` returns the already-computed value wired
    into that input port (or None when nothing is)."""
    t, p = node["type"], node["params"]
    b = ctx["bars"]
    n = ctx["n"]
    # NOT unpacked up front: fields_needed() only loads the columns this graph
    # reads, so b["h"] does not exist for a graph made of close and volume, and
    # touching it here would raise on every symbol of every such filter.
    ohlc = lambda: (b["h"], b["l"], b["c"])

    # ---- sources ---------------------------------------------------------
    if t == "price":
        return {"out": ("num", _src(ctx, p["field"]))}
    if t == "const":
        return {"out": ("const", p["value"])}
    if t == "symbol":
        return {"out": ("text", str(ctx["meta"].get(p["field"]) or ""))}

    # ---- indicators ------------------------------------------------------
    if t in ("sma", "ema", "wma", "rsi", "stdev"):
        px = _src(ctx, p["src"])
        if t == "sma":
            s = _ma_of(px, p["n"], p["method"])
        elif t == "ema":
            s = _ema_of(px, p["n"])
        elif t == "wma":
            s = _wma_of(px, p["n"])
        elif t == "rsi":
            s = db._rsi_series(px, p["n"])
        else:
            s = _roll(px, p["n"], "STDEV")
        return {"out": ("num", s)}

    if t == "macd":
        m, sig, hist = db._macd_series(_src(ctx, p["src"]), p["fast"], p["slow"], p["sig"])
        return {"macd": ("num", m), "signal": ("num", sig), "hist": ("num", hist)}

    if t == "boll":
        px = _src(ctx, p["src"])
        mid = _ma_of(px, p["n"], p["method"])
        sd = _roll(px, p["n"], "STDEV")
        up = _binary(mid, sd, lambda m_, s_: m_ + p["k"] * s_)
        lo = _binary(mid, sd, lambda m_, s_: m_ - p["kd"] * s_)
        width = [((u - d) / m * 100.0) if (u is not None and d is not None and m)
                 else None for u, d, m in zip(up, lo, mid)]
        return {"upper": ("num", up), "mid": ("num", mid),
                "lower": ("num", lo), "width": ("num", width)}

    if t == "stoch":
        H, L, C = ohlc()
        hh, ll = _roll(H, p["n"], "MAX"), _roll(L, p["n"], "MIN")
        raw = [None] * n
        for i in range(n):
            if hh[i] is None or ll[i] is None:
                continue
            span = hh[i] - ll[i]
            # A bar whose n-bar range is a single price is neither overbought
            # nor oversold; 50 is the midpoint every implementation uses.
            raw[i] = 50.0 if span == 0 else (C[i] - ll[i]) / span * 100.0
        k = _ma_of(raw, p["ks"], p["method"]) if p["ks"] > 1 else raw
        d = _ma_of(k, p["ds"], p["method"]) if p["ds"] > 1 else list(k)
        return {"k": ("num", k), "d": ("num", d)}

    if t == "atr":
        H, L, C = ohlc()
        return {"out": ("num", _atr_series(H, L, C, p["n"]))}
    if t == "adx":
        H, L, C = ohlc()
        adx, pdi, ndi = _adx_series(H, L, C, p["n"])
        return {"adx": ("num", adx), "pdi": ("num", pdi), "ndi": ("num", ndi)}
    if t == "cci":
        H, L, C = ohlc()
        return {"out": ("num", db._cci_series(H, L, C, p["n"]))}
    if t == "willr":
        H, L, C = ohlc()
        return {"out": ("num", db._willr_series(H, L, C, p["n"]))}
    if t == "mfi":
        H, L, C = ohlc()
        return {"out": ("num", _mfi_series(H, L, C, b["v"], p["n"]))}
    if t == "obv":
        return {"out": ("num", _obv_series(_src(ctx, p["src"]), b["v"]))}
    if t == "ao":
        return {"out": ("num", db._ao_series(b["h"], b["l"]))}
    if t == "ichimoku":
        H, L, C = ohlc()
        tk, kj, sa, sb = _ichimoku(H, L, C, p["tenkan"], p["kijun"], p["spanb"])
        return {"tenkan": ("num", tk), "kijun": ("num", kj),
                "spana": ("num", sa), "spanb": ("num", sb)}
    if t == "psar":
        return {"out": ("num", _psar_series(b["h"], b["l"], p["step"], p["cap"]))}
    if t == "supertrend":
        H, L, C = ohlc()
        line, direction = _supertrend(H, L, C, p["n"], p["mult"])
        return {"line": ("num", line), "dir": ("num", direction)}

    # ---- moving averages (the second wave) -------------------------------
    if t in ("hma", "dema", "tema", "smma", "vwma"):
        px = _src(ctx, p["src"])
        if t == "hma":
            out = _hma_series(px, p["n"])
        elif t == "dema":
            out = _dema_series(px, p["n"])
        elif t == "tema":
            out = _tema_series(px, p["n"])
        elif t == "smma":
            out = _rma_series(px, p["n"])
        else:
            out = _vwma_series(px, b["v"], p["n"])
        return {"out": ("num", out)}

    # ---- oscillators -----------------------------------------------------
    if t == "stochrsi":
        k, d = _stochrsi_series(_src(ctx, p["src"]), p["n"], p["ks"], p["ds"])
        return {"k": ("num", k), "d": ("num", d)}
    if t == "roc":
        return {"out": ("num", _roc_series(_src(ctx, p["src"]), p["n"]))}
    if t == "momentum":
        px = _src(ctx, p["src"])
        return {"out": ("num", _binary(px, _shift(px, p["n"]), lambda x, y: x - y))}
    if t == "trix":
        return {"out": ("num", _trix_series(_src(ctx, p["src"]), p["n"]))}
    if t == "uo":
        H, L, C = ohlc()
        return {"out": ("num", _uo_series(H, L, C))}
    if t == "cmo":
        return {"out": ("num", _cmo_series(_src(ctx, p["src"]), p["n"]))}

    # ---- trend -----------------------------------------------------------
    if t == "aroon":
        up, dn = _aroon_series(b["h"], b["l"], p["n"])
        osc = _binary(up, dn, lambda x, y: x - y)
        return {"up": ("num", up), "dn": ("num", dn), "osc": ("num", osc)}
    if t == "vortex":
        H, L, C = ohlc()
        pvi, nvi = _vortex_series(H, L, C, p["n"])
        return {"pvi": ("num", pvi), "nvi": ("num", nvi)}
    if t == "linreg":
        slope, value, r2 = _linreg_series(_src(ctx, p["src"]), p["n"])
        return {"slope": ("num", slope), "value": ("num", value), "r2": ("num", r2)}

    # ---- channels and volatility -----------------------------------------
    if t == "keltner":
        H, L, C = ohlc()
        up, mid, lo = _keltner_series(H, L, C, _src(ctx, p["src"]), p["n"], p["mult"])
        return {"upper": ("num", up), "mid": ("num", mid), "lower": ("num", lo)}
    if t == "donchian":
        up, mid, lo = _donchian_series(b["h"], b["l"], p["n"])
        return {"upper": ("num", up), "mid": ("num", mid), "lower": ("num", lo)}
    if t == "squeeze":
        H, L, C = ohlc()
        px = _src(ctx, p["src"])
        _, bb_up, bb_lo = db._boll_series(px, p["n"], p["k"])
        kc_up, _, kc_lo = _keltner_series(H, L, C, px, p["n"], p["mult"])
        out = [None] * n
        for i in range(n):
            if None in (bb_up[i], bb_lo[i], kc_up[i], kc_lo[i]):
                continue
            out[i] = bb_up[i] < kc_up[i] and bb_lo[i] > kc_lo[i]
        return {"out": ("bool", out)}
    if t == "bbpercent":
        mid, up, lo = db._boll_series(_src(ctx, p["src"]), p["n"], p["k"])
        out = [None] * n
        for i in range(n):
            if up[i] is None or lo[i] is None:
                continue
            span = up[i] - lo[i]
            px_i = _src(ctx, p["src"])[i]
            out[i] = 50.0 if span == 0 else (px_i - lo[i]) / span * 100.0
        return {"out": ("num", out)}

    # ---- volume ----------------------------------------------------------
    if t == "relvol":
        px = _src(ctx, p["src"])
        avg = db._sma_series(px, p["n"])
        return {"out": ("num", _binary(px, avg, lambda x, y: x / y if y else None))}
    if t == "vwap":
        H, L, C = ohlc()
        return {"out": ("num", _vwap_series(H, L, C, b["v"], p["n"]))}
    if t == "cmf":
        H, L, C = ohlc()
        return {"out": ("num", _cmf_series(H, L, C, b["v"], p["n"]))}
    if t == "ad":
        H, L, C = ohlc()
        return {"out": ("num", _ad_series(H, L, C, b["v"]))}
    if t == "force":
        return {"out": ("num", _force_series(b["c"], b["v"], p["n"]))}

    # ---- candles ---------------------------------------------------------
    if t == "heikin":
        ho, hh, hl, hc = _heikin_series(b["o"], b["h"], b["l"], b["c"])
        return {"o": ("num", ho), "h": ("num", hh),
                "l": ("num", hl), "c": ("num", hc)}
    if t == "pivot":
        H, L, C = ohlc()
        lv = _pivot_series(H, L, C, p["mode"])
        return {k: ("num", v) for k, v in lv.items()}
    if t == "candlepart":
        o_, h_, l_, c_ = b["o"], b["h"], b["l"], b["c"]
        part = p["part"]
        if part == "body":
            out = [abs(c - o) for o, c in zip(o_, c_)]
        elif part == "range":
            out = [h - l for h, l in zip(h_, l_)]
        elif part == "ushadow":
            out = [h - max(o, c) for o, h, c in zip(o_, h_, c_)]
        elif part == "lshadow":
            out = [min(o, c) - l for o, l, c in zip(o_, l_, c_)]
        elif part == "bodyratio":
            out = [(abs(c - o) / (h - l)) if h > l else None
                   for o, h, l, c in zip(o_, h_, l_, c_)]
        else:                                   # pos — where in the bar it closed
            out = [((c - l) / (h - l) * 100.0) if h > l else None
                   for h, l, c in zip(h_, l_, c_)]
        return {"out": ("num", out)}
    if t == "candle":
        return {"out": ("bool", _candle_series(b["o"], b["h"], b["l"], b["c"],
                                               b["f"], p["pattern"]))}

    # ---- the price band and the swing levels -----------------------------
    if t == "pricelimit":
        f, c_ = b["f"], b["c"]
        vol = b.get("v")
        prev = _shift(f, 1)
        band = _band_pct(p["band"], ctx) / 100.0
        up = [None if x is None else x * (1.0 + band) for x in prev]
        dn = [None if x is None else x * (1.0 - band) for x in prev]
        dup = _binary(up, c_, lambda u, c: (u - c) / c * 100.0 if c else None)
        ddn = _binary(c_, dn, lambda c, d: (c - d) / c * 100.0 if c else None)
        buyq, sellq = [None] * n, [None] * n
        for i in range(n):
            u, d, c = up[i], dn[i], c_[i]
            if u is None or d is None or not c:
                continue
            # A symbol that did not trade sits at yesterday's price and would
            # otherwise report a queue every single day it stays suspended.
            traded = True if vol is None else (vol[i] or 0) > 0
            # BOTH sides, not just "at or above". A symbol reopening after a
            # halt («بازگشایی») trades with no limit at all and routinely closes
            # well above yesterday's ceiling — a one-sided test called every one
            # of those a buy queue, which is the opposite of the truth: a
            # reopening is the one session where there is no queue to be in.
            buyq[i] = bool(traded and u * (1.0 - _QUEUE_TOL) <= c
                           <= u * (1.0 + _QUEUE_TOL))
            sellq[i] = bool(traded and d * (1.0 - _QUEUE_TOL) <= c
                            <= d * (1.0 + _QUEUE_TOL))
        return {"up": ("num", up), "dn": ("num", dn), "dup": ("num", dup),
                "ddn": ("num", ddn), "buyq": ("bool", buyq),
                "sellq": ("bool", sellq)}

    if t == "srlevel":
        H, L, C = ohlc()
        res, sup = _swing_levels(H, L, C, p["k"])
        return {"res": ("num", res), "sup": ("num", sup),
                "dres": ("num", _binary(res, C,
                                        lambda r, c: (r - c) / c * 100.0 if c else None)),
                "dsup": ("num", _binary(C, sup,
                                        lambda c, x: (c - x) / c * 100.0 if c else None))}

    # ---- the formula -----------------------------------------------------
    if t == "formula":
        wired = {"a": get("a"), "b": get("b"), "c": get("c"), "d": get("d")}

        def env(name):
            if name in wired:
                v = wired[name]
                return None if v is None else _series(v, n)
            if name in _FIELD_COL:
                return _src(ctx, name)
            return None

        return {"out": ("num", _eval_formula(compile_formula(p["expr"]), env, n))}

    # ---- the symbol universe ---------------------------------------------
    if t == "universe":
        m = ctx["meta"]
        ok = True
        for pid in ("market", "panel", "group"):
            want = _csv(p.get(pid, ""))
            if want:
                have = m.get(pid) or ""
                ok = ok and any(w in have for w in want)
        want = _csv(p.get("tickers", ""))
        if want:
            ok = ok and (m.get("ticker") or "").strip() in want
        return {"out": ("bool", [ok] * n)}

    # ---- arithmetic ------------------------------------------------------
    if t == "ifelse":
        cond, a, bb = get("cond"), get("a"), get("b")
        if cond is None or a is None or bb is None:
            return {"out": ("num", [None] * n)}
        flags = _bools(cond, n)
        sa, sb = _series(a, n), _series(bb, n)
        return {"out": ("num", [None if flags[i] is None else (sa[i] if flags[i] else sb[i])
                                for i in range(n)])}

    if t == "math":
        a, bb = get("a"), get("b")
        if a is None or bb is None:
            return {"out": ("num", [None] * n)}
        # Two constants stay a constant — a graph of thresholds costs nothing.
        if a[0] == "const" and bb[0] == "const":
            try:
                v = _MATH[p["op"]](a[1], bb[1])
            except (ZeroDivisionError, ValueError, OverflowError):
                return {"out": ("num", [None] * n)}
            return {"out": ("const", v)}
        return {"out": ("num", _binary(_series(a, n), _series(bb, n), _MATH[p["op"]]))}

    if t == "unary":
        a = get("a")
        if a is None:
            return {"out": ("num", [None] * n)}
        fn = _UNARY[p["op"]]
        k = p.get("scale", 1.0)
        return {"out": ("num", _unary(_series(a, n), lambda x: fn(x) * k))}

    # ---- rolling statistics ----------------------------------------------
    if t == "agg":
        a = get("a")
        if a is None:
            return {"out": ("num", [None] * n)}
        return {"out": ("num", _roll(_series(a, n), p["n"], p["op"]))}

    if t == "change":
        a = get("a")
        if a is None:
            return {"out": ("num", [None] * n)}
        s = _series(a, n)
        prev = _shift(s, p["n"])
        if p["op"] == "SHIFT":
            return {"out": ("num", prev)}
        if p["op"] == "DIFF":
            return {"out": ("num", _binary(s, prev, lambda x, y: x - y))}
        return {"out": ("num", _binary(s, prev,
                                       lambda x, y: (x - y) / y * 100.0 if y else None))}

    if t == "counttrue":
        a = get("a")
        if a is None:
            return {"out": ("num", [None] * n)}
        bl = _bools(a, n)
        out = [None] * n
        win, hits = p["n"], 0
        for i in range(n):
            if bl[i]:
                hits += 1
            if i >= win and bl[i - win]:
                hits -= 1
            out[i] = float(hits)
        return {"out": ("num", out)}

    if t == "percentile":
        a = get("a")
        if a is None:
            return {"out": ("num", [None] * n)}
        return {"out": ("num", _percentile_series(_series(a, n), p["n"]))}

    if t in ("streak", "barssince"):
        a = get("a")
        if a is None:
            return {"out": ("num", [None] * n)}
        flags = _bools(a, n)
        fn = _streak_series if t == "streak" else _barssince_series
        return {"out": ("num", fn(flags))}

    # ---- comparison ------------------------------------------------------
    if t == "compare":
        a, bb = get("a"), get("b")
        if a is None or bb is None:
            return {"out": ("bool", [None] * n)}
        sa, sb = _series(a, n), _series(bb, n)
        tol = p.get("tol", 0.0) / 100.0
        op = p["op"]
        out = [None] * n
        for i in range(n):
            x, y = sa[i], sb[i]
            if x is None or y is None:
                continue
            if op == ">":
                out[i] = x > y
            elif op == "<":
                out[i] = x < y
            elif op == ">=":
                out[i] = x >= y
            elif op == "<=":
                out[i] = x <= y
            else:
                # «برابر» on floats is never exact, so equality is a band whose
                # width the user sets («رواداری»). At 0 it falls back to an exact
                # comparison, which is what an integer field like `count` wants.
                near = abs(x - y) <= abs(y) * tol if tol else x == y
                out[i] = near if op == "=" else not near
        return {"out": ("bool", out)}

    if t == "between":
        a = get("a")
        if a is None:
            return {"out": ("bool", [None] * n)}
        lo, hi = min(p["lo"], p["hi"]), max(p["lo"], p["hi"])
        return {"out": ("bool", [None if x is None else (lo <= x <= hi)
                                 for x in _series(a, n)])}

    if t == "cross":
        a, bb = get("a"), get("b")
        if a is None or bb is None:
            return {"out": ("bool", [None] * n)}
        return {"out": ("bool", _cross(_series(a, n), _series(bb, n), p["op"]))}

    if t == "slope":
        a = get("a")
        if a is None:
            return {"out": ("bool", [None] * n)}
        s = _series(a, n)
        prev = _shift(s, p["n"])
        rising = p["op"] == "Rising"
        return {"out": ("bool", [None if (x is None or y is None)
                                 else (x > y if rising else x < y)
                                 for x, y in zip(s, prev)])}

    # ---- logic -----------------------------------------------------------
    if t in ("and", "or", "atleast"):
        vals = get("in", all_of=True)
        if not vals:
            return {"out": ("bool", [None] * n)}
        cols = [_bools(v, n) for v in vals]
        need = p["n"] if t == "atleast" else (len(cols) if t == "and" else 1)
        out = [None] * n
        for i in range(n):
            seen = [c[i] for c in cols]
            if any(x is None for x in seen):
                out[i] = None if t == "and" else (True if sum(1 for x in seen if x) >= need else None)
                continue
            out[i] = sum(1 for x in seen if x) >= need
        return {"out": ("bool", out)}

    if t == "not":
        a = get("a")
        if a is None:
            return {"out": ("bool", [None] * n)}
        return {"out": ("bool", [None if x is None else (not x) for x in _bools(a, n)])}

    if t == "inlist":
        # Split on commas AND the Persian comma, and on newlines — a list pasted
        # out of a broker's watchlist arrives in whichever of the three the
        # source happened to use.
        raw = p["value"].replace("،", ",").replace("\n", ",")
        wanted = {x.strip() for x in raw.split(",") if x.strip()}
        hit = (ctx["meta"].get("ticker") or "").strip() in wanted if wanted else False
        return {"out": ("bool", [hit] * n)}

    if t == "bars":
        return {"out": ("const", float(n))}

    # ---- text ------------------------------------------------------------
    if t == "textmatch":
        a = get("a")
        txt = (a[1] if (a and a[0] == "text") else "")
        want = p["value"].strip()
        if not want:
            hit = True
        elif p["op"] == "eq":
            hit = txt.strip() == want
        elif p["op"] == "ncontains":
            hit = want not in txt
        else:
            hit = want in txt
        return {"out": ("bool", [hit] * n)}

    # ---- sinks -----------------------------------------------------------
    # «توضیحات» is here rather than special-cased in normalise(): a note is a
    # node like any other, it simply computes nothing. That keeps it draggable,
    # selectable, duplicable and undoable for free.
    if t in ("output", "column", "note", "signal", "alert"):
        return {}

    raise GraphError(f"نود پیاده‌سازی‌نشده: {t}")


_MATH = {
    "+": lambda x, y: x + y,
    "-": lambda x, y: x - y,
    "*": lambda x, y: x * y,
    "/": lambda x, y: x / y if y else None,
    "C%": lambda x, y: (x - y) / y * 100.0 if y else None,
    "%b": lambda x, y: x / y * 100.0 if y else None,
    "min": min,
    "max": max,
    # Guarded: 0**-1 raises and (-8)**0.5 returns a complex number, and a chip
    # that returns a complex would poison every comparison downstream.
    "^": lambda x, y: (x ** y) if (x > 0 or float(y).is_integer()) else None,
}

_UNARY = {
    "abs": abs,
    "neg": lambda x: -x,
    "sqrt": lambda x: math.sqrt(x) if x >= 0 else None,
    "ln": lambda x: math.log(x) if x > 0 else None,
    "log10": lambda x: math.log10(x) if x > 0 else None,
    "round": lambda x: float(round(x)),
    "floor": lambda x: float(math.floor(x)),
    "ceil": lambda x: float(math.ceil(x)),
    "sign": lambda x: 1.0 if x > 0 else (-1.0 if x < 0 else 0.0),
}



#: The reserved column id the «برچسب سیگنال» blocks share. Reserved rather than
#: generated because the result table needs ONE «سیگنال» column however many
#: signal blocks are on the canvas — four columns of mostly-blank text is not
#: what a user who wired a buy and a sell rule was asking for.
SIGNAL_COL = "__signal"


def evaluate(nodes, order, out_node, columns, bars, meta,
             kind="stock", signals=()):
    """Run the graph for ONE symbol. Returns (matched, {column_id: value}, at,
    [signal labels]).

    `matched` is True when the output condition held on any of the last
    `within` bars — the Asan-Bourse semantics, and the reason a filter written
    as «تقاطع» does not have to fire on exactly today's candle to be useful.

    `at` is how many bars back that happened (0 = the last candle). Without it
    «چرا این نماد آمد؟» is unreadable for exactly the filters that need it most:
    a crossing that fired three sessions ago is False today, so every chip on
    the canvas would be painted ✕ next to a symbol the filter had just
    returned."""
    n = len(bars["c"])
    ctx = {"bars": bars, "n": n, "meta": meta, "kind": kind, "frames": {}}
    memo = _memoise(nodes, order, ctx)

    cond = None
    bucket = out_node["ins"].get("in") or []
    if bucket:
        src, sport = bucket[0]
        cond = memo.get(src, {}).get(sport)
    if cond is None:
        return False, {}, 0, []
    flags = _bools(cond, n)
    within = min(out_node["params"]["within"], n)
    at = None
    for back in range(within):                 # the MOST RECENT firing wins
        if flags[n - 1 - back]:
            at = back
            break
    if at is None:
        return False, {}, 0, []

    def wired(sink, port="a"):
        b2 = sink["ins"].get(port) or []
        if not b2:
            return None
        src2, sport2 = b2[0]
        return memo.get(src2, {}).get(sport2)

    values = {}
    for col in columns:
        v = wired(col)
        if v is None:
            continue
        series = _series(v, n)
        # The column reports the value ON THE BAR THAT MATCHED, so a row saying
        # «RSI ۲۸» is the RSI that satisfied the filter rather than whatever it
        # has drifted to since.
        last = series[n - 1 - at]
        if last is None:
            last = next((x for x in reversed(series) if x is not None), None)
        values[col["id"]] = last

    # Signals are read on the SAME bar, for the same reason: a row that matched
    # three sessions ago is labelled with what it was three sessions ago.
    labels = []
    for sig in signals:
        v = wired(sig, "in")
        if v is None:
            continue
        if _bools(v, n)[n - 1 - at]:
            label = _SIGNAL_LABELS.get(sig["params"]["signal"])
            if label and label not in labels:
                labels.append(label)
    return True, values, at, labels


# ---------------------------------------------------------------------------
# The market-wide run
# ---------------------------------------------------------------------------
def run(graph, kind="stock", as_of=None, group=None, sub_group=None, limit=MAX_ROWS):
    """Evaluate `graph` against every symbol of `kind` and return the matches.

    Raises GraphError with a Persian message for anything the user can fix."""
    if kind not in ("stock", "etf"):
        kind = "stock"
    nodes, edges, order, out_node = normalise(graph)
    columns = [n for n in nodes.values() if n["type"] == "column"]
    signals = [n for n in nodes.values() if n["type"] == "signal"]
    bars = bars_needed(nodes, out_node)
    # REPORTED, not silently clamped. A monthly 200-period average wants 4400
    # sessions and gets 1300, and every value it produces is None — which
    # reaches the user as an empty table that looks exactly like a strict
    # filter. `clipped` is what lets the result panel say which one it was.
    clipped = _raw_bars_needed(nodes, out_node) > _BAR_BUCKETS[-1]
    fields = fields_needed(nodes)

    if as_of is None:
        as_of = db.latest_date(kind)
    if as_of is None:
        return {"as_of": None, "rows": [], "scanned": 0, "count": 0,
                "columns": [], "bars": bars, "clipped": clipped}

    panel = _panel(kind, as_of, bars, fields)
    meta = _meta(kind)
    fallback_mode = out_node["params"].get("sort", "price")
    # An indicator needs its warm-up window; below that every value is None and
    # the symbol can only ever be a non-match, so it is not "scanned" either.
    min_bars = max(30, min(bars // 2, 60))

    rows = []
    scanned = 0
    errors = 0
    for ticker, series in panel.items():
        m = meta.get(ticker)
        if not m:
            continue
        if group and m.get("group") != group:
            continue
        if sub_group and m.get("sub_group") != sub_group:
            continue
        if len(series["c"]) < min_bars:
            continue
        scanned += 1
        try:
            ok, values, at, labels = evaluate(nodes, order, out_node, columns,
                                              series, m, kind, signals)
        except GraphError:
            raise
        except Exception:                      # one bad symbol must not kill the run
            errors += 1
            if errors < 4:
                log.warning("designer: symbol failed", extra={"ticker": ticker},
                            exc_info=True)
            continue
        if not ok:
            continue
        if signals:
            values[SIGNAL_COL] = "، ".join(labels)
        rows.append({"id": m["id"], "ticker": ticker, "name": m["name"],
                     "group": m.get("group"), "latest": series["f"][-1],
                     "vals": values, "at": at,
                     "_k": _fallback_key(series, fallback_mode)})

    col_meta = [{"id": c["id"], "label": c["params"]["label"] or "مقدار",
                 "digits": c["params"]["digits"], "type": "num",
                 "sort": c["params"].get("sort", "desc")} for c in columns]
    if signals:
        col_meta.insert(0, {"id": SIGNAL_COL, "label": "سیگنال", "digits": 0,
                            "sort": "none", "type": "text"})
    # Sorted by the first column that ASKS to be sorted. «فاصله تا سقف» wants
    # ascending and «حجم نسبی» wants descending, and before the column carried
    # its own direction the answer was always "biggest first" — which put the
    # least interesting rows at the top of half the filters people write.
    # Missing values sink either way: an em dash at the top of a ranked list is
    # never the answer to the question that produced the ranking.
    order = next((c for c in col_meta if c["sort"] in ("asc", "desc")), None)
    if order:
        key, sign = order["id"], (1.0 if order["sort"] == "asc" else -1.0)
        rows.sort(key=lambda r: (r["vals"].get(key) is None,
                                 sign * (r["vals"].get(key) or 0.0)))
    elif fallback_mode == "ticker":
        rows.sort(key=lambda r: r["ticker"])
    else:
        # «مرتب‌سازی» on the output block, defaulting to «قیمت پایانی» — the
        # order this table had before the block carried the setting at all.
        rows.sort(key=lambda r: -(r["_k"] or 0.0))
    for r in rows:
        del r["_k"]

    # `errors` is REPORTED, not just logged. A per-symbol exception is caught so
    # one bad symbol cannot kill a market-wide run — but swallowing it silently
    # turns a broken block into "this filter matches nothing", which is
    # indistinguishable from a strict filter and impossible to test for. With
    # the count in the payload, verify_designer.py can assert it is zero.
    return {"as_of": as_of, "rows": rows[:limit], "count": len(rows),
            "scanned": scanned, "columns": col_meta, "bars": bars,
            "errors": errors, "truncated": len(rows) > limit, "clipped": clipped}


def _fallback_key(series, mode):
    """The number «مرتب‌سازی» on the output block ranks by, for one symbol."""
    if mode == "volume":
        return (series.get("v") or [0.0])[-1]
    if mode == "value":
        return (series.get("val") or [0.0])[-1]
    if mode == "pct":
        f = series["f"]
        return (f[-1] / f[-2] - 1.0) * 100.0 if len(f) > 1 and f[-2] else 0.0
    return series["f"][-1]


def explain(graph, kind, ticker, as_of=None, tail=12):        # noqa: D417
    """Every node's value over the last `tail` bars for ONE symbol.

    This is «چرا این نماد آمد؟». A screener that only says yes or no is
    impossible to debug: the user changes a threshold, the list empties, and
    nothing on the screen says which of the eleven conditions went false. This
    returns the per-node truth so the canvas can colour each chip."""
    nodes, edges, order, out_node = normalise(graph)
    columns = [n for n in nodes.values() if n["type"] == "column"]
    bars = bars_needed(nodes, out_node)
    # Long enough that the bar the filter fired on is always inside the window
    # the browser is shown — otherwise the ✓ the canvas is trying to explain
    # sits just off the left edge of the strip.
    tail = max(tail, min(out_node["params"]["within"] + 2, MAX_WITHIN + 2))
    if as_of is None:
        as_of = db.latest_date(kind)
    panel = _panel(kind, as_of, bars, fields_needed(nodes))
    series = panel.get(ticker)
    meta = _meta(kind).get(ticker)
    if not series or not meta:
        raise GraphError(f"نماد «{ticker}» در این بازه داده ندارد.")

    n = len(series["c"])
    ctx = {"bars": series, "n": n, "meta": meta, "kind": kind, "frames": {}}
    memo = _memoise(nodes, order, ctx)

    out = {}
    for nid, ports in memo.items():
        for port, val in ports.items():
            if val is None:
                continue
            k, payload = val
            if k == "text":
                out[f"{nid}:{port}"] = {"kind": "text", "value": payload}
            elif k == "const":
                out[f"{nid}:{port}"] = {"kind": "const", "value": payload}
            elif k == "bool":
                out[f"{nid}:{port}"] = {"kind": "bool", "tail": payload[-tail:]}
            else:
                out[f"{nid}:{port}"] = {"kind": "num", "tail": payload[-tail:]}

    signals = [x for x in nodes.values() if x["type"] == "signal"]
    ok, values, at, labels = evaluate(nodes, order, out_node, columns, series,
                                      meta, kind, signals)
    return {"ticker": ticker, "name": meta["name"], "as_of": as_of,
            "signals": labels,
            "matched": ok, "at": at, "ports": out, "bars": min(tail, n),
            "within": out_node["params"]["within"]}


# ---------------------------------------------------------------------------
# Saved filters («فیلترهای من»)
# ---------------------------------------------------------------------------
def ensure_tables():
    """Create `custom_filters` if absent. Called from app boot next to
    db.init_db(), for the same reason that one is: `python app.py` has to work
    against a database nobody has run Alembic on."""
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS custom_filters (
                    id          BIGSERIAL PRIMARY KEY,
                    user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    name        TEXT NOT NULL,
                    kind        TEXT NOT NULL DEFAULT 'stock',
                    description TEXT NOT NULL DEFAULT '',
                    graph       JSONB NOT NULL,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    -- Two filters with one name is a mistake, not a preference:
                    -- the picker shows the name and nothing else.
                    UNIQUE (user_id, name)
                )
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_custom_filters_user "
                        "ON custom_filters(user_id, updated_at DESC)")
            # Migration 0007. ADD COLUMN IF NOT EXISTS so this is also the
            # upgrade path for a database that already had the 0006 table.
            cur.execute("ALTER TABLE custom_filters "
                        "ADD COLUMN IF NOT EXISTS alert_jd TEXT")
        conn.commit()
    finally:
        db.release(conn)


def list_filters(user_id):
    """The picker's rows. `alert` says whether this filter carries an «هشدار»
    block, so the list can show which of them are watching the market — a user
    with nine saved filters has no other way to tell, and «چرا این نماد اعلان
    داد؟» starts with knowing which filter is armed."""
    return db._rows(
        """SELECT id, name, kind, description, created_at, updated_at,
                  (graph @> %s) AS alert
           FROM custom_filters WHERE user_id = %s ORDER BY updated_at DESC""",
        (_ALERT_PROBE, user_id))


def get_filter(user_id, filter_id):
    return db._one(
        """SELECT id, name, kind, description, graph, created_at, updated_at
           FROM custom_filters WHERE id = %s AND user_id = %s""",
        (filter_id, user_id))


def save_filter(user_id, name, kind, graph, description="", filter_id=None):
    """Insert or update one saved filter. The graph is stored as JSONB verbatim
    — including the node COORDINATES, because a filter the user cannot reopen
    and rearrange is a black box, not a design."""
    import json
    name = (name or "").strip()[:80]
    if not name:
        raise GraphError("نام فیلتر را وارد کنید.")
    if kind not in ("stock", "etf"):
        kind = "stock"
    # Validated before storage: a graph that cannot run must never make it into
    # the table, or the picker fills up with entries that fail on open.
    normalise(graph)
    payload = json.dumps(graph, ensure_ascii=False)
    now = db._utcnow()
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            if filter_id:
                cur.execute(
                    """UPDATE custom_filters
                       SET name=%s, kind=%s, description=%s, graph=%s,
                           updated_at=%s, alert_jd=NULL
                       WHERE id=%s AND user_id=%s RETURNING id""",
                    (name, kind, description[:400], payload, now, filter_id, user_id))
                row = cur.fetchone()
                if not row:
                    raise GraphError("این فیلتر پیدا نشد.")
                new_id = row[0]
            else:
                cur.execute(
                    """INSERT INTO custom_filters
                           (user_id, name, kind, description, graph, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (user_id, name) DO UPDATE
                           SET kind=EXCLUDED.kind, description=EXCLUDED.description,
                               graph=EXCLUDED.graph, updated_at=EXCLUDED.updated_at,
                               -- Re-arm on edit: what the OLD graph matched says
                               -- nothing about what this one matches.
                               alert_jd=NULL
                       RETURNING id""",
                    (user_id, name, kind, description[:400], payload, now, now))
                new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    finally:
        db.release(conn)


def delete_filter(user_id, filter_id):
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM custom_filters WHERE id=%s AND user_id=%s",
                        (filter_id, user_id))
            n = cur.rowcount
        conn.commit()
        return n
    finally:
        db.release(conn)


# ---------------------------------------------------------------------------
# «بلاک هشدار» — saved filters that watch the market for themselves
#
# A designed filter is a market-wide scan, so this cannot work the way
# db.evaluate_alerts() does (one rule, one symbol, one number). It runs the
# whole graph and reports the symbols that are NEWLY in the result — which is
# the only thing worth an interruption. A filter that has matched «فولاد» for
# nine sessions running should say so once.
#
# The de-duplication state lives in `alert_events` rather than in a table of its
# own, keyed by the rule string. That is deliberate: an alert is only ever
# interesting because an event was written, so the events ARE the state, and
# there is nothing that can drift out of step with them.
# ---------------------------------------------------------------------------
#: The jsonb containment probe that finds filters carrying an «هشدار» block.
#: Postgres answers it from the graph column directly, so arming a filter is
#: something the user does by dragging a box onto the canvas and saving —
#: there is no second switch somewhere else to forget.
_ALERT_PROBE = '{"nodes": [{"type": "alert"}]}'

#: How many alerting filters one pass will run. Each is a market-wide scan, so
#: an unbounded loop here is an unbounded nightly job; the rest wait for the
#: next pass rather than making the update task run long.
FILTER_ALERT_MAX = int(os.environ.get("FILTER_ALERT_MAX", "40"))


def _alert_rule(filter_id):
    """The `rule` string an event carries. Also the de-duplication key."""
    return "filter:%d" % filter_id


def alerting_filters():
    """Every saved filter with an «هشدار» block, oldest first."""
    return db._rows(
        """SELECT id, user_id, name, kind, graph, alert_jd FROM custom_filters
           WHERE graph @> %s ORDER BY updated_at DESC LIMIT %s""",
        (_ALERT_PROBE, FILTER_ALERT_MAX))


def _alert_params(graph):
    """The «هشدار» block's own settings, out of the stored graph."""
    for node in (graph or {}).get("nodes") or []:
        if isinstance(node, dict) and node.get("type") == "alert":
            p = node.get("params") or {}
            return (str(p.get("once", "1")) != "0",
                    str(p.get("msg") or "")[:200])
    return True, ""


def _session_jdate(kind):
    """The Jalali date of the last stored session — what an event is stamped
    with, and what stops a second pass on the same day from repeating itself."""
    table = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    row = db._one("SELECT j_date FROM %s ORDER BY date DESC LIMIT 1" % table)
    return (row or {}).get("j_date")


def evaluate_filter_alerts(limit_rows=60):
    """Run every armed filter and record the symbols that are newly in it.

    Returns the same shape as db.evaluate_alerts() so the task that calls both
    can log one summary. Never raises: one user's broken graph must not stop
    every other user's alerts, so a failure is counted and logged.
    """
    fired, checked, skipped = [], 0, 0
    for f in alerting_filters():
        rule = _alert_rule(f["id"])
        once, template = _alert_params(f["graph"])
        j_date = _session_jdate(f["kind"])
        if not j_date:
            continue
        # ALREADY RUN for this trading session. This task fires on every update
        # AND every three hours as a safety net, and each armed filter is a
        # market-wide scan of eight hundred symbols — while the data only moves
        # once a day. Without this marker the common outcome ("the same symbols
        # as this morning, so nothing new to report") is indistinguishable from
        # "never ran", and the scan repeats eight times a day to write nothing
        # eight times.
        if f.get("alert_jd") == j_date:
            skipped += 1
            continue
        checked += 1

        # What we have already told this user about, for this filter. With
        # «یک‌بار» that is every ticker ever reported; otherwise it is only the
        # ones reported for THIS session, so a filter that stays true reports
        # again tomorrow but not twice today.
        seen = {r["ticker"] for r in db._rows(
            "SELECT DISTINCT ticker FROM alert_events WHERE user_id=%s AND rule=%s"
            + ("" if once else " AND j_date=%s"),
            (f["user_id"], rule) if once else (f["user_id"], rule, j_date))}

        try:
            out = run(f["graph"], kind=f["kind"], limit=limit_rows)
        except GraphError as e:
            log.warning("filter alert %s: %s", f["id"], e)
            continue
        except Exception:
            log.exception("filter alert %s failed", f["id"])
            continue

        rows = [r for r in out["rows"] if r["ticker"] not in seen]
        # The marker is written even when nothing matched — "evaluated, nothing
        # new" is exactly the outcome it exists to record. Same transaction as
        # the events, so a crash between the two cannot lose the notifications
        # and then mark the session done.
        conn = db.get_db()
        try:
            with conn.cursor() as cur:
                for r in rows:
                    cur.execute(
                        """INSERT INTO alert_events
                             (alert_id, user_id, kind, ticker, rule, value,
                              j_date, message, fired_at)
                           VALUES (NULL,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (f["user_id"], f["kind"], r["ticker"], rule,
                         float(r["latest"] or 0.0), j_date,
                         _alert_text(template, f["name"], r), db._utcnow()))
                cur.execute("UPDATE custom_filters SET alert_jd=%s WHERE id=%s",
                            (j_date, f["id"]))
            conn.commit()
        finally:
            db.release(conn)
        fired.extend({"filter_id": f["id"], "ticker": r["ticker"]} for r in rows)
    return {"checked": checked, "fired": len(fired), "skipped": skipped,
            "events": fired}


def _alert_text(template, filter_name, row):
    """The notification line. The template is USER text, so the placeholders are
    substituted by replacement rather than by str.format — `{0.__class__}` in a
    format string is a way to walk the object graph, and there is no version of
    this feature that needs it."""
    text = (template or "").strip() or "«{ticker}» در فیلتر «{filter}» آمد."
    for key, val in (("{ticker}", row["ticker"]), ("{name}", row["name"] or ""),
                     ("{filter}", filter_name)):
        text = text.replace(key, val)
    return text[:300]


# ---------------------------------------------------------------------------
# Ready-made examples («فایل‌های آماده»)
#
# An empty canvas is the reason node editors get abandoned. These are the four
# graphs from the reference product's own gallery, laid out so they open
# readable, and they double as the worked examples in the help text.
# ---------------------------------------------------------------------------
def _node(nid, ntype, x, y, **params):
    return {"id": nid, "type": ntype, "x": x, "y": y, "params": params}


def _edge(a, b, to_port="in", from_port="out"):
    return {"from": a, "fromPort": from_port, "to": b, "toPort": to_port}


def _bullish_engulfing():
    """کندل پوشای صعودی — the reference product's own first example, wire for
    wire: yesterday red, today green, today's body covering yesterday's."""
    nodes = [
        _node("p1", "price", 40, 40, field="close", shift=1),
        _node("p2", "price", 40, 130, field="open", shift=1),
        _node("c1", "compare", 250, 80, op="<"),          # close-1 < open-1  (red)
        _node("p3", "price", 40, 230, field="close", shift=0),
        _node("p4", "price", 40, 320, field="open", shift=0),
        _node("c2", "compare", 250, 270, op=">"),         # close > open      (green)
        _node("p5", "price", 40, 420, field="close", shift=0),
        _node("p6", "price", 40, 510, field="open", shift=1),
        _node("c3", "compare", 250, 460, op=">"),         # close > open-1
        _node("p7", "price", 40, 610, field="open", shift=0),
        _node("p8", "price", 40, 700, field="close", shift=1),
        _node("c4", "compare", 250, 650, op="<"),         # open  < close-1
        _node("v1", "price", 40, 800, field="volume", shift=0),
        _node("v2", "sma", 40, 890, n=20, src="volume", shift=1),
        _node("c5", "compare", 250, 840, op=">"),         # حجم بالای میانگین
        _node("and1", "and", 520, 420),
        _node("out", "output", 720, 420, within=1),
    ]
    edges = [
        _edge("p1", "c1", "a"), _edge("p2", "c1", "b"),
        _edge("p3", "c2", "a"), _edge("p4", "c2", "b"),
        _edge("p5", "c3", "a"), _edge("p6", "c3", "b"),
        _edge("p7", "c4", "a"), _edge("p8", "c4", "b"),
        _edge("v1", "c5", "a"), _edge("v2", "c5", "b"),
        _edge("c1", "and1"), _edge("c2", "and1"), _edge("c3", "and1"),
        _edge("c4", "and1"), _edge("c5", "and1"),
        _edge("and1", "out"),
    ]
    return {"nodes": nodes, "edges": edges}


def _golden_cross():
    nodes = [
        _node("s1", "sma", 40, 60, n=50, src="final", shift=0),
        _node("s2", "sma", 40, 150, n=200, src="final", shift=0),
        _node("x1", "cross", 280, 100, op="CrossUp"),
        _node("r1", "rsi", 40, 260, n=14, src="final", shift=0),
        _node("k1", "const", 40, 350, value=70),
        _node("c1", "compare", 280, 300, op="<"),
        _node("and1", "and", 520, 200),
        _node("out", "output", 720, 200, within=5),
        _node("col", "column", 720, 300, label="RSI", digits=1),
    ]
    edges = [
        _edge("s1", "x1", "a"), _edge("s2", "x1", "b"),
        _edge("r1", "c1", "a"), _edge("k1", "c1", "b"),
        _edge("x1", "and1"), _edge("c1", "and1"),
        _edge("and1", "out"), _edge("r1", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _ichimoku_breakout():
    nodes = [
        _node("p1", "price", 40, 60, field="final", shift=0),
        _node("i1", "ichimoku", 40, 150, tenkan=9, kijun=26, spanb=52),
        _node("c1", "compare", 300, 60, op=">"),        # final > سنکو A
        _node("c2", "compare", 300, 160, op=">"),       # final > سنکو B
        _node("i2", "ichimoku", 40, 300, tenkan=9, kijun=26, spanb=52),
        _node("x1", "cross", 300, 300, op="CrossUp"),   # تنکان از کیجون
        _node("a1", "adx", 40, 430, n=14),
        _node("k1", "const", 40, 520, value=20),
        _node("c3", "compare", 300, 460, op=">"),
        _node("and1", "and", 560, 250),
        _node("out", "output", 760, 250, within=3),
    ]
    edges = [
        _edge("p1", "c1", "a"), _edge("i1", "c1", "b", from_port="spana"),
        _edge("p1", "c2", "a"), _edge("i1", "c2", "b", from_port="spanb"),
        _edge("i2", "x1", "a", from_port="tenkan"),
        _edge("i2", "x1", "b", from_port="kijun"),
        _edge("a1", "c3", "a", from_port="adx"), _edge("k1", "c3", "b"),
        _edge("c1", "and1"), _edge("c2", "and1"), _edge("x1", "and1"), _edge("c3", "and1"),
        _edge("and1", "out"),
    ]
    return {"nodes": nodes, "edges": edges}


def _near_year_high():
    nodes = [
        _node("p1", "price", 40, 60, field="final", shift=0),
        _node("h1", "price", 40, 150, field="high", shift=0),
        _node("g1", "agg", 280, 150, op="MAX", n=240),
        _node("m1", "math", 520, 100, op="C%"),
        _node("k1", "const", 40, 300, value=-5),
        _node("c1", "compare", 760, 150, op=">="),
        _node("v1", "price", 40, 400, field="value", shift=0),
        _node("k2", "const", 40, 490, value=10000000000),
        _node("c2", "compare", 280, 440, op=">"),
        _node("and1", "and", 1000, 250),
        _node("out", "output", 1180, 250, within=1),
        _node("col", "column", 1180, 350, label="فاصله تا سقف ٪", digits=2),
    ]
    edges = [
        _edge("h1", "g1", "a"),
        _edge("p1", "m1", "a"), _edge("g1", "m1", "b"),
        _edge("m1", "c1", "a"), _edge("k1", "c1", "b"),
        _edge("v1", "c2", "a"), _edge("k2", "c2", "b"),
        _edge("c1", "and1"), _edge("c2", "and1"),
        _edge("and1", "out"), _edge("m1", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _rsi_oversold_bounce():
    nodes = [
        _node("r1", "rsi", 40, 60, n=14, src="final", shift=0),
        _node("k1", "const", 40, 150, value=30),
        _node("x1", "cross", 280, 100, op="CrossUp"),
        _node("p1", "price", 40, 250, field="final", shift=0),
        _node("b1", "boll", 40, 340, n=20, k=2.0, src="final"),
        _node("c1", "compare", 280, 290, op=">"),
        _node("and1", "and", 540, 190),
        _node("out", "output", 740, 190, within=3),
        _node("col", "column", 740, 290, label="RSI", digits=1),
    ]
    edges = [
        _edge("r1", "x1", "a"), _edge("k1", "x1", "b"),
        _edge("p1", "c1", "a"), _edge("b1", "c1", "b", from_port="lower"),
        _edge("x1", "and1"), _edge("c1", "and1"),
        _edge("and1", "out"), _edge("r1", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _squeeze_breakout():
    """Volatility compressed, then price leaves the channel on real volume."""
    nodes = [
        _node("sq", "squeeze", 40, 60, n=20, k=2.0, mult=1.5, src="final"),
        _node("p1", "price", 40, 160, field="final", shift=0),
        _node("d1", "donchian", 40, 250, n=20),
        _node("c1", "compare", 300, 200, op=">"),
        _node("rv", "relvol", 40, 360, n=20, src="volume"),
        _node("k1", "const", 40, 450, value=1.5),
        _node("c2", "compare", 300, 400, op=">"),
        _node("and1", "and", 560, 220),
        _node("out", "output", 760, 220, within=3),
        _node("col", "column", 760, 330, label="حجم نسبی", digits=2, sort="desc"),
    ]
    edges = [
        _edge("p1", "c1", "a"), _edge("d1", "c1", "b", from_port="upper"),
        _edge("rv", "c2", "a"), _edge("k1", "c2", "b"),
        _edge("sq", "and1"), _edge("c1", "and1"), _edge("c2", "and1"),
        _edge("and1", "out"), _edge("rv", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _hammer_confirmed():
    """A candlestick reversal that the tape agrees with — the pattern alone is
    the single most over-traded signal on this exchange."""
    nodes = [
        _node("h1", "candle", 40, 60, pattern="hammer", shift=0),
        _node("rv", "relvol", 40, 160, n=20, src="volume"),
        _node("k1", "const", 40, 250, value=1.2),
        _node("c1", "compare", 300, 200, op=">"),
        _node("r1", "rsi", 40, 360, n=14, src="final", shift=0),
        _node("k2", "const", 40, 450, value=45),
        _node("c2", "compare", 300, 400, op="<"),
        _node("and1", "and", 560, 220),
        _node("out", "output", 760, 220, within=3),
        _node("col", "column", 760, 330, label="RSI", digits=1, sort="asc"),
    ]
    edges = [
        _edge("rv", "c1", "a"), _edge("k1", "c1", "b"),
        _edge("r1", "c2", "a"), _edge("k2", "c2", "b"),
        _edge("h1", "and1"), _edge("c1", "and1"), _edge("c2", "and1"),
        _edge("and1", "out"), _edge("r1", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _strong_trend():
    """A trend measured rather than eyeballed: a positive regression slope, an
    R² that says the move really was a trend, and ADX to confirm it."""
    nodes = [
        _node("lr", "linreg", 40, 60, n=30, src="final"),
        _node("k0", "const", 40, 150, value=0),
        _node("c0", "compare", 300, 100, op=">"),
        _node("k1", "const", 40, 250, value=0.7),
        _node("c1", "compare", 300, 200, op=">"),
        _node("a1", "adx", 40, 360, n=14),
        _node("k2", "const", 40, 450, value=25),
        _node("c2", "compare", 300, 400, op=">"),
        _node("and1", "and", 560, 220),
        _node("out", "output", 760, 220, within=1),
        _node("col", "column", 760, 330, label="R²", digits=2, sort="desc"),
        _node("col2", "column", 760, 420, label="ADX", digits=1, sort="none"),
    ]
    edges = [
        _edge("lr", "c0", "a", from_port="slope"), _edge("k0", "c0", "b"),
        _edge("lr", "c1", "a", from_port="r2"), _edge("k1", "c1", "b"),
        _edge("a1", "c2", "a", from_port="adx"), _edge("k2", "c2", "b"),
        _edge("c0", "and1"), _edge("c1", "and1"), _edge("c2", "and1"),
        _edge("and1", "out"),
        _edge("lr", "col", "a", from_port="r2"),
        _edge("a1", "col2", "a", from_port="adx"),
    ]
    return {"nodes": nodes, "edges": edges}


def _money_flowing_in():
    """Accumulation, in the only three ways this database can see it: money
    flow positive, volume above its own average, price above rolling VWAP."""
    nodes = [
        _node("cm", "cmf", 40, 60, n=20),
        _node("k0", "const", 40, 150, value=0.05),
        _node("c0", "compare", 300, 100, op=">"),
        _node("rv", "relvol", 40, 250, n=20, src="value"),
        _node("k1", "const", 40, 340, value=2),
        _node("c1", "compare", 300, 290, op=">"),
        _node("p1", "price", 40, 440, field="final", shift=0),
        _node("vw", "vwap", 40, 530, n=20),
        _node("c2", "compare", 300, 480, op=">"),
        _node("and1", "and", 560, 290),
        _node("out", "output", 760, 290, within=2),
        _node("col", "column", 760, 400, label="CMF", digits=3, sort="desc"),
    ]
    edges = [
        _edge("cm", "c0", "a"), _edge("k0", "c0", "b"),
        _edge("rv", "c1", "a"), _edge("k1", "c1", "b"),
        _edge("p1", "c2", "a"), _edge("vw", "c2", "b"),
        _edge("c0", "and1"), _edge("c1", "and1"), _edge("c2", "and1"),
        _edge("and1", "out"), _edge("cm", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _pullback_in_uptrend():
    """The classic entry: a symbol still in an uptrend, but temporarily oversold
    and sitting on a support level rather than in free fall."""
    nodes = [
        _node("p1", "price", 40, 60, field="final", shift=0),
        _node("s1", "sma", 40, 150, n=50, src="final", shift=0),
        _node("c1", "compare", 300, 100, op=">"),
        _node("sr", "stochrsi", 40, 260, n=14, ks=3, ds=3, src="final"),
        _node("k1", "const", 40, 350, value=25),
        _node("c2", "compare", 300, 300, op="<"),
        _node("p2", "price", 40, 460, field="final", shift=0),
        _node("pv", "pivot", 40, 550, mode="classic"),
        _node("c3", "compare", 300, 500, op=">"),
        _node("and1", "and", 560, 300),
        _node("out", "output", 760, 300, within=2),
        _node("col", "column", 760, 410, label="StochRSI %K", digits=1, sort="asc"),
    ]
    edges = [
        _edge("p1", "c1", "a"), _edge("s1", "c1", "b"),
        _edge("sr", "c2", "a", from_port="k"), _edge("k1", "c2", "b"),
        _edge("p2", "c3", "a"), _edge("pv", "c3", "b", from_port="s1"),
        _edge("c1", "and1"), _edge("c2", "and1"), _edge("c3", "and1"),
        _edge("and1", "out"), _edge("sr", "col", "a", from_port="k"),
    ]
    return {"nodes": nodes, "edges": edges}


def _queue_with_weekly_trend():
    """«صف خرید» is the single most-asked-for screen on this exchange and the
    single most useless one on its own: on any given session a hundred symbols
    sit on their ceiling and most of them are illiquid پایه names that will open
    down tomorrow. This is the version worth running — a queue on a real board,
    in a symbol whose WEEKLY trend agrees."""
    nodes = [
        _node("note", "note", 40, 20,
              text="صف خرید، اما فقط وقتی روند هفتگی هم صعودی است."),
        _node("lim", "pricelimit", 40, 130, band="auto", shift=0),
        _node("r1", "rsi", 40, 250, n=14, src="final", tf="W", shift=0),
        _node("k1", "const", 40, 350, value=50),
        _node("c1", "compare", 320, 280, op=">"),
        _node("and1", "and", 560, 200),
        _node("out", "output", 780, 200, within=1, sort="value"),
        _node("sig", "signal", 780, 300, signal="buy"),
        _node("col", "column", 780, 400, label="RSI هفتگی", digits=1, sort="desc"),
        _node("col2", "column", 780, 500, label="تا کف مجاز ٪", digits=1, sort="none"),
    ]
    edges = [
        _edge("r1", "c1", "a"), _edge("k1", "c1", "b"),
        _edge("lim", "and1", from_port="buyq"), _edge("c1", "and1"),
        _edge("and1", "out"), _edge("and1", "sig"),
        _edge("r1", "col", "a"), _edge("lim", "col2", "a", from_port="ddn"),
    ]
    return {"nodes": nodes, "edges": edges}


def _monthly_trend_daily_pullback():
    """Two time frames in one graph, which is the whole reason «تایم فریم»
    exists: the direction is decided on the slow frame and the entry on the fast
    one. A daily oversold reading inside a monthly downtrend is not a pullback,
    it is a falling knife, and this is the difference between the two."""
    nodes = [
        _node("note", "note", 40, 20,
              text="جهت از تایم‌فریم ماهانه، نقطهٔ ورود از روزانه."),
        _node("uni", "universe", 40, 120, market="بورس, فرابورس"),
        _node("p1", "price", 40, 220, field="final", tf="M", shift=0),
        _node("m1", "sma", 40, 320, n=10, method="sma", src="final", tf="M", shift=0),
        _node("c1", "compare", 320, 250, op=">"),
        _node("r1", "rsi", 40, 440, n=14, src="final", tf="D", shift=0),
        _node("k1", "const", 40, 540, value=50),
        _node("c2", "compare", 320, 470, op="<"),
        _node("p2", "price", 40, 640, field="final", shift=0),
        _node("m2", "sma", 40, 740, n=50, method="ema", src="final", shift=0),
        _node("c3", "compare", 320, 680, op=">"),
        _node("and1", "and", 580, 400),
        _node("out", "output", 800, 400, within=3, sort="value"),
        _node("col", "column", 800, 510, label="RSI روزانه", digits=1, sort="asc"),
    ]
    edges = [
        _edge("p1", "c1", "a"), _edge("m1", "c1", "b"),
        _edge("r1", "c2", "a"), _edge("k1", "c2", "b"),
        _edge("p2", "c3", "a"), _edge("m2", "c3", "b"),
        _edge("uni", "and1"),
        _edge("c1", "and1"), _edge("c2", "and1"), _edge("c3", "and1"),
        _edge("and1", "out"), _edge("r1", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


def _breaking_resistance():
    """Price arriving at a swing level it has been rejected from before, with
    the distance written as a formula rather than as four arithmetic boxes."""
    nodes = [
        _node("note", "note", 40, 20,
              text="فاصلهٔ قیمت تا نزدیک‌ترین مقاومت، با «فرمول‌نویسی»."),
        _node("sr", "srlevel", 40, 130, k=3, shift=0),
        _node("f1", "formula", 300, 130, expr="(a-close)/close*100", shift=0),
        _node("k1", "const", 40, 300, value=3.0),
        _node("c1", "compare", 560, 180, op="<"),
        _node("k0", "const", 40, 380, value=0.0),
        _node("c2", "compare", 560, 320, op=">"),
        _node("ad", "adx", 40, 470, n=14, shift=0),
        _node("k2", "const", 40, 570, value=20),
        _node("c3", "compare", 560, 470, op=">"),
        _node("and1", "and", 780, 320),
        _node("out", "output", 980, 320, within=1, sort="value"),
        _node("col", "column", 980, 430, label="تا مقاومت ٪", digits=2, sort="asc"),
    ]
    edges = [
        _edge("sr", "f1", "a", from_port="res"),
        _edge("f1", "c1", "a"), _edge("k1", "c1", "b"),
        _edge("f1", "c2", "a"), _edge("k0", "c2", "b"),
        _edge("ad", "c3", "a", from_port="adx"), _edge("k2", "c3", "b"),
        _edge("c1", "and1"), _edge("c2", "and1"), _edge("c3", "and1"),
        _edge("and1", "out"), _edge("f1", "col", "a"),
    ]
    return {"nodes": nodes, "edges": edges}


EXAMPLES = [
    {"key": "engulf", "name": "کندل پوشای صعودی",
     "desc": "کندل سبز امروز، بدنهٔ کندل قرمز دیروز را کامل می‌پوشاند و حجم بالای میانگین ۲۰ روزه است.",
     "graph": _bullish_engulfing()},
    {"key": "golden", "name": "تقاطع طلایی ۵۰/۲۰۰",
     "desc": "میانگین ۵۰ روزه از میانگین ۲۰۰ روزه به بالا عبور کرده و RSI هنوز در اشباع خرید نیست.",
     "graph": _golden_cross()},
    {"key": "ichi", "name": "شکست ابر ایچیموکو",
     "desc": "قیمت پایانی بالای هر دو خط ابر، تقاطع صعودی تنکان و کیجون، و ADX بالای ۲۰.",
     "graph": _ichimoku_breakout()},
    {"key": "yearhigh", "name": "نزدیک سقف یک‌ساله",
     "desc": "قیمت پایانی حداکثر ۵٪ پایین‌تر از بیشترین سقف ۲۴۰ کندل اخیر، با ارزش معاملات بالای ۱۰ میلیارد.",
     "graph": _near_year_high()},
    {"key": "rsibounce", "name": "برگشت از اشباع فروش",
     "desc": "RSI از زیر ۳۰ به بالا عبور کرده و قیمت به بالای باند پایین بولینگر برگشته است.",
     "graph": _rsi_oversold_bounce()},
    {"key": "squeeze", "name": "فشردگی نوسان و شکست",
     "desc": "باند بولینگر داخل کانال کلتنر رفته (نوسان جمع شده)، قیمت از سقف کانال "
             "دانچیان بیرون زده و حجم دست‌کم ۱.۵ برابر میانگین است.",
     "graph": _squeeze_breakout()},
    {"key": "hammer", "name": "چکش با تأیید حجم",
     "desc": "الگوی چکش پس از یک ریزش، همراه با حجم بالاتر از میانگین و RSI زیر ۴۵ — "
             "الگوی کندلی به‌تنهایی پرتکرارترین سیگنال اشتباه این بازار است.",
     "graph": _hammer_confirmed()},
    {"key": "trend", "name": "روند قوی و اندازه‌گیری‌شده",
     "desc": "شیب رگرسیون خطی ۳۰ کندل مثبت، R² بالای ۰.۷ (یعنی واقعاً روند بوده نه "
             "نوسان) و ADX بالای ۲۵.",
     "graph": _strong_trend()},
    {"key": "moneyflow", "name": "ورود پول",
     "desc": "جریان نقدینگی چایکین مثبت، ارزش معاملات بیش از دو برابر میانگین ۲۰ روزه، "
             "و قیمت بالای VWAP بیست‌روزه.",
     "graph": _money_flowing_in()},
    {"key": "pullback", "name": "اصلاح در روند صعودی",
     "desc": "قیمت بالای میانگین ۵۰ روزه است ولی StochRSI زیر ۲۵ رفته و قیمت هنوز بالای "
             "حمایت پیوت S1 ایستاده — اصلاح، نه ریزش.",
     "graph": _pullback_in_uptrend()},
    {"key": "queue", "name": "صف خرید با تأیید هفتگی",
     "desc": "نماد روی سقف مجاز بسته شده و RSI هفتگی‌اش بالای ۵۰ است — صف خرید "
             "تنها، بدون روند، سیگنال نیست.",
     "graph": _queue_with_weekly_trend()},
    {"key": "mtf", "name": "روند ماهانه، ورود روزانه",
     "desc": "پایانی ماهانه بالای میانگین ۱۰ ماهه (جهت)، RSI روزانه زیر ۵۰ (اصلاح) "
             "و قیمت هنوز بالای میانگین ۵۰ روزه — پولبک، نه ریزش. فقط بورس و فرابورس.",
     "graph": _monthly_trend_daily_pullback()},
    {"key": "resist", "name": "رسیدن به مقاومت",
     "desc": "قیمت کمتر از ۳٪ زیر نزدیک‌ترین سقف نوسانی تأییدشده، با ADX بالای ۲۰. "
             "فاصله با یک جعبهٔ «فرمول‌نویسی» حساب شده است.",
     "graph": _breaking_resistance()},
]
