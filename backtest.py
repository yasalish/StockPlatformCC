"""
backtest.py — «بک‌تست فیلتر»: what this graph would have found, and what
happened next.

A designed filter answers "which symbols match TODAY". This answers the only
question that makes that one worth asking: if I had run this filter every
session for the last two years, would following it have made money?

WHY THIS IS CHEAP

It looks like it should cost one market-wide scan per historical session — five
hundred scans for two years — and it does not. filter_engine.evaluate() already
computes the graph's condition as a SERIES over every bar it loaded and then
reads only the last `within` of them; the other twelve hundred booleans are
computed and thrown away. A backtest is that same single pass with the whole
series kept. Two years of history costs one scan, not five hundred.

WHAT MAKES A SCREENER BACKTEST LIE, AND WHAT IS DONE ABOUT IT HERE

  1. Reading the signal bar's own close as the entry. The filter is only known
     after that candle has closed, so the first price anyone could actually pay
     is the NEXT bar's open. That is what is used.

  2. Frame leakage. A weekly block broadcast its finished candle back across the
     whole week, so a Saturday knew Wednesday's high — measured at up to
     fourteen sessions of foresight on a monthly frame. filter_engine's causal
     frame mode (ctx["causal"], see _causal_index there) is switched on for
     every backtest and off for the live screener, where "this week so far" is
     the honest answer.

  3. Fills nobody could get. On this exchange a symbol locked in «صف خرید» at
     the ceiling cannot be bought at all, and a halted one does not trade. Both
     print a price that a naive backtest happily buys, and both are exactly the
     bars a momentum filter fires on — so this is not a rounding error, it is
     the whole result. Those entries are dropped and counted separately.

  4. No benchmark. «۷٪ در ۲۲ روز» is a triumph in a flat market and a disaster
     in one that rose 11 %. Every horizon is reported against the equal-weighted
     return of the same market over the same days, and the excess is the number
     printed last, because it is the only one that means anything.

WHAT IS STILL NOT MODELLED, AND IS SAID SO IN THE UI

  Survivorship. The universe is the symbols that exist now; a company delisted
  in 1402 is in no backtest here. There is no delisting table to fix it with,
  and pretending otherwise would be worse than saying it.
"""
import logging
import os
from array import array

import db
import filter_engine as fe

log = logging.getLogger(__name__)

#: Forward windows, in trading sessions. 22 ≈ one Jalali month of sessions.
HORIZONS = (1, 5, 10, 22)

#: How far back a backtest may reach. The panel is streamed in ticker slices so
#: this is a time budget rather than a memory one: 3000 sessions is roughly
#: twelve years, and the query behind it is ~15 s of the request.
MAX_BARS = 3000

#: The default window, in trading sessions. 250 ≈ one year. The list of windows
#: the page offers lives in BacktestApp.vue rather than here, because those are
#: Persian labels («۶ ماه») and not engine values.
DEFAULT_SESSIONS = 250

#: Tickers per panel slice. The whole market at 1300 bars is 1.1 GB of Python
#: floats; at 120 symbols a slice it is under 200 MB whatever the depth, which
#: is what lets this reach twelve years on the same box that serves the site.
CHUNK = int(os.environ.get("BACKTEST_CHUNK", "120"))

#: Round-trip cost in percent — کارمزد خرید و فروش. Charged once, on the entry,
#: which is where it hurts and where it is easiest to explain.
DEFAULT_COST = 1.2

#: Trades sent to the browser. Statistics are computed on ALL of them; this caps
#: only the table, which nobody scrolls past the first hundred rows of anyway.
TRADE_ROWS = 300

#: A symbol needs this much history beyond the graph's own warm-up before its
#: first bar is allowed to signal, or a freshly listed symbol contributes
#: signals computed from six bars of data.
MIN_HISTORY = 30

#: A bar is treated as locked («صف») when its whole session printed at one
#: price. _QUEUE_TOL is filter_engine's own tolerance for "at the band edge".
_LOCK_TOL = fe._QUEUE_TOL

#: The widest price ratio a single holding period is allowed to report before it
#: is thrown away as a data defect rather than believed as a return.
#:
#: A handful of symbols carry a DISCONTINUOUS adjusted series: «رفاه» after its
#: capital increase has adj_final stored as 0.0 and 1.0 for its 1396 bars and in
#: the 185,000s later on, so a 22-session window that straddles the break
#: reports a 185,305× return. One such bar in a cross-sectional average of eight
#: hundred symbols moved the market benchmark for that day to +505 %, which then
#: became the yardstick every trade entered that session was scored against.
#:
#: 50× is far outside anything the exchange can produce — twenty-two consecutive
#: ceilings on the widest band («بازار پایه», 10 %) is 8.1×, and even a
#: بازگشایی gapping on top of that does not approach it — and far inside the
#: artefacts, which are orders of magnitude out. Samples beyond it are dropped
#: and counted, never clamped: a clamped 50× is still a fiction, just a smaller
#: one, and it would sit in the «بهترین» column looking like a triumph.
_MAX_MOVE = 50.0


class BacktestError(ValueError):
    """A backtest that cannot run. Message is Persian and shown verbatim."""


def _calendar(kind, as_of, bars):
    """The market's own session axis, newest last.

    Every symbol is mapped onto this. Without it the equity curve would be
    summing a halted symbol's Tuesday onto everyone else's Wednesday: the panel
    right-aligns each ticker to its OWN last bar, so bar `i` is a different
    calendar day for a symbol that missed three sessions to a halt.
    """
    table = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    rows = db._tuples(
        f"""SELECT j_date FROM {table}
            WHERE adj_close > 0 AND adj_final > 0 AND date <= %s AND date >= %s
            GROUP BY j_date ORDER BY max(date) DESC LIMIT %s""",
        (*db._window(kind, as_of, max(2, bars // 200 + 2)), bars))
    return [r[0] for r in reversed(rows)]


def _stats(rets, bench):
    """One horizon's column of the report card.

    `bench` carries None for a trade whose entry session the market average
    could not be computed for. Those are EXCLUDED rather than read as zero —
    a zero is a claim that the market was flat that day, and averaging a
    handful of them against a market that actually rose 6 % is what turned a
    control filter with no edge by construction into a +0.64 % «مازاد».

    «مازاد» is then paired — the mean of (trade − its own day's market) — not
    the difference of two means over different samples. With every trade
    benchmarked the two are identical; when some are not, only the paired form
    is still comparing like with like.
    """
    n = len(rets)
    if not n:
        return {"n": 0}
    ordered = sorted(rets)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    avg = sum(rets) / n
    var = sum((x - avg) ** 2 for x in rets) / n if n > 1 else 0.0
    paired = [(r, b) for r, b in zip(rets, bench) if b is not None]
    b_avg = sum(b for _, b in paired) / len(paired) if paired else 0.0
    excess = sum(r - b for r, b in paired) / len(paired) if paired else 0.0
    return {
        "n": n,
        "avg": avg,
        "median": median,
        # «برد» counts strictly positive: a flat trade paid the commission and
        # was not a win.
        "win": sum(1 for x in rets if x > 0) / n * 100.0,
        "best": ordered[-1],
        "worst": ordered[0],
        "stdev": var ** 0.5,
        "bench": b_avg,
        "excess": excess,
        # How many trades the market could be measured for. Anything below `n`
        # is worth knowing about; the UI says so when the gap is material.
        "benched": len(paired),
    }


def _drawdown(curve):
    """Deepest peak-to-trough fall of an equity curve, in percent."""
    peak = curve[0] if curve else 1.0
    worst = 0.0
    for v in curve:
        if v > peak:
            peak = v
        if peak > 0:
            worst = min(worst, (v / peak - 1.0) * 100.0)
    return worst


def backtest(graph, kind="stock", *, sessions=DEFAULT_SESSIONS,
             horizons=HORIZONS, hold=None, group=None, sub_group=None,
             cost=DEFAULT_COST, require_fill=True, repeat=False,
             as_of=None, chunk=CHUNK):
    """Replay `graph` over history and report what its signals were worth.

    `sessions`   how many trading days of signals to collect.
    `hold`       the horizon the equity curve follows; defaults to the longest.
    `repeat`     False (default) counts a run of true bars as ONE signal, on the
                 bar it turned true — otherwise «RSI زیر ۳۰», which stays true
                 for a fortnight, reports fourteen identical trades and every
                 statistic below becomes a measure of how long conditions last
                 rather than of what they were worth.
    `require_fill` drop entries into a locked or halted bar (see module docs).
    """
    if kind not in ("stock", "etf"):
        kind = "stock"
    nodes, edges, order, out_node = fe.normalise(graph)

    # «داده بنیادی» IS LOOK-AHEAD, AND IT CANNOT BE MADE NOT TO BE.
    #
    # That block reads the latest دیده‌بان snapshot — today's EPS, today's market
    # cap, today's buy queue. There is one value per symbol and no history
    # behind it, so replaying it over two years applies TODAY'S numbers to bars
    # from 1402. A «P/E کمتر از ۸» backtest would then be selecting symbols that
    # are cheap NOW and asking what they did back then, which is precisely the
    # bias this file's control test exists to catch — except this one is
    # structural, so no amount of care in the engine removes it.
    #
    # Refused rather than quietly returning None: a backtest that silently
    # scores zero is indistinguishable from a filter with no edge, and the whole
    # argument of this module is that a number which looks like a fact must be
    # one. «حقیقی و حقوقی» is deliberately NOT refused — ri_history is real
    # per-session history, so replaying it is honest.
    fundamental = sorted(n["id"] for n in nodes.values()
                         if n["type"] == "fundamental")
    if fundamental:
        raise BacktestError(
            "بلاک «داده بنیادی» را نمی‌توان بک‌تست کرد: مقدارهای آن (EPS، "
            "P/E، ارزش بازار، صف) فقط برای آخرین عکس دیده‌بان موجودند و "
            "سابقهٔ روزانه ندارند، پس اجرای آن‌ها روی گذشته یعنی نسبت دادن "
            "عددهای امروز به کندل‌های دو سال پیش. برای بک‌تست، این بلاک را از "
            "گراف بردارید؛ بلاک «حقیقی و حقوقی» سابقهٔ واقعی دارد و مشکلی ندارد.")

    # …and «حقیقی و حقوقی» over an empty ri_history would score a flat zero,
    # which reads as "this filter has no edge" rather than "this filter has no
    # data". Same refusal the live run gives, for the same reason.
    try:
        fe._require_datasets(nodes, kind)
    except fe.GraphError as exc:
        raise BacktestError(str(exc)) from exc

    horizons = tuple(sorted({int(h) for h in horizons if int(h) > 0})) or HORIZONS
    max_h = max(horizons)
    hold = int(hold or max_h)
    if hold not in horizons:
        hold = max_h
    sessions = max(20, int(sessions))

    # `within` is a LIVE convenience — "fired at some point in the last N bars",
    # so that a crossing three sessions old still shows up on today's list. A
    # backtest knows exactly which bar fired, so it reads the raw condition and
    # ignores the setting rather than counting each signal `within` times.
    warmup = fe._raw_bars_needed(nodes, out_node) - out_node["params"]["within"]
    warmup = max(warmup, 0) + MIN_HISTORY
    bars = warmup + sessions + max_h + 2
    clipped = bars > MAX_BARS
    if clipped:
        bars = MAX_BARS
        sessions = bars - warmup - max_h - 2
        if sessions < 20:
            raise BacktestError(
                "این گراف آن‌قدر تاریخچه لازم دارد که جایی برای بک‌تست نمی‌ماند؛ "
                "دورهٔ اندیکاتورها یا تایم‌فریم را کوچک‌تر کنید.")

    fields = set(fe.fields_needed(nodes))
    fields.update(("o", "f"))                    # entry at open, exit at final
    if require_fill:
        fields.update(("v", "h", "l"))
    fields = tuple(sorted(fields))

    if as_of is None:
        as_of = db.latest_date(kind)
    if as_of is None:
        raise BacktestError("داده‌ای برای بک‌تست موجود نیست.")

    axis = _calendar(kind, as_of, bars)
    if len(axis) < warmup + 20:
        raise BacktestError("تاریخچهٔ کافی برای این بک‌تست وجود ندارد.")
    slot = {jd: i for i, jd in enumerate(axis)}
    span = len(axis)

    # First session of the tested window. Shared by the benchmark
    # accumulator and the equity curve so the two can never disagree
    # about where the backtest starts.
    g_lo = max(0, span - sessions - max_h - 1)

    meta = fe._meta(kind)
    universe = [t for t, m in meta.items()
                if (not group or m.get("group") == group)
                and (not sub_group or m.get("sub_group") == sub_group)]
    if not universe:
        raise BacktestError("هیچ نمادی در این گروه نیست.")

    # ---- accumulators ----------------------------------------------------
    # Compact arrays rather than a list of dicts: a loose filter over twelve
    # years produces hundreds of thousands of signals, and the statistics need
    # every one of them even though the table shows three hundred.
    names = []                                   # ticker per trade, by index
    t_in = array("i")                            # entry bar, calendar index
    t_px = array("d")
    t_ret = {h: array("d") for h in horizons}    # net of cost
    t_out = {h: array("i") for h in horizons}    # exit bar, calendar index

    # The market: every symbol's every daily move, averaged cross-sectionally —
    # an equal-weighted index of the same universe the filter chose from. This
    # is what the benchmark CURVE is drawn from.
    m_sum = [0.0] * span
    m_cnt = [0] * span
    # …and the benchmark each horizon is scored against, which is a different
    # number and has to be. Compounding the daily index above answers "what did
    # a fund that rebalanced every night make", and Tehran's limit bands make
    # that strategy quietly profitable on its own — a rebalancing bonus that
    # showed up as a widening −0.16 → −0.40 % «مازاد» for a filter that bought
    # the entire market and therefore had no edge to lose. So each horizon gets
    # the cross-sectional average of the SAME trade anyone else could have made:
    # buy every symbol at that session's open, hold h bars, sell at settlement.
    # Same entry price, same exit price, same days — the only thing that differs
    # is which symbols, which is the one thing being measured.
    b_sum = {h: [0.0] * span for h in horizons}
    b_cnt = {h: [0] * span for h in horizons}
    # The filter's own portfolio: the same shape, restricted to bars that a
    # signal was actually holding.
    e_sum = [0.0] * span
    e_cnt = [0] * span

    scanned = skipped_lock = skipped_halt = dropped = 0
    errors = 0
    src_port = None
    bucket = out_node["ins"].get("in") or []
    if not bucket:
        raise BacktestError("گراف نود «خروجی فیلتر» وصل‌شده ندارد.")
    src_id, src_port = bucket[0]

    for start in range(0, len(universe), chunk):
        slice_ = universe[start:start + chunk]
        store = fe._load_columns(kind, as_of, bars, fields,
                                 tickers=slice_, dates=True)
        dates = store.get(fe.DATE_COL) or {}
        for ticker in dates:
            m = meta.get(ticker)
            if not m:
                continue
            jd = dates[ticker]
            series = {}
            for f in fields:
                col = store[f].get(ticker)
                if col is None:
                    series = None
                    break
                series[f] = col
            if series is None:
                continue
            n = len(series["c"])
            if n < warmup + max_h + 2:
                continue
            scanned += 1

            # Where each of this symbol's bars falls on the market calendar. A
            # bar whose date the market axis does not carry (a symbol that
            # traded on a day nothing else did) is dropped from the curves but
            # can still signal.
            gid = [slot.get(d, -1) for d in jd]

            f_col, o_all = series["f"], series["o"]
            for k in range(1, n):
                g = gid[k]
                prev = f_col[k - 1]
                if g < 0 or not prev:
                    continue
                m_sum[g] += f_col[k] / prev - 1.0
                m_cnt[g] += 1
            # Bars this symbol could be ENTERED on: one after a bar the graph
            # was allowed to signal on, and early enough to hold to the longest
            # horizon. The signal loop below re-derives the same two bounds.
            first = max(warmup, 1)
            last = n - 1 - max_h
            if last < first:
                continue

            # The benchmark is accumulated over EXACTLY those bars and no
            # others, and that identity is the whole point of it.
            #
            # It is tempting to widen this to the whole panel — buying the
            # market needs no indicator to be warm, so why make it wait out the
            # graph's warm-up? Because then the two sides stop being the same
            # question. The benchmark has to be "what would buying every symbol
            # THIS FILTER COULD HAVE PICKED have paid", and a symbol thirty bars
            # into its listing could not have been picked. Widening it by that
            # one rule moved the control filter — one that buys the entire
            # market and therefore has no edge by construction — from +0.005 %
            # to +0.642 % of «مازاد» at twenty-two sessions. A backtester that
            # awards two thirds of a percent to a strategy with no edge will
            # award it to every strategy anyone tests.
            k_lo, k_hi = first + 1, min(n, last + 2)
            while k_lo < k_hi and gid[k_lo] < g_lo:
                k_lo += 1
            bases = [(o_all[k] or f_col[k]) for k in range(k_lo, k_hi)]
            lo_r, hi_r = 1.0 / _MAX_MOVE, _MAX_MOVE
            for h in horizons:
                bs, bc, off = b_sum[h], b_cnt[h], h - 1
                for k in range(k_lo, k_hi):
                    g = gid[k]
                    if g < 0:
                        continue
                    base = bases[k - k_lo]
                    if not base:
                        continue
                    ratio = f_col[k + off] / base
                    if lo_r < ratio < hi_r:      # else a broken adjusted series
                        bs[g] += ratio - 1.0
                        bc[g] += 1

            ctx = {"bars": series, "n": n, "meta": m, "kind": kind,
                   "frames": {}, "causal": True}
            try:
                memo = fe._memoise(nodes, order, ctx)
            except Exception:                    # one bad symbol, not the run
                errors += 1
                if errors < 4:
                    log.warning("backtest: symbol failed",
                                extra={"ticker": ticker}, exc_info=True)
                continue
            cond = memo.get(src_id, {}).get(src_port)
            if cond is None:
                continue
            flags = fe._bools(cond, n)

            o_col, v_col = series["o"], series.get("v")
            h_col, l_col = series.get("h"), series.get("l")
            band = fe._band_pct("auto", ctx) / 100.0

            # `first` and `last` came from the benchmark block above, which has
            # to use the identical bounds. Room to act on the signal (bar i+1)
            # and to hold it to the longest horizon (bar i+max_h): the last
            # max_h sessions therefore produce no signals at all — their future
            # has not happened yet — which is why `to` in the payload is not
            # simply the last session on the calendar.
            for i in range(first, last + 1):
                if not flags[i]:
                    continue
                if not repeat and flags[i - 1]:
                    continue                     # still the same signal
                j = i + 1                        # the bar we could act on
                entry = o_col[j] or f_col[j]
                if not entry:
                    continue
                if require_fill:
                    if v_col is not None and not v_col[j]:
                        skipped_halt += 1        # متوقف — did not trade
                        continue
                    # «صف خرید» at the moment we would have bought. The test is
                    # on the OPEN rather than on the close because the open is
                    # the price this backtest pays: a bar that opened at the
                    # ceiling had a queue waiting before the session started and
                    # no order of ours joins the front of it. A bar that merely
                    # CLOSED there rose into the queue during the day and was
                    # perfectly buyable at the open, so it is left alone —
                    # filter_engine's «سقف و کف مجاز» block answers the other
                    # question, "is it in a queue now", and rightly uses close.
                    ceiling = f_col[i] * (1.0 + band) if f_col[i] else 0.0
                    if ceiling and entry >= ceiling * (1.0 - _LOCK_TOL):
                        skipped_lock += 1
                        continue
                    # …and the halt that prints a price: one flat bar at the
                    # ceiling, which is what a symbol locked from open to close
                    # looks like when volume is not reported as zero.
                    if (h_col is not None and h_col[j] == l_col[j]
                            and ceiling and f_col[j] >= ceiling * (1.0 - _LOCK_TOL)):
                        skipped_lock += 1
                        continue
                g_in = gid[j]
                # Outside the window the user asked for, or on a date the market
                # calendar does not carry.
                #
                # The bar-index bounds above are NOT enough to enforce this. The
                # panel right-aligns every symbol to its own last bar, so a
                # symbol that stopped trading in 1401 has all of its bars — bar
                # `first` included — sitting years before the tested window
                # begins. Those signals were being counted: 7 % of the control
                # filter's trades came from before the window, they had no
                # market average to be scored against, and a request for "one
                # year" quietly reported trades from 1400 in its total.
                if g_in < g_lo:
                    continue

                # Every horizon is priced BEFORE any of them is recorded, so a
                # signal is either in the report for all of them or in none. A
                # per-horizon drop would leave the columns with different `n`
                # and quietly make them incomparable — which is the one thing
                # the table exists to do.
                ratios = [f_col[j + h - 1] / entry for h in horizons]
                if any(not (1.0 / _MAX_MOVE < x < _MAX_MOVE) for x in ratios):
                    dropped += 1
                    continue

                names.append(ticker)
                t_in.append(g_in)
                t_px.append(entry)
                for h, ratio in zip(horizons, ratios):
                    # The commission is charged once, on the way in.
                    t_ret[h].append((ratio - 1.0) * 100.0 - cost)
                    t_out[h].append(gid[j + h - 1])

                # The equity curve holds this position for `hold` sessions. Day
                # one runs from the price actually paid, not from yesterday's
                # settlement, which is the difference between an equity curve
                # and a fantasy.
                for k in range(j, j + hold):
                    g = gid[k]
                    if g < 0:
                        continue
                    base = entry if k == j else f_col[k - 1]
                    if not base:
                        continue
                    r = f_col[k] / base - 1.0
                    if k == j:
                        r -= cost / 100.0
                    e_sum[g] += r
                    e_cnt[g] += 1
        del store

    if not names:
        return {
            "as_of": as_of, "kind": kind, "sessions": sessions, "bars": bars,
            "scanned": scanned, "signals": 0, "clipped": clipped,
            "horizons": list(horizons), "hold": hold, "cost": cost,
            "stats": {}, "curve": [], "bench_curve": [], "dates": [],
            "trades": [], "drawdown": 0.0, "bench_drawdown": 0.0,
            "exposure": 0.0,
            "skipped": {"lock": skipped_lock, "halt": skipped_halt,
                        "bad": dropped},
            "errors": errors,
            "from": axis[g_lo],
            "to": axis[span - 1 - max_h],
            "group": group, "subgroup": sub_group,
        }

    # ---- the market curve, and every trade measured against it -----------
    # Built only now, because a trade that opened in 1403 is benchmarked with
    # bars that had not been read yet when it was recorded.
    m_ret = [(m_sum[t] / m_cnt[t]) if m_cnt[t] else 0.0 for t in range(span)]

    # What buying the whole market on the same day, for the same number of
    # bars, would have paid. Indexed by the ENTRY session, because that is the
    # decision being second-guessed: on the day this filter said «فولاد», the
    # alternative was every other symbol on the board.
    #
    # Charged the same commission as the strategy. Buying the market is a trade
    # too, and leaving it gross would bake a flat −`cost` into every «مازاد» —
    # making a filter with no edge look like it destroyed 1.2 %, and a filter
    # with a real 1 % edge look like it had none.
    bench = {}
    for h in horizons:
        sums, cnts = b_sum[h], b_cnt[h]
        avg = [(sums[t] / cnts[t] * 100.0 - cost) if cnts[t] else None
               for t in range(span)]
        bench[h] = [avg[g] for g in t_in]

    stats = {str(h): _stats(t_ret[h], bench[h]) for h in horizons}

    # ---- the two curves, over the tested window only ---------------------
    lo = g_lo
    curve, bench_curve = [], []
    eq = bq = 1.0
    held = 0
    dates = axis[lo:]
    for t in range(lo, span):
        if e_cnt[t]:
            eq *= 1.0 + e_sum[t] / e_cnt[t]
            held += 1
        bq *= 1.0 + m_ret[t]
        curve.append(eq)
        bench_curve.append(bq)
    # «زمان در بازار». The curve above is flat on every session the filter had
    # nothing open, while the benchmark is invested on all of them — so a filter
    # can trail the index while beating it per day held, and a filter that fires
    # twice a year can beat it while being an rounding error on a portfolio.
    # Without this number neither case is visible.
    exposure = held / len(dates) * 100.0 if dates else 0.0

    # ---- the table: the most recent trades, newest first -----------------
    recent = sorted(range(len(names)), key=lambda k: t_in[k], reverse=True)
    trades = []
    for k in recent[:TRADE_ROWS]:
        row = {"ticker": names[k],
               "name": (meta.get(names[k]) or {}).get("name") or "",
               "date": axis[t_in[k]], "entry": t_px[k]}
        for h in horizons:
            row[f"r{h}"] = t_ret[h][k]
        b = t_out[hold][k]
        row["exit_date"] = axis[b] if b >= 0 else None
        trades.append(row)

    return {
        "as_of": as_of, "kind": kind, "sessions": sessions, "bars": bars,
        "scanned": scanned, "signals": len(names), "clipped": clipped,
        "horizons": list(horizons), "hold": hold, "cost": cost,
        "stats": stats,
        "curve": curve, "bench_curve": bench_curve, "dates": dates,
        "drawdown": _drawdown(curve), "bench_drawdown": _drawdown(bench_curve),
        "trades": trades, "exposure": exposure,
        "skipped": {"lock": skipped_lock, "halt": skipped_halt,
                        "bad": dropped},
        "errors": errors,
        "from": axis[lo], "to": axis[span - 1 - max_h],
        "group": group, "subgroup": sub_group,
    }
