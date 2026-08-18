"""
verify_order08.py — verification for Order 08 (Vue 3 island on market.html).

  A  Scope — market.html is the ONLY template converted; the other four table
     pages and every other template are untouched.
  B  Build — Vue 3 + TypeScript + Vite + TanStack, built to static/dist/ and
     loaded through the existing asset_version() cache-busting.
  C  Persian formatting — the TypeScript port of db.to_persian / _pill.html is
     compared against the real Python over every value in the live dataset.
  D  Endpoint — /api/market/<kind> is thin, cached and login-gated.
  E  BEFORE vs AFTER in a real browser — the page is rendered with the original
     template and with the converted one, and the two are compared: column
     widths, row heights, header labels, cell text, pill classes, star states.
  F  Behaviour — filters with no round trip, URL and Back, sorting, the text
     filter, virtualization, the Excel export, and the watchlist star.

Needs: the «Stock» database, Redis, node, and Chrome or Edge.
Run:  python verify_order08.py            (skip the browser with --no-browser)
"""
import io
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".tools"))
os.chdir(HERE)

BROWSER = "--no-browser" not in sys.argv
FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


FRONTEND = os.path.join(HERE, "frontend")
SHOTS = os.path.join(HERE, ".tools", "shots")
PRE08 = os.path.join(HERE, ".tools", "market.html.pre08")
LIVE = os.path.join(HERE, "templates", "market.html")
os.makedirs(SHOTS, exist_ok=True)

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
       "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
       "MSYS_NO_PATHCONV": "1"}


def node(script, *args, cwd=FRONTEND, timeout=900):
    return subprocess.run(["node", script, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd, env=ENV,
                          timeout=timeout)


# ===========================================================================
print("=" * 74)
print("PART A — scope: ONE page converted")
print("=" * 74)

check(os.path.isdir("frontend"), "frontend/ project exists")
check(os.path.isdir("static/dist"), "the bundle is built into static/dist/")

market = read("templates/market.html")
check("market-panel-app" in market and "calc-grid-app" in market,
      "market.html mounts the island")
check("dist/market.js" in market and "asset_version" in market,
      "…loaded with the existing asset_version() cache-busting")
# The template still MENTIONS BN.initTable in a comment explaining why it is
# gone; look for a call, not the word.
check('BN.initTable("' not in market,
      "…and no longer CALLS BN.initTable (TanStack Table owns sorting now)")

# Order 08 converted market.html ONLY, and this part still holds it to that:
# no other template may mount the MARKET island or load market.js.
#
# performance.html, filters.html and strategies.html have since been converted
# too — not by this order, but by the same pattern and with their own islands
# (perf-app / dist/perf.js and scan-app / dist/scan.js), because after order 08
# they were the three heaviest pages left: 2.2 MB / 37k nodes, 1.2 MB / 23k and
# 2.2 MB / 38k. They are still checked here for the MARKET island's absence,
# like every other page, but no longer for BN.initTable; they have their own
# verification in verify_perf_island.py and verify_scan_islands.py — as has
# screener.html, the last of the big server-rendered tables.
UNTOUCHED = ["performance.html", "strategies.html", "filters.html",
             "screener.html", "base.html", "stock_detail.html", "etf_detail.html",
             "dashboard.html", "update.html", "watchlist.html", "index.html"]
for t in UNTOUCHED:
    p = os.path.join("templates", t)
    if not os.path.exists(p):
        continue
    body = read(p)
    check("market-panel-app" not in body and "dist/market.js" not in body,
          f"{t}: untouched by the conversion")
    if t == "watchlist.html":
        check("BN.initTable" in body,
              f"{t}: still the original Jinja table (BN.initTable intact)")

check("nav-loader" in read("templates/base.html"),
      "base.html is unchanged — the #nav-loader overlay is still there "
      "(see the report: it still serves the four unconverted pages)")


# ===========================================================================
print()
print("=" * 74)
print("PART B — the build")
print("=" * 74)

pkg = json.loads(read("frontend/package.json"))
deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
for name in ("vue", "@tanstack/vue-table", "@tanstack/vue-virtual", "vite",
             "typescript", "vue-tsc"):
    check(name in deps, f"dependency: {name} {deps.get(name, '')}")

check(os.path.exists("static/dist/market.js"), "static/dist/market.js exists")
# The bundle is measured across the entry AND the chunks it imports: since the
# performance island was added there are two entries, and rollup lifts what they
# share (Vue, TanStack, format.ts) into a common chunk.
dist_js = sorted(f for f in os.listdir("static/dist") if f.endswith(".js"))
size = sum(os.path.getsize(os.path.join("static/dist", f)) for f in dist_js)
check(0 < size < 400_000,
      f"bundle is {size / 1024:.1f} kB across {len(dist_js)} files (uncompressed)",
      ", ".join(dist_js))

vite = read("frontend/vite.config.ts")
check('outDir' in vite and "static/dist" in vite, "vite builds into static/dist/")
# Entries keep FIXED names (market.js, perf.js) because the templates stamp them
# with asset_version(); only the shared chunks are content-hashed, because
# nothing stamps a chunk URL and /static/ is immutable for a year.
check('entryFileNames: "[name].js"' in vite and 'chunk-[name]-[hash].js' in vite,
      "…entries keep fixed filenames, shared chunks are content-hashed")
check(os.path.exists("static/dist/perf.js"),
      "the performance island builds alongside it (static/dist/perf.js)")
check('target: "es2019"' in vite,
      "…targeting es2019 for the older Android browsers common in the audience")

tsc = subprocess.run(["npx", "vue-tsc", "--noEmit"], capture_output=True, text=True,
                     encoding="utf-8", errors="replace", cwd=FRONTEND, env=ENV,
                     shell=(os.name == "nt"), timeout=600)
check(tsc.returncode == 0, "vue-tsc typechecks clean",
      (tsc.stdout + tsc.stderr).strip()[:160])


# ===========================================================================
print()
print("=" * 74)
print("PART C — Persian formatting matches db.to_persian EXACTLY")
print("=" * 74)

import db

rows, _ = db.market_gainer("stock")
crows, _ = db.period_gainer("stock")
erows, _ = db.market_gainer("etf")
vals = []
for src, periods in ((rows, db.PERIODS), (crows, db.CALC_PERIODS), (erows, db.PERIODS)):
    for r in src:
        for v in [r.get("latest")] + [r.get(p["key"]) for p in periods]:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                vals.append(v)
# Values that probe the rounding rule specifically — this is where the port
# originally diverged (Python rounds half to even, JS toFixed rounds half up).
vals += [0, 0.125, 2.625, 15.625, 40.625, 1234.5, -1234.5, 999.995, 9.995,
         1e6, 1234567.891, -0.001, 3.14159, 123456789]

cases = [{"v": v, "fa": db.to_persian(v)} for v in vals]
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader("templates"))
env.globals["fa"] = db.to_persian
pill_t = env.get_template("_pill.html")
pills = [{"v": v, "html": pill_t.render(val=v).strip()}
         for v in ([r.get(p["key"]) for r in rows for p in db.PERIODS][:4000]
                   + [0.0, 12.3456, -4.5, None, -0.004])]

io.open(".tools/fmt-cases.json", "w", encoding="utf-8").write(
    json.dumps(cases, ensure_ascii=False))
io.open(".tools/pill-cases.json", "w", encoding="utf-8").write(
    json.dumps(pills, ensure_ascii=False))

subprocess.run(["npx", "esbuild", "src/format.ts", "--format=esm",
                "--outfile=../.tools/format.mjs", "--log-level=error"],
               cwd=FRONTEND, env=ENV, shell=(os.name == "nt"), check=True,
               capture_output=True, timeout=300)

cmp_js = r"""
import { fa, pill } from './format.mjs';
import { readFileSync } from 'node:fs';
const nums = JSON.parse(readFileSync('.tools/fmt-cases.json','utf8'));
const pls  = JSON.parse(readFileSync('.tools/pill-cases.json','utf8'));
let numBad = [], pillBad = [];
for (const c of nums) if (fa(c.v) !== c.fa) numBad.push([c.v, c.fa, fa(c.v)]);
for (const c of pls) {
  const p = pill(c.v);
  const html = p.missing ? '<span class="muted">—</span>'
                         : `<span class="pill ${p.cls}">${p.text}</span>`;
  if (html !== c.html) pillBad.push([c.v, c.html, html]);
}
console.log(JSON.stringify({numTotal:nums.length, numBad, pillTotal:pls.length, pillBad}));
"""
io.open(".tools/cmpfmt.mjs", "w", encoding="utf-8").write(cmp_js)
r = subprocess.run(["node", ".tools/cmpfmt.mjs"], capture_output=True, text=True,
                   encoding="utf-8", cwd=HERE, env=ENV, timeout=300)
try:
    res = json.loads(r.stdout.strip().splitlines()[-1])
    check(len(res["numBad"]) == 0,
          f"fa(): {res['numTotal']} live values, {len(res['numBad'])} mismatches",
          str(res["numBad"][:3]))
    check(len(res["pillBad"]) == 0,
          f"_pill.html markup: {res['pillTotal']} values, "
          f"{len(res['pillBad'])} mismatches", str(res["pillBad"][:2]))
except Exception as e:
    check(False, "formatting comparison ran", f"{e} {r.stdout[:200]} {r.stderr[:200]}")

fmt = read("frontend/src/format.ts")
check("half to even" in fmt or "half-even" in fmt.lower(),
      "the port documents the half-to-even tie rule it had to implement")
check("U+2212" in fmt and "U+066A" in fmt,
      "…and the two non-ASCII characters _pill.html uses (− and ٪)")
# Checked against the BUILT bundle: format.ts mentions Intl in a comment
# explaining why it is the wrong tool here, and the build strips comments.
# Every shipped .js is checked, not just market.js — format.ts now lives in the
# chunk both islands share, so testing one entry would prove nothing.
check(all("Intl.NumberFormat" not in read(os.path.join("static/dist", f))
          for f in dist_js),
      "Intl.NumberFormat is not in any shipped bundle — it would emit «٬» and "
      "«٫», which is better Persian but different from every other page here")


# ===========================================================================
print()
print("=" * 74)
print("PART D — the JSON endpoint")
print("=" * 74)

appsrc = read("app.py")
check("/api/market/<kind>" in appsrc, "/api/market/<kind> exists")
check("db.market_gainer(kind, as_of=as_of_arg)" in appsrc,
      "…and is thin: it returns db.market_gainer()'s dicts as they are")

import app as webapp
anon = webapp.app.test_client()
check(anon.get("/api/market/stock").status_code in (401, 302),
      "…and is behind the login gate like every other page")

import bn_liveserver as L
made_user = L.ensure_user()
try:
    with webapp.app.test_client() as c:
        uid = db._one("SELECT id FROM users WHERE username = %s", (L.USERNAME,))
        with c.session_transaction() as s:
            s["_user_id"] = str(uid["id"])
            s["_fresh"] = True
        t0 = time.perf_counter()
        r1 = c.get("/api/market/stock")
        cold = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        r2 = c.get("/api/market/stock")
        warm = (time.perf_counter() - t0) * 1000
        payload = r2.get_json()
        check(r1.status_code == 200 and r2.status_code == 200,
              f"200 for a logged-in user ({cold:.0f} ms cold, {warm:.0f} ms warm)")
        check(len(payload["rows"]) > 100 and len(payload["calc_rows"]) > 100,
              f"{len(payload['rows'])} rows + {len(payload['calc_rows'])} calc rows")
        check(warm < 250, f"warm response is {warm:.0f} ms — the table is not "
                          f"sitting on a slow API")
        check(payload["rows"][0].keys() >= {"ticker", "name", "latest", "id"},
              "row shape is db.market_gainer()'s, unchanged")
        check("watched" in payload,
              "the star state ships with the data, so stars render pre-filled")

    # ===================================================================
    if not BROWSER:
        print("\n(--no-browser: skipping the before/after render comparison)")
    else:
        print()
        print("=" * 74)
        print("PART E — BEFORE vs AFTER, rendered in Chrome")
        print("=" * 74)

        if not os.path.exists(PRE08):
            check(False, "the pre-conversion template is kept for comparison",
                  f"missing {PRE08}")
        else:
            def capture(tag, path="/stocks"):
                srv = L.LiveServer()
                srv.start()
                try:
                    out = os.path.join(SHOTS, tag).replace("\\", "/")
                    res = node("capture.mjs", srv.base, L.USERNAME, L.PASSWORD,
                               path, out)
                    if res.returncode != 0:
                        raise RuntimeError((res.stdout + res.stderr)[-400:])
                    return json.loads(read(out + ".json"))
                finally:
                    srv.stop()

            converted = read(LIVE)
            try:
                io.open(LIVE, "w", encoding="utf-8", newline="\n").write(read(PRE08))
                before = capture("before-stocks")
            finally:
                io.open(LIVE, "w", encoding="utf-8", newline="\n").write(converted)
            after = capture("after-stocks")

            check(before["dir"] == after["dir"] == "rtl", "both render dir=rtl")
            check(before["lang"] == after["lang"] == "fa", "both render lang=fa")
            check(before["title"] == after["title"],
                  f"same page title ({after['title']})")

            for i, name in ((0, "calculator table"), (1, "main market table")):
                tb, ta = before["tables"][i], after["tables"][i]
                check(tb["headers"] == ta["headers"], f"{name}: headers identical")
                n = min(len(tb["firstRows"]), len(ta["firstRows"]))
                check(tb["firstRows"][:n] == ta["firstRows"][:n],
                      f"{name}: first {n} rows identical, cell for cell")
                check(tb["firstPillClasses"][:n] == ta["firstPillClasses"][:n],
                      f"{name}: pill up/down classes identical")
                check(tb["stars"][:n] == ta["stars"][:n],
                      f"{name}: watchlist star markup identical")
                check(tb["colWidths"] == ta["colWidths"],
                      f"{name}: every column is the same width",
                      f"{ta['colWidths']}")
                check(tb["rowHeights"] == ta["rowHeights"],
                      f"{name}: row heights identical — text wraps the same way")

            check(after["domNodes"] < before["domNodes"] / 10,
                  f"DOM shrank from {before['domNodes']:,} to {after['domNodes']:,} "
                  f"nodes ({before['domNodes'] / max(after['domNodes'], 1):.0f}x)")
            rendered_before = sum(t["renderedRows"] for t in before["tables"])
            rendered_after = sum(t["renderedRows"] for t in after["tables"])
            check(rendered_after < 100 < rendered_before,
                  f"rows in the DOM: {rendered_before} → {rendered_after} "
                  f"(virtualization)")
            check(len(after["consoleErrors"]) <= len(before["consoleErrors"]),
                  f"no NEW console errors ({len(before['consoleErrors'])} before, "
                  f"{len(after['consoleErrors'])} after — the one on both is a "
                  f"pre-existing missing favicon)")

            # ===============================================================
            print()
            print("=" * 74)
            print("PART F — behaviour in the browser")
            print("=" * 74)
            srv = L.LiveServer()
            srv.start()
            try:
                res = node("interact.mjs", srv.base, L.USERNAME, L.PASSWORD)
                if res.returncode != 0:
                    check(False, "interaction suite ran",
                          (res.stdout + res.stderr)[-400:])
                else:
                    R = json.loads(res.stdout[res.stdout.index("{"):])
                    check(R["navigationsAfterFilter"] == 0,
                          "changing the market filter causes NO page load "
                          f"({R['filterMs']} ms, {R['navigationsAfterFilter']} "
                          f"document requests)")
                    check("۳۶۱" in R["afterFilterCount"] or
                          R["afterFilterCount"] != R["initialCount"],
                          f"…and the count updates: {R['initialCount']} → "
                          f"{R['afterFilterCount']}")
                    check("market=" in R["urlAfterFilter"],
                          f"the URL still carries the filter ({R['urlAfterFilter']})")
                    check("market=" in R["exportHref"],
                          f"the Excel export follows it ({R['exportHref']})")
                    check(R["urlAfterBack"] == "" and
                          R["countAfterBack"] == R["initialCount"],
                          "Back returns to the unfiltered view…")
                    check(R["navigationsAfterBack"] == 0,
                          "…without a page load either")
                    check(R["textFilterTickers"] == ["فولاد"],
                          f"the text filter narrows to «فولاد» "
                          f"({R['textFilterCount']})")
                    check(R["sortIndicatorAsc"] == "sorted-asc" and
                          R["sortIndicatorDesc"] == "sorted-desc",
                          "clicking a header sorts and shows the ▲/▼ indicator")
                    check(R["sortedAscFirst"] != R["sortedDescFirst"],
                          f"…and reverses: {R['sortedAscFirst'][0]} ⇄ "
                          f"{R['sortedDescFirst'][0]}")
                    check(R["virtualWindowMoved"],
                          f"scrolling moves the virtual window "
                          f"({R['tickersAtTop'][0]} → {R['tickersAtBottom'][-1]})")
                    check(R["maxDomRows"] < 60,
                          f"never more than {R['maxDomRows']} of 742 rows in the DOM")
                    check("on" in R["starAfter"] and "on" not in R["starBefore"],
                          f"the watchlist star toggles ({R['starTicker']})")
                    check(all("on" in c for c in R["starInCalcTable"]),
                          "…in BOTH tables on the page at once")
                    check(R["navBadge"] is not None,
                          f"…and the nav badge updates ({R['navBadge']})")
                    check("on" not in R["starRestored"],
                          "…and toggling back leaves no state behind")
                    check(R["rowClickUrl"].startswith("/stock/"),
                          f"clicking a row still opens the detail page "
                          f"({R['rowClickUrl']})")
                    check(R["pageErrors"] == [],
                          f"no uncaught JavaScript errors ({R['pageErrors']})")
            finally:
                srv.stop()
finally:
    if made_user:
        L.remove_user()
        print("\n  throwaway user removed")

print()
print("=" * 74)
if FAIL:
    print(f"{len(FAIL)} FAILURE(S):")
    for f in FAIL:
        print("   -", f)
else:
    print("ALL CHECKS PASSED")
print("=" * 74)
sys.exit(1 if FAIL else 0)
