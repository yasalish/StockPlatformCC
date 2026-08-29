"""
verify_perf_island.py — verification for the «بازدهٔ دوره‌ای» conversion.

/performance was the heaviest page in the app: 782 symbols × 22 columns rendered
into 2.2 MB of HTML and ~37,000 DOM nodes, which cost the browser about 1.3 s of
parsing and layout on EVERY navigation — and every dropdown change was a fresh
one of those. It now follows the same pattern order 08 established for
market.html: Jinja renders a shell, a Vue island fetches
/api/performance/<kind> and materialises only the rows on screen.

  A  Shell — the page ships no table rows and stays small.
  B  API — the endpoint returns what the page used to compute, filters included,
     and the 🏆 winners / compare table still come from Python, not TypeScript.
  C  Equivalence — for the same query the island's data matches what the old
     server-side computation produced (same rows, same order, same numbers).
  D  Browser — node + Chrome/Edge: DOM budget, sorting on every column, the text
     filter, a dropdown change with no page reload, Back, and deep scrolling.

Needs: the «Stock» database. Part D also needs node and Chrome or Edge; pass
--no-browser to skip it.

Run:  python verify_perf_island.py
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, "frontend")
sys.path.insert(0, HERE)
os.chdir(HERE)

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

BROWSER = "--no-browser" not in sys.argv
FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


import app as A
import db

uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")["id"]
client = A.app.test_client()
with client.session_transaction() as s:
    s["_user_id"] = str(uid)
    s["_fresh"] = True


# ===========================================================================
print("=" * 74)
print("PART A — the page is a shell")
print("=" * 74)

client.get("/performance")                 # warm the templates
t0 = time.perf_counter()
page = client.get("/performance")
page_ms = (time.perf_counter() - t0) * 1000
body = page.get_data(as_text=True)

check(page.status_code == 200, f"/performance renders ({page.status_code})")
check(len(body) < 100_000,
      f"the HTML is {len(body) / 1024:.0f} kB (was 2,234 kB with the rows in it)")
# The shell must not compute the table it no longer prints: anything near the
# API's own time would mean /performance is doing the ~780-row job twice.
check(page_ms < 60, f"and takes {page_ms:.0f} ms to build — no row work in the shell")
check('id="perf-app"' in body, "it mounts the island")
check("dist/perf.js" in read("templates/performance.html")
      and "asset_version" in read("templates/performance.html")
      and "dist/perf.js?v=" in body,
      "…loaded with the existing asset_version() cache-busting")
check("data-ticker=" not in body, "no table rows are rendered server-side")
check('BN.initTable("' not in body,
      "…and BN.initTable is not called (the island owns sorting now)")
check("bn-island-fallback" in body,
      "a fallback is present for when the bundle or the fetch fails")
check('id="perf-date-form"' in body,
      "the از/تا date form stays server-rendered (it drives a server computation)")

for f in ("frontend/src/PerfGrid.vue", "frontend/src/PerfPanel.vue", "frontend/src/perf.ts"):
    check(os.path.exists(f), f"source: {f}")
check(os.path.exists("static/dist/perf.js"), "built: static/dist/perf.js")


# ===========================================================================
print()
print("=" * 74)
print("PART B — /api/performance/<kind>")
print("=" * 74)

t0 = time.perf_counter()
r = client.get("/api/performance/stock")
cold_ms = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter()
r = client.get("/api/performance/stock")
warm_ms = (time.perf_counter() - t0) * 1000
payload = r.get_json()
check(r.status_code == 200, f"/api/performance/stock answers ({r.status_code})")
# Cold is the first call in a fresh worker (the scan runs and the analytics cache
# fills); warm is every call after it, which is what a browsing session sees.
check(cold_ms < 2500, f"cold: {cold_ms:.0f} ms")
check(warm_ms < 250, f"warm: {warm_ms:.0f} ms, {len(r.data) / 1024:.0f} kB of JSON")
for key in ("rows", "cols", "tops", "comparison", "groups", "markets", "watched",
            "etf_type_colors", "as_of", "group_label"):
    check(key in payload, f"payload carries «{key}»")
check(len(payload["rows"]) > 0, f"{len(payload['rows'])} rows")
check(len(payload["tops"]) == len(payload["cols"]),
      "one 🏆 winner per period column — computed in Python, not in the browser")
check(client.get("/api/performance/bogus").status_code == 404,
      "an unknown kind is a 404")

first = payload["rows"][0]
# Period keys come from db.PERF_PERIODS rather than being spelled out. They were
# 'm1_ceil'/'m1_floor' when this was written, and the day-based ladder («۵ روز»…
# «۷۲۰ روز») renamed every one of them — a literal here just goes stale and
# reports a missing field that is not missing.
_mid = db.PERF_PERIODS[len(db.PERF_PERIODS) // 2]["key"]
for field in ("ticker", "id", "latest", "sector",
              f"{_mid}_ceil", f"{_mid}_floor", "first_gain"):
    check(field in first, f"row field: {field}")

# filters are applied SERVER-side, so the island never has to reproduce them
grp = payload["groups"][0]
sub = client.get(f"/api/performance/stock?group={grp}").get_json()
check(0 < len(sub["rows"]) <= len(payload["rows"]),
      f"?group={grp} narrows the rows ({len(sub['rows'])} of {len(payload['rows'])})")
check(all(x["sector"] == grp for x in sub["rows"]),
      "…and every row really is in that group")
check(sub["group"] == grp, "the endpoint echoes the filter it applied")

# a bad filter is dropped rather than returning an empty table
bogus = client.get("/api/performance/stock?group=NOPE").get_json()
check(bogus["group"] is None and len(bogus["rows"]) == len(payload["rows"]),
      "an unknown group is ignored, exactly as the page route does")

# compare
tk = payload["rows"][0]["ticker"]
cmpd = client.get(f"/api/performance/stock?cmp={tk}").get_json()
check(cmpd["compare"] and cmpd["compare"]["ticker"] == tk,
      f"?cmp={tk} returns the compared symbol")
check(len(cmpd["comparison"]) == len(cmpd["cols"]),
      "…with one comparison row per period")

# the custom-range column
dates = db.recent_trading_dates("stock", n=40)
if len(dates) >= 30:
    rto, rfrom = dates[0], dates[29]
    cust = client.get(f"/api/performance/stock?rfrom={rfrom}&rto={rto}").get_json()
    check(cust["cols"][0]["key"] == "custom",
          f"«بازهٔ دلخواه» is added for {rfrom}…{rto}")
    check(any(x.get("custom_gain") is not None for x in cust["rows"]),
          "…and the rows carry its numbers")


# ===========================================================================
print()
print("=" * 74)
print("PART C — the island's data equals the old server-side computation")
print("=" * 74)

# _performance_data() is the function the page used before the conversion; the
# API is a second caller of it, so this compares the endpoint against the very
# code path that produced the old HTML.
for query in ({}, {"group": grp}, {"cmp": tk}):
    expected = A._performance_data({**query, "kind": "stock"})
    qs = "&".join(f"{k}={v}" for k, v in query.items())
    got = client.get(f"/api/performance/stock?{qs}").get_json()
    label = qs or "no filter"
    check(len(got["rows"]) == len(expected["rows"]), f"{label}: same row count")
    check([x["ticker"] for x in got["rows"]] == [x["ticker"] for x in expected["rows"]],
          f"{label}: same rows in the same order")
    check([t["ticker"] for t in got["tops"]] == [t["ticker"] for t in expected["tops"]],
          f"{label}: same 🏆 winners")
    same_numbers = all(
        abs((g.get(k) or 0) - (e.get(k) or 0)) < 1e-9
        for g, e in zip(got["rows"][:50], expected["rows"][:50])
        for k in ("latest", f"{_mid}_ceil", f"{_mid}_floor",
                  f'{db.PERF_PERIODS[-1]["key"]}_ceil', "first_floor"))
    check(same_numbers, f"{label}: the numbers are identical to the page's")

# The endpoint ships doubles at full precision. Shortening them looks free — it
# is 20% of the payload — but it is not: measured over this dataset, rounding to
# FOUR decimals already moves a number the user reads (184.43496801705757 shows
# as ۱۸۴.۴۳; rounded to 184.435 it shows as ۱۸۴.۴۴). This check is kept so that
# anyone who tries it again finds out here rather than from a wrong digit.
raw = A._performance_data({"kind": "stock"})["rows"]
values = [v for r in raw for v in r.values() if isinstance(v, float)]
moved = [v for v in values if db.to_persian(round(v, 4)) != db.to_persian(v)]
print(f"  NOTE  shortening the payload by rounding to 4 decimals would change "
      f"{len(moved)} of {len(values):,} displayed numbers"
      + (f" (e.g. {moved[0]!r})" if moved else "")
      + " — hence full precision")


# ===========================================================================
print()
print("=" * 74)
print("PART D — in a real browser")
print("=" * 74)

if not BROWSER:
    print("  SKIP  --no-browser")
else:
    import threading
    import werkzeug.serving

    srv = werkzeug.serving.make_server("127.0.0.1", 5099, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    # A signed session cookie, so the browser can open the login-gated page.
    with A.app.test_request_context():
        cookie = A.app.session_interface.get_signing_serializer(A.app).dumps(
            {"_user_id": str(uid), "_fresh": True})

    try:
        proc = subprocess.run(
            ["node", "perf_check.mjs", "http://127.0.0.1:5099", cookie],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=FRONTEND, timeout=600, shell=(os.name == "nt"))
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except Exception as e:
        out = {}
        check(False, "the browser check ran", f"{e} {proc.stderr[:200] if 'proc' in dir() else ''}")
    finally:
        srv.shutdown()

    if out:
        check(not out["errors"], "no JavaScript errors on the page",
              "; ".join(out["errors"])[:200])
        check(out["nodes"] < 3000,
              f"the page is {out['nodes']} DOM nodes (was 37,097)")
        check(5 < out["renderedRows"] < 60,
              f"only the visible rows exist ({out['renderedRows']} of "
              f"{len(payload['rows'])})")
        check(out["interactiveMs"] < 2500,
              f"interactive in {out['interactiveMs']} ms "
              f"(HTML {out['htmlBytes'] / 1024:.0f} kB, TTFB {out['ttfbMs']} ms)")
        check(out["sortableHeaders"] == 3 + 2 * len(payload["cols"]),
              f"every column is sortable ({out['sortableHeaders']})")
        check(out["topCards"] == len(payload["cols"]),
              f"the 🏆 cards are rendered ({out['topCards']})")
        check(out["stickyFirst"], "the نماد column is still pinned while scrolling sideways")

        asc, desc = out["floorAsc"], out["floorDesc"]
        check(all(asc[i - 1] <= v for i, v in enumerate(asc) if i),
              f"«کف» sorts ascending ({', '.join(f'{v:.2f}' for v in asc[:4])})")
        check(all(desc[i - 1] >= v for i, v in enumerate(desc) if i),
              "…and descending on the second click")
        check(all(out["priceAsc"][i - 1] <= v for i, v in enumerate(out["priceAsc"]) if i),
              "قیمت پایانی sorts too")
        check(out["filtered"] and all("فولاد" in t for t in out["filtered"]),
              f"the symbol filter still filters ({', '.join(out['filtered'])})")

        check(out["documentLoadsOnFilter"] == 0,
              "changing «گروه» does NOT reload the page — the whole point of the change")
        check(out["filterMs"] < 1500, f"…and lands in {out['filterMs']} ms")
        check("group=" in out["filterUrl"],
              f"the URL still carries the filter ({out['filterUrl']})")
        check("group=" not in out["urlAfterBack"],
              "Back steps out of the filter, as a page reload used to")
        check(out["deepFirstIndex"] > 50 and out["deepNodes"] < 4000,
              f"scrolling deep renders row {out['deepFirstIndex']} onward and the DOM "
              f"stays at {out['deepNodes']} nodes")


print()
print("=" * 74)
print(("FAILED: " + ", ".join(FAIL)) if FAIL else "ALL CHECKS PASSED")
print("=" * 74)
sys.exit(1 if FAIL else 0)
