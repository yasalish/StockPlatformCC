"""
verify_order00.py — verification for Order 00.

Part A: time db.market_gainer("stock") unfiltered, then with a market filter,
        then with a sector filter. All three must be well under 100 ms after the
        first, and the filtered rows must be exact subsets of the unfiltered
        scan (same values, same order) so no analytics maths moved.
Part B: render /stocks, /etfs and a stock detail page and confirm they still
        come back correct and RTL, plus check the new cache headers and that the
        foreign-CDN references are gone.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def ms(fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    return (time.perf_counter() - t0) * 1000.0, r


# ===========================================================================
print("=" * 74)
print("PART A — db.market_gainer timings")
print("=" * 74)

import db
import cache       # the analytics cache moved into Redis in order 04

db.clear_cache()   # start genuinely cold, as a fresh process would be

full_rows, as_of = db.market_gainer("stock")   # warm-up / cold scan
t_cold, _ = ms(db.market_gainer, "stock")      # (already cached by the line above)

# Pick a real market and a real sector that actually have rows.
markets = sorted({r["market"] for r in full_rows if r["market"]})
sectors = sorted({r["sector"] for r in full_rows if r["sector"]})
MARKET = max(markets, key=lambda m: sum(1 for r in full_rows if r["market"] == m))
SECTOR = max(sectors, key=lambda s: sum(1 for r in full_rows if r["sector"] == s))

print(f"\nas_of = {as_of}   tickers = {len(full_rows)}")
print(f"market filter = {MARKET!r}   sector filter = {SECTOR!r}\n")

# --- the three calls the order asks for -----------------------------------
db.clear_cache()
t1, r1 = ms(db.market_gainer, "stock")
print(f"  1. unfiltered            (COLD, first call) : {t1:8.1f} ms   {len(r1[0]):4d} rows")

t2, r2 = ms(db.market_gainer, "stock", market=MARKET)
print(f"  2. ?market={MARKET:<14}            : {t2:8.1f} ms   {len(r2[0]):4d} rows")

t3, r3 = ms(db.market_gainer, "stock", sector=SECTOR)
print(f"  3. ?group=<sector>                    : {t3:8.1f} ms   {len(r3[0]):4d} rows")

t4, r4 = ms(db.market_gainer, "stock")
print(f"  4. unfiltered again                   : {t4:8.1f} ms   {len(r4[0]):4d} rows")

# subgroup + etf, for completeness
subs = sorted({r["sub_sector"] for r in full_rows if r["sub_sector"]})
SUB = max(subs, key=lambda s: sum(1 for r in full_rows if r["sub_sector"] == s)) if subs else None
if SUB:
    t5, r5 = ms(db.market_gainer, "stock", sector=SECTOR, sub_sector=SUB)
    print(f"  5. ?group=…&subgroup=…                : {t5:8.1f} ms   {len(r5[0]):4d} rows")
else:
    t5 = 0.0

print()
check(t2 < 100, f"market filter under 100 ms ({t2:.1f} ms)")
check(t3 < 100, f"sector filter under 100 ms ({t3:.1f} ms)")
check(t4 < 100, f"unfiltered re-call under 100 ms ({t4:.1f} ms)")
if SUB:
    check(t5 < 100, f"group+subgroup filter under 100 ms ({t5:.1f} ms)")

# ===========================================================================
print()
print("=" * 74)
print("PART A2 — the filtered rows are still exactly right")
print("=" * 74)

full = r1[0]

check(r2[1] == as_of and r3[1] == as_of, "(rows, as_of) shape kept; as_of unchanged across filters")

exp_m = [r for r in full if r["market"] == MARKET]
check(r2[0] == exp_m, f"market subset matches the unfiltered scan ({len(exp_m)} rows, same order)")

exp_s = [r for r in full if r["sector"] == SECTOR]
check(r3[0] == exp_s, f"sector subset matches the unfiltered scan ({len(exp_s)} rows, same order)")

# Every market subset partitions the full list — nothing lost, nothing invented.
tot = sum(len(db.market_gainer("stock", market=m)[0]) for m in markets)
check(tot == sum(1 for r in full if r["market"]),
      f"market subsets partition the table ({tot} rows across {len(markets)} markets)")

tot_s = sum(len(db.market_gainer("stock", sector=s)[0]) for s in sectors)
check(tot_s == sum(1 for r in full if r["sector"]),
      f"sector subsets partition the table ({tot_s} rows across {len(sectors)} sectors)")

# Ordering: a filtered list must be a subsequence of the sorted full list.
def is_subsequence(sub, whole):
    it = iter(whole)
    return all(any(x is y for y in it) for x in sub)

check(is_subsequence(r2[0], full), "market rows keep the unfiltered sort order")
check(is_subsequence(r3[0], full), "sector rows keep the unfiltered sort order")

# Spot-check the maths against fresh, uncached scans.
#
# Careful: the scan itself is not reproducible on this database. Two back-to-back
# UNCACHED _gainer() calls already disagree with each other, because
# stockpricehistory holds ~2.04M duplicate (ticker, j_date) groups and
# ROW_NUMBER() OVER (ORDER BY j_date DESC) breaks those ties arbitrarily. So the
# test is not "equal to a fresh scan" — nothing satisfies that — but "the cached
# path diverges from a fresh scan on NO ticker that two fresh scans don't already
# disagree on themselves". i.e. the refactor adds no error of its own.
PKEYS = [p["key"] for p in db.PERIODS]


def differing(x, y):
    bx = {r["ticker"]: r for r in x}
    by = {r["ticker"]: r for r in y}
    return {t for t in bx if t in by and (
        bx[t]["latest"] != by[t]["latest"]
        or any(bx[t].get(k) != by[t].get(k) for k in PKEYS))}


# The tickers whose result CAN move between runs are exactly those with a
# duplicated (ticker, j_date) inside the scanned window — that is a fixed,
# checkable set, unlike "whichever two runs happened to disagree".
AMBIG = {r["ticker"] for r in db._rows(
    """SELECT DISTINCT ticker FROM (
         SELECT ticker, j_date FROM stockpricehistory
         WHERE adj_final > 0 AND j_date <= %s AND j_date >= %s
         GROUP BY ticker, j_date HAVING COUNT(*) > 1) x""",
    (as_of, db._cutoff(as_of)))}

fresh1, _ = db._gainer("stock")
fresh2, _ = db._gainer("stock")
inherent = differing(fresh1, fresh2)
mine = differing(full, fresh1) | differing(full, fresh2)

check(inherent <= AMBIG,
      "run-to-run drift is confined to tickers with duplicate (ticker, j_date) rows",
      f"{len(inherent)} drifting, all within the {len(AMBIG)} ambiguous tickers")
check(mine <= AMBIG,
      "cached path matches a fresh scan on every UNAMBIGUOUS ticker",
      f"{len(full) - len(AMBIG)} clean tickers identical; "
      f"{len(mine)} differ, all of them ambiguous")
print(f"  NOTE  {len(AMBIG)}/{len(full)} tickers have duplicate (ticker, j_date) rows,")
print(f"        so their period returns are non-reproducible run-to-run. "
      f"PRE-EXISTING data bug — see report.")

# ETFs go down the same path.
te, re_ = ms(db.market_gainer, "etf")
etypes = sorted({r["type"] for r in re_[0] if r["type"]})
if etypes:
    tet, ret = ms(db.market_gainer, "etf", etf_type=etypes[0])
    print(f"\n  etf ?type={etypes[0]:<12}: {tet:8.1f} ms   {len(ret[0]):4d} rows")
    check(tet < 100, f"etf type filter under 100 ms ({tet:.1f} ms)")
    check(ret[0] == [r for r in re_[0] if r["type"] == etypes[0]],
          "etf type subset matches the unfiltered scan")

# ===========================================================================
print()
print("=" * 74)
print("PART A3 — clear_cache() still invalidates")
print("=" * 74)

db.market_gainer("stock", market=MARKET)
db.perf_multi("stock")
before = len(cache.keys())
db.clear_cache()
check(cache.keys() == [],
      f"clear_cache() invalidates every cache ({before} entries -> {len(cache.keys())})")

tc, _ = ms(db.market_gainer, "stock", market=MARKET)
check(tc > 100, f"a filtered call after clear_cache() really recomputes ({tc:.0f} ms)")
t_after, _ = ms(db.market_gainer, "stock", market=MARKET)
check(t_after < 100, f"and is cached again immediately after ({t_after:.1f} ms)")

# One cache entry per (kind, as_of) — not one per filter combination.
db.clear_cache()
for m in markets:
    db.market_gainer("stock", market=m)
for s in sectors:
    db.market_gainer("stock", sector=s)
gain_keys = cache.keys("gain")
check(len(gain_keys) == 1,
      f"{len(markets) + len(sectors)} filter calls -> {len(gain_keys)} cached scan (was 1 per filter)",
      str(gain_keys))

# perf_multi: same shape of fix
db.clear_cache()
db.perf_multi("stock")
for m in markets[:5]:
    db.perf_multi("stock", market=m)
perf_keys = cache.keys("perfm")
check(len(perf_keys) == 1, f"perf_multi caches one table, not one per filter ({perf_keys})")

# perf_multi hands back copies, so the /performance custom-range write can't leak
rows_a, _ = db.perf_multi("stock")
rows_a[0]["custom_gain"] = 12345.0
rows_b, _ = db.perf_multi("stock")
check("custom_gain" not in rows_b[0], "perf_multi rows are copies — caller writes don't reach the cache")

# strategy / filter / score scans: already keyed (kind, as_of); confirm it holds
db.clear_cache()
db.strategy_scan("stock")
for s in sectors[:5]:
    db.strategy_scan("stock", group=s)
check(len(cache.keys("strategy")) == 1, f"_strategy_scan_full: one entry for many groups ({len(cache.keys('strategy'))})")
db.filter_scan("stock")
for s in sectors[:5]:
    db.filter_scan("stock", group=s)
check(len(cache.keys("filter")) == 1, f"_filter_scan_full: one entry for many groups ({len(cache.keys('filter'))})")
db.score_scan("stock")
for s in sectors[:5]:
    db.score_scan("stock", group=s)
check(len(cache.keys("score")) == 1, f"score_scan_full: one entry for many groups ({len(cache.keys('score'))})")

# ===========================================================================
print()
print("=" * 74)
print("PART B — pages still render (RTL) + cache headers")
print("=" * 74)

import app as webapp

flask_app = webapp.app
flask_app.config["WTF_CSRF_ENABLED"] = False

uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")
if not uid:
    print("  !! no users in the DB — cannot exercise the logged-in pages")
    FAIL.append("no user to log in as")
else:
    client = flask_app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid["id"])
        sess["_fresh"] = True

    stock_id = full[0]["id"]

    pages = [("/stocks", "market"),
             (f"/stocks?market={MARKET}", "stocks + market"),
             (f"/stocks?group={SECTOR}", "stocks + group"),
             ("/etfs", "etfs"),
             (f"/stock/{stock_id}", "stock detail")]

    print("\n  (each page hit twice: cold, then warm)\n")
    for path, label in pages:
        t0 = time.perf_counter()
        resp = client.get(path)
        cold = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        resp = client.get(path)
        warm = (time.perf_counter() - t0) * 1000.0
        body = resp.get_data(as_text=True)
        rtl = 'dir="rtl"' in body and 'lang="fa"' in body
        nocdn = "cdn.jsdelivr.net" not in body and "cdnjs.cloudflare.com" not in body
        check(resp.status_code == 200 and rtl and nocdn, f"{label:<16} {path}",
              f"HTTP {resp.status_code}, {len(body):,} B, "
              f"{cold:.0f} ms cold / {warm:.0f} ms warm, rtl={rtl}, no-cdn={nocdn}")
        cc = resp.headers.get("Cache-Control", "")
        # HTML must still be revalidated on every navigation and never stored by
        # a shared cache — but it is deliberately NOT `no-store` any more: that
        # token disables Chrome's back/forward cache, which made «برگشت» a full
        # 1.9 s re-render of a page the browser had just built.
        check("no-cache" in cc and "must-revalidate" in cc and "private" in cc
              and "no-store" not in cc,
              f"{label:<16} HTML revalidates but stays bfcache-eligible", cc)
        # the page must load the self-hosted font, not the CDN one
        if "/stock/" not in path:
            check("css/style.css" in body, f"{label:<16} links local style.css")

    # login page: needs a LOGGED-OUT client (an authenticated one gets a 302)
    anon = flask_app.test_client()
    lg = anon.get("/login")
    lb = lg.get_data(as_text=True)
    check(lg.status_code == 200 and "cdnjs.cloudflare.com" not in lb
          and "cdn.jsdelivr.net" not in lb and "all.min.css" not in lb
          and 'dir="rtl"' in lb,
          "/login renders RTL with no foreign-CDN <link> left",
          f"HTTP {lg.status_code}, {len(lb):,} B")

    # static assets: long, immutable cache instead of no-store
    for asset in ("/static/css/style.css", "/static/js/app.js",
                  "/static/fonts/Vazirmatn-Regular.woff2"):
        r = client.get(asset)
        cc = r.headers.get("Cache-Control", "")
        check(r.status_code == 200 and "immutable" in cc and "max-age=31536000" in cc
              and "no-store" not in cc,
              f"static cacheable  {asset}", f"HTTP {r.status_code}, {len(r.data):,} B, {cc}")

    # all nine self-hosted weights are actually served
    weights = ["Thin", "ExtraLight", "Light", "Regular", "Medium",
               "SemiBold", "Bold", "ExtraBold", "Black"]
    got = [w for w in weights
           if client.get(f"/static/fonts/Vazirmatn-{w}.woff2").status_code == 200]
    check(len(got) == 9, f"all 9 Vazirmatn weights served locally ({len(got)}/9)")

    # the removed file is really gone, and nothing asks for it
    check(client.get("/static/js/tv-chart.js").status_code == 404,
          "static/js/tv-chart.js removed")

print()
print("=" * 74)
if FAIL:
    print(f"RESULT: {len(FAIL)} FAILURE(S)")
    for f in FAIL:
        print(f"   - {f}")
    sys.exit(1)
print("RESULT: all checks passed")
print("=" * 74)
