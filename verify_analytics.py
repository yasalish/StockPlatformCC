"""
verify_analytics.py — prove the materialized analytics reproduce db.py exactly.

Run after any change to analytics_views.py, to the period definitions, or after
a Python upgrade (CPython's float sum() algorithm is part of the contract — see
analytics_views.py). Exits non-zero on any mismatch.

Method. The raw scan is NOT reproducible on this database: stockpricehistory
holds ~2.04M duplicate (ticker, j_date) groups, so ROW_NUMBER() breaks ties
arbitrarily and two runs of the same query disagree with each other. Comparing a
view against a fresh live scan would therefore measure that data bug rather than
the SQL port. So both sides are fed the SAME bars — the ones mv_bars_K/mv_ind_K
have already frozen — and the Python reference applies db.py's own functions to
them. Any difference is then attributable to the port and nothing else.

Equality is exact: `==` on floats, no tolerance.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import psycopg2.extras

import db

FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def rows(sql, params=()):
    return db._rows(sql, params)


def ulps(a, b):
    if a is None or b is None or a == b:
        return 0
    return abs(int.from_bytes(struct.pack("<d", a), "little")
               - int.from_bytes(struct.pack("<d", b), "little"))


def same(a, b):
    return a is None and b is None or (a is not None and b is not None and a == b)


# ===========================================================================
print("=" * 78)
print("0. INPUT ASSUMPTION — integral prices")
print("=" * 78)
# analytics_views.py uses window SUM() for SMA/Bollinger instead of db.py's
# sliding sums. That is only bit-safe while every price is a whole number small
# enough that 200-term sums (and their squares) are exact in float8.
for tbl in ("stockpricehistory", "etfpricehistory"):
    r = rows(f"""SELECT count(*) FILTER (WHERE adj_final <> trunc(adj_final)
                                           OR adj_open  <> trunc(adj_open)
                                           OR adj_high  <> trunc(adj_high)
                                           OR adj_low   <> trunc(adj_low)
                                           OR adj_close <> trunc(adj_close)) AS frac,
                        max(adj_final) AS mx FROM {tbl}""")[0]
    check(r["frac"] == 0, f"{tbl}: all OHLC values integral",
          f"{r['frac']} fractional rows, max={r['mx']}")
    check(float(r["mx"]) ** 2 * 200 < 2 ** 53,
          f"{tbl}: 200-term sums of squares stay exact in float8")

# ===========================================================================
print()
print("=" * 78)
print("1. GAINER / PERF views vs db.py maths on identical bars")
print("=" * 78)


def load_bars(kind):
    out = {}
    for r in rows(f"SELECT ticker, j_date, rn, k, o, h, l, c, v FROM mv_bars_{kind}"):
        out.setdefault(r["ticker"], {})[r["rn"]] = r
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    return out, rows(f"SELECT MAX(j_date) d FROM {tbl}")[0]["d"]


def anchor(inwin, n):
    """The reference implementation of the market-calendar anchor: of the bars in
    the window, the one with the SMALLEST k that is still at least n+1 market
    sessions back. `None` when the symbol has no history that far back.

    This is deliberately a different shape of code from the SQL (a python min()
    over a filtered list, not MIN() FILTER) so the two can disagree."""
    cands = [r for r in inwin.values() if r["k"] >= n + 1]
    return min(cands, key=lambda r: r["k"]) if cands else None


def newest(inwin):
    """The symbol's own last bar in the window — smallest k, i.e. mv_bars' rn=1."""
    return min(inwin.values(), key=lambda r: r["k"]) if inwin else None


def meta_rows(kind):
    if kind == "stock":
        return rows("SELECT stockid AS id, ticker, name, market, sector, sub_sector, "
                    "NULL::text AS type FROM stocks")
    return rows("SELECT id, ticker, name, NULL::text AS market, NULL::text AS sector, "
                "NULL::text AS sub_sector, type FROM etf")


def gainer_case(kind, view, periods, label):
    bars, as_of = load_bars(kind)
    cut = db._cutoff(as_of, 2)
    ref = {}
    for t, by_rn in bars.items():
        inwin = {rn: r for rn, r in by_rn.items() if cut <= r["j_date"] <= as_of}
        first = newest(inwin)
        if not first or first["v"] is None:
            continue
        latest = first["v"]
        row = {"latest": latest, "ldate": first["j_date"]}
        for p in periods:
            a = anchor(inwin, p["n"])
            past = a["v"] if a else None
            row[p["key"]] = ((latest - past) / past * 100.0) if past else None
        ref[t] = row
    want = {(m["ticker"], m["id"]): ref[m["ticker"]]
            for m in meta_rows(kind) if m["ticker"] in ref}
    got = {(r["ticker"], r["id"]): r for r in rows(f"SELECT * FROM {view}_{kind}")}
    check(set(want) == set(got), f"{label} {kind}: ticker set",
          f"python {len(want)}, view {len(got)}")
    cols = ["latest", "ldate"] + [p["key"] for p in periods]
    bad, n = [], 0
    for k in set(want) & set(got):
        for c in cols:
            n += 1
            if not same(want[k][c], got[k][c]):
                bad.append((k[0], c, want[k][c], got[k][c]))
    check(not bad, f"{label} {kind}: values bit-identical",
          f"{n:,} compared" if not bad else f"{len(bad)} differ, e.g. {bad[:2]}")


for kind in ("stock", "etf"):
    gainer_case(kind, "mv_market_gainer", db.PERIODS, "market_gainer")
for kind in ("stock", "etf"):
    gainer_case(kind, "mv_period_gainer", db.CALC_PERIODS, "period_gainer")

for kind in ("stock", "etf"):
    bars, as_of = load_bars(kind)
    cut4 = db._cutoff(as_of, 4)
    allt = {r["ticker"]: r for r in rows(f"SELECT * FROM mv_alltime_{kind}")}
    ref = {}
    for t, by_rn in bars.items():
        inwin = {rn: r for rn, r in by_rn.items() if cut4 <= r["j_date"] <= as_of}
        first = newest(inwin)
        if not first or first["v"] is None:
            continue
        latest = first["v"]
        row = {"latest": latest, "ldate": first["j_date"]}
        for p in db.PERF_PERIODS:
            k, kmax = p["key"], p["n"] + 1
            g = anchor(inwin, p["n"])
            span = g["k"] if g else kmax
            vals = [r["v"] for r in inwin.values() if r["k"] <= span]
            row[f"{k}_gain"] = db._pct(latest, g["v"] if g else None)
            row[f"{k}_ceil"] = db._pct(latest, max(vals) if vals else None)
            row[f"{k}_floor"] = db._pct(latest, min(vals) if vals else None)
        a = allt.get(t)
        row["first_gain"] = db._pct(latest, a["first_v"]) if a else None
        row["first_ceil"] = db._pct(latest, a["mx"]) if a else None
        row["first_floor"] = db._pct(latest, a["mn"]) if a else None
        ref[t] = row
    got = {r["ticker"]: r for r in rows(f"SELECT * FROM mv_perf_prices_{kind}")}
    check(set(ref) == set(got), f"perf_prices {kind}: ticker set",
          f"python {len(ref)}, view {len(got)}")
    cols = ["latest", "ldate"] + [f"{p['key']}_{s}" for p in db.PERF_PERIODS
                                  for s in ("gain", "ceil", "floor")] + \
           ["first_gain", "first_ceil", "first_floor"]
    bad, n = [], 0
    for t in set(ref) & set(got):
        for c in cols:
            n += 1
            if not same(ref[t][c], got[t][c]):
                bad.append((t, c, ref[t][c], got[t][c]))
    check(not bad, f"perf_prices {kind}: values bit-identical",
          f"{n:,} compared" if not bad else f"{len(bad)} differ, e.g. {bad[:2]}")

# ===========================================================================
print()
print("=" * 78)
print("2. INDICATOR KERNEL vs db._sma/_ema/_rsi/_macd/_boll_series")
print("=" * 78)

for kind in ("stock", "etf"):
    series = {}
    for r in rows(f"SELECT ticker, i, o, h, l, c, v, sma20, sma50, sma200, "
                  f"boll_up, boll_lo, macd, macd_sig, rsi14, rsi2 "
                  f"FROM mv_ind_{kind} ORDER BY ticker, i"):
        series.setdefault(r["ticker"], []).append(r)
    stats = {}
    for t, rs in series.items():
        px = [r["v"] for r in rs]
        m, s, _ = db._macd_series(px)
        _mid, bu, bl = db._boll_series(px, 20, 2.0)
        ref = {"sma20": db._sma_series(px, 20), "sma50": db._sma_series(px, 50),
               "sma200": db._sma_series(px, 200), "rsi14": db._rsi_series(px, 14),
               "rsi2": db._rsi_series(px, 2), "macd": m, "macd_sig": s,
               "boll_up": bu, "boll_lo": bl}
        for col, exp in ref.items():
            st = stats.setdefault(col, [0, 0, 0])
            for k, r in enumerate(rs):
                st[0] += 1
                if not same(exp[k], r[col]):
                    st[1] += 1
                    st[2] = max(st[2], ulps(exp[k], r[col]))
    for col, (tot, bad, worst) in sorted(stats.items()):
        check(bad == 0, f"kernel {kind}: {col:9s}",
              f"{tot:,} values" if not bad else f"{bad} differ, worst {worst} ULP")

# ===========================================================================
print()
print("=" * 78)
print("3. STRATEGY / FILTER / SCORE vs db._eval_* and db.signal_score")
print("=" * 78)

for kind in ("stock", "etf"):
    series = {}
    for r in rows(f"SELECT ticker, i, o, h, l, c, v FROM mv_ind_{kind} ORDER BY ticker, i"):
        s = series.setdefault(r["ticker"], {"o": [], "h": [], "l": [], "c": [], "v": []})
        for k in "ohlcv":
            s[k].append(r[k])
    known = {r["ticker"] for r in rows(
        f"SELECT ticker FROM {'stocks' if kind == 'stock' else 'etf'}")}

    graded = {}
    for t, s in series.items():
        px = s["v"]
        if len(px) < 30 or t not in known:
            continue
        sig, ind = db._eval_strategies(px)
        n = len(px)
        graded[t] = {"signals": list(sig), "rsi": ind["rsi"],
                     "mom": (px[-22] / px[-253] - 1) if (n >= 253 and px[-253] > 0) else None}
    moms = sorted(r["mom"] for r in graded.values() if r["mom"] is not None)
    if len(moms) >= 20:
        thr = moms[int(0.9 * (len(moms) - 1))]
        for r in graded.values():
            if r["mom"] is not None and r["mom"] >= thr:
                r["signals"].append("xsec_mom")
    view = {r["ticker"]: r for r in rows(f"SELECT ticker, rsi, signals FROM mv_strategy_{kind}")}
    check(set(graded) == set(view), f"strategy {kind}: ticker set",
          f"python {len(graded)}, view {len(view)}")
    bad = [t for t in set(graded) & set(view) if graded[t]["signals"] != view[t]["signals"]]
    check(not bad, f"strategy {kind}: signal lists identical",
          f"{len(set(graded) & set(view))} tickers"
          if not bad else f"{len(bad)} differ, e.g. {bad[:2]}")

    fpy = {t: db._eval_filters(s["o"], s["h"], s["l"], s["c"], s["v"])[0]
           for t, s in series.items() if len(s["v"]) >= 30 and t in known}
    fv = {r["ticker"]: r["matches"] for r in rows(f"SELECT ticker, matches FROM mv_filter_{kind}")}
    check(set(fpy) == set(fv), f"filter {kind}: ticker set",
          f"python {len(fpy)}, view {len(fv)}")
    bad = [t for t in set(fpy) & set(fv) if fpy[t] != fv[t]]
    check(not bad, f"filter {kind}: match lists identical",
          f"{len(set(fpy) & set(fv))} tickers"
          if not bad else f"{len(bad)} differ, e.g. {bad[:2]}")

    spy = {}
    for t, s in series.items():
        if t not in known:
            continue
        res = db.signal_score(s["v"])
        if res is not None:
            spy[t] = res
    sv = {r["ticker"]: r for r in rows(
        f"SELECT ticker, composite, trend, momentum, risk, rsi, range_pos FROM mv_score_{kind}")}
    check(set(spy) == set(sv), f"score {kind}: ticker set",
          f"python {len(spy)}, view {len(sv)}")
    fields = {}
    for t in set(spy) & set(sv):
        r, v = spy[t], sv[t]
        rd = lambda x: None if x is None else round(x, 1)
        for nm, a, b in (("score", r["score"], round(v["composite"], 1)),
                         ("verdict", r["verdict"], db._verdict(v["composite"])),
                         ("trend", r["subs"]["trend"], rd(v["trend"])),
                         ("momentum", r["subs"]["momentum"], rd(v["momentum"])),
                         ("risk", r["subs"]["risk"], rd(v["risk"])),
                         ("rsi", r["indicators"]["rsi"], v["rsi"]),
                         ("range_pos", r["indicators"]["range_pos"], v["range_pos"])):
            f = fields.setdefault(nm, [0, 0, None])
            f[0] += 1
            if a != b:
                f[1] += 1
                f[2] = f[2] or (t, a, b)
    for nm, (tot, bad, ex) in fields.items():
        check(bad == 0, f"score {kind}: {nm:10s}",
              f"{tot:,} compared" if not bad else f"{bad} differ, e.g. {ex}")

# ===========================================================================
print()
print("=" * 78)
print("4. READERS — public shapes, and the no-views fallback")
print("=" * 78)

db.clear_cache()
rows_s, as_of = db.market_gainer("stock")
check(len(rows_s) > 0 and as_of, "market_gainer returns (rows, as_of)",
      f"{len(rows_s)} rows, as_of={as_of}")
keys = set(rows_s[0])
want_keys = {"id", "ticker", "name", "market", "sector", "sub_sector", "type",
             "latest", "ldate"} | {p["key"] for p in db.PERIODS}
check(keys == want_keys, "market_gainer row keys unchanged",
      f"extra={keys - want_keys}, missing={want_keys - keys}")

sc = db.strategy_scan("stock")
check(set(sc) >= {"as_of", "by_strategy", "picks", "count", "scanned",
                  "scanned_by_group", "scanned_by_sub"},
      "strategy_scan shape unchanged", f"scanned={sc['scanned']}, count={sc['count']}")
fs = db.filter_scan("stock")
check(set(fs) >= {"as_of", "by_filter", "count", "scanned"},
      "filter_scan shape unchanged", f"scanned={fs['scanned']}, count={fs['count']}")
ss = db.score_scan("stock")
check(set(ss) >= {"as_of", "rows", "scanned"}, "score_scan shape unchanged",
      f"scanned={ss['scanned']}, rows={len(ss['rows'])}")
pm, pas = db.perf_multi("stock")
check(len(pm) > 0 and pas, "perf_multi returns (rows, as_of)", f"{len(pm)} rows")

# a historical as_of must bypass the views and still work
hist = db._rows("SELECT DISTINCT j_date FROM stockpricehistory "
                "ORDER BY j_date DESC OFFSET 5 LIMIT 1")[0]["j_date"]
check(db._use_view("stock", hist) is False,
      "historical as_of bypasses the views", f"as_of={hist}")
hr, ha = db.market_gainer("stock", as_of=hist)
check(len(hr) > 0 and ha == hist, "historical as_of still computes live",
      f"{len(hr)} rows at {ha}")

# Views that have not caught up with the price table must NOT be read. A data
# update refreshes the views afterwards and mv_ind_stock alone takes ~30 minutes,
# so "the price table has a session the views do not" is a state that really
# occurs — and serving the views then puts today's date on last session's table.
db.clear_cache()
_real_vasof = db._view_as_of
try:
    db._view_as_of = lambda kind: "1400-01-01"
    check(db._use_view("stock", None) is False,
          "views behind the price table are not read for today",
          f"stamp=1400-01-01, latest={db.latest_date('stock')}")
    check(db._use_view("stock", "1400-01-01") is True,
          "the date the views DO hold is still served from them")
    db._view_as_of = lambda kind: None
    check(db._use_view("stock", None) is False,
          "an unreadable stamp falls back to live computation")
finally:
    db._view_as_of = _real_vasof
# NOT "the stamp equals latest_date" — that is a statement about whether someone
# has run refresh_analytics lately, not about the code, and it is legitimately
# false between a data update and its refresh (and while one is running, since
# the stamp is refreshed last). What must ALWAYS hold is the guard's reaction:
# whenever the two disagree, in either direction, the views must not be read.
# A price table that has LOST a session — the delete-then-refetch workflow — puts
# the stamp AHEAD of it, which is just as wrong to serve as behind.
db.clear_cache()
for kind in ("stock", "etf"):
    stamp, latest = db._view_as_of(kind), db.latest_date(kind)
    if stamp == latest:
        check(db._use_view(kind, None) is True,
              f"{kind}: views are current, so they are read",
              f"stamp=latest={stamp}")
    else:
        check(db._use_view(kind, None) is False,
              f"{kind}: views disagree with the price table, so they are NOT read",
              f"stamp={stamp}, price table={latest} — refresh_analytics() pending")

# migration-safe: pretend the views are absent
db.clear_cache()
db._VIEWS_READY = False
fb, fa = db.market_gainer("stock")
check(len(fb) > 0, "fallback path works with views 'absent'", f"{len(fb)} rows")
check(set(fb[0]) == want_keys, "fallback row keys identical to the view path")
db._VIEWS_READY = None
db.clear_cache()

# ===========================================================================
print()
print("=" * 78)
print("5. TRAILING WINDOWS ARE ANCHORED TO THE MARKET CALENDAR, NOT TO THE SYMBOL")
print("=" * 78)
# The regression this guards. Every "n روز" column used to resolve its base bar
# with ROW_NUMBER() PARTITION BY ticker, i.e. n bars back through the SYMBOL's
# own history. For a halted (متوقف) symbol whose last data point is months or
# years old, "5 days ago" then pointed at the week before that final bar: وزمین,
# last traded 150 market sessions before as_of, reported −۵۸٪ in the ۱ هفته
# column and sorted to the very bottom of the market table. The window must
# instead end at the market's own last session, so a symbol that has not traded
# inside the window reports ۰٪ — it has not moved.
#
# Both invariants below are checked on the VIEWS and on the live Python path, and
# for both period sets, because the two paths resolve the anchor independently.
def anchor_invariants(kind, label, get_rows, periods, years):
    as_of = db.latest_date(kind)
    tbl = "stockpricehistory" if kind == "stock" else "etfpricehistory"
    # market sessions between each symbol's last bar and as_of
    age = {r["ticker"]: r["age"] for r in rows(f"""
        WITH cal AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date DESC) k
                     FROM (SELECT DISTINCT date FROM {tbl}) d)
        SELECT p.ticker, MIN(c.k) - 1 AS age
        FROM {tbl} p JOIN cal c ON c.date = p.date
        WHERE p.adj_final > 0 GROUP BY p.ticker""")}
    rs = get_rows()
    stale = [r for r in rs if age.get(r["ticker"], 0) > 0]

    # (a) a symbol that has not traded for n market sessions cannot have moved
    #     over the last n of them.
    bad = [(r["ticker"], p["key"], r[p["key"]], age[r["ticker"]])
           for r in stale for p in periods
           if age[r["ticker"]] >= p["n"] and r[p["key"]] not in (None, 0.0)]
    check(not bad, f"{label} {kind}: halted symbols report 0% inside their halt",
          f"{len(stale)} stale symbols checked"
          if not bad else f"{len(bad)} non-zero, e.g. {bad[:3]}")

    # (b) the base for period n is the price on the (n+1)-th MARKET session, or —
    #     if the symbol did not trade that day — the nearest one before it. Read
    #     straight off the raw price table and resolved in python, so it is an
    #     independent derivation rather than a restatement of the SQL. MAX per k
    #     because stockpricehistory holds duplicate (ticker, j_date) groups (see
    #     the module docstring); MAX is what the FILTER aggregation picks too.
    # The lookback is the view's OWN window (2 years for the gainer tables, 4 for
    # the performance table), not `max(n) + 1`: the anchor for the longest period
    # may sit further back than n+1 sessions when the symbol did not trade that
    # day, and a reference that cannot see it would report a false mismatch.
    maxn = max(p["n"] for p in periods) + 1
    hi, lo = db._window(kind, as_of, years)
    px = {}
    for r in rows(f"""
        WITH cal AS (SELECT date, ROW_NUMBER() OVER (ORDER BY date DESC) k
                     FROM (SELECT DISTINCT date FROM {tbl} WHERE date <= %s) d)
        SELECT p.ticker, c.k, MAX(p.adj_final::float8) v
        FROM {tbl} p JOIN cal c ON c.date = p.date
        WHERE p.adj_final > 0 AND p.date <= %s AND p.date >= %s
        GROUP BY p.ticker, c.k""", (hi, hi, lo)):
        px.setdefault(r["ticker"], {})[r["k"]] = r["v"]
    got = {r["ticker"]: r for r in rs}
    bad2, n2, gapless = [], 0, 0
    for t, by_k in px.items():
        r = got.get(t)
        if not r:
            continue
        latest = by_k[min(by_k)]
        if all(i in by_k for i in range(1, maxn + 1)):
            gapless += 1
        for p in periods:
            far = [k for k in by_k if k >= p["n"] + 1]
            base = by_k[min(far)] if far else None
            n2 += 1
            want = ((latest - base) / base * 100.0) if base else None
            if not same(want, r[p["key"]]):
                bad2.append((t, p["key"], want, r[p["key"]]))
    check(n2 > 0 and not bad2,
          f"{label} {kind}: base = the (n+1)-th MARKET session (or the last before it)",
          f"{len(px)} symbols ({gapless} gapless) x {len(periods)} periods "
          f"= {n2:,} checked" if not bad2
          else f"{len(bad2)} differ, e.g. {bad2[:2]}")


db.clear_cache()
for kind in ("stock", "etf"):
    anchor_invariants(kind, "view market_gainer", lambda k=kind: rows(
        f"SELECT * FROM mv_market_gainer_{k}"), db.PERIODS, 2)
    anchor_invariants(kind, "view period_gainer", lambda k=kind: rows(
        f"SELECT * FROM mv_period_gainer_{k}"), db.CALC_PERIODS, 2)
    anchor_invariants(kind, "live _gainer", lambda k=kind: db._gainer(
        k, db.latest_date(k), db.PERIODS, "p20")[0], db.PERIODS, 2)

# The performance page keys its columns _gain instead of the bare period key.
for kind in ("stock", "etf"):
    perf_periods = [{"key": f"{p['key']}_gain", "n": p["n"]} for p in db.PERF_PERIODS]
    anchor_invariants(kind, "view perf_prices", lambda k=kind: rows(
        f"SELECT * FROM mv_perf_prices_{k}"), perf_periods, 4)
    anchor_invariants(kind, "live _perf_prices", lambda k=kind: [
        dict(r, ticker=t) for t, r in db._perf_prices(k, db.latest_date(k)).items()],
        perf_periods, 4)

# نبض بازار / نقشهٔ بازار read a one-session change from the same kind of
# countdown, so it gets the same treatment: a halted symbol has not moved today.
for kind in ("stock", "etf"):
    ls = db.last_session(kind)
    as_of = db.latest_date(kind)
    bad = [(t, v["jdate"], v["chg"]) for t, v in ls.items()
           if v["jdate"] != as_of and v["chg"] not in (None, 0.0)]
    check(not bad, f"last_session {kind}: halted symbols report 0% for today",
          f"{len(ls)} symbols" if not bad else f"{len(bad)} differ, e.g. {bad[:3]}")

print()
print("=" * 78)
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    for f in FAIL:
        print("   -", f)
    sys.exit(1)
print("RESULT: the materialized analytics reproduce db.py exactly")
