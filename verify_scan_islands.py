"""
verify_scan_islands.py — verification for the /filters, /strategies and
/screener conversion.

Both pages rendered every section's table on every navigation: 19 tables / 2,497
rows (1.2 MB, 23,000 DOM nodes) and 16 tables / 3,782 rows (2.2 MB, 38,000 DOM
nodes). Almost all of it was markup for sections the reader never scrolls to.
They now share one island (frontend/src/scan.ts) fed by /api/scan/<what>/<kind>,
which mounts a section's table when the section comes near the viewport.

/screener was the same problem in one table instead of many — 779 ranked rows,
1.1 MB, 16,600 nodes — so it is converted alongside them, with a virtualized grid
(frontend/src/ScreenerGrid.vue) rather than lazy sections.

  A  Shells — no page ships table rows, and none runs its scan twice.
  B  API — the payload matches what the pages used to compute, is normalised
     (each symbol once), and honours the same group / sub-group validation.
  C  Browser — node + Chrome/Edge: DOM budget on load, sections materialising on
     scroll, an honest scrollbar, sorting, and filters that do not reload.
  D  The screener island — its own endpoint, ranking, badges and bands.

Needs the «Stock» database; part C also needs node and Chrome/Edge (--no-browser
skips it).

Run:  python verify_scan_islands.py
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

PAGES = {"filters": "/filters", "strategies": "/strategies"}
WAS = {"filters": (1228, 23075), "strategies": (2198, 38383),
       "screener": (1104, 16583)}                               # kB, DOM nodes


# ===========================================================================
print("=" * 74)
print("PART A — both pages are shells")
print("=" * 74)

for what, url in PAGES.items():
    client.get(url)                                   # warm the templates
    t0 = time.perf_counter()
    r = client.get(url)
    ms = (time.perf_counter() - t0) * 1000
    body = r.get_data(as_text=True)
    was_kb = WAS[what][0]
    check(r.status_code == 200, f"{url} renders ({r.status_code})")
    check(len(body) < 60_000,
          f"{url}: {len(body) / 1024:.0f} kB of HTML (was {was_kb:,} kB)")
    check(ms < 60, f"{url}: built in {ms:.0f} ms — the shell does not re-run the scan")
    check('id="scan-app"' in body and f'data-what="{what}"' in body,
          f"{url}: mounts the island")
    check("dist/scan.js" in body, f"{url}: loads the island bundle")
    check("data-ticker=" not in body, f"{url}: no rows are rendered server-side")
    check("BN.initTable" not in body, f"{url}: BN.initTable is gone")
    check("bn-island-fallback" in body, f"{url}: has a fallback")

# the screener shell, same rules
client.get("/screener")
t0 = time.perf_counter()
r = client.get("/screener")
ms = (time.perf_counter() - t0) * 1000
body = r.get_data(as_text=True)
check(r.status_code == 200 and len(body) < 60_000,
      f"/screener: {len(body) / 1024:.0f} kB of HTML (was {WAS['screener'][0]:,} kB)")
check(ms < 60, f"/screener: built in {ms:.0f} ms — the shell does not re-run the scan")
check('id="screener-app"' in body and "dist/screener.js" in body,
      "/screener: mounts its island")
check("data-ticker=" not in body and "BN.initTable" not in body,
      "/screener: no rows and no BN.initTable")

for f in ("frontend/src/ScanPanel.vue", "frontend/src/ScanSection.vue",
          "frontend/src/ScanTable.vue", "frontend/src/scan.ts",
          "frontend/src/ScreenerGrid.vue", "frontend/src/ScreenerPanel.vue",
          "frontend/src/screener.ts",
          "static/dist/scan.js", "static/dist/screener.js"):
    check(os.path.exists(f), f"exists: {f}")


# ===========================================================================
print()
print("=" * 74)
print("PART B — /api/scan/<what>/<kind>")
print("=" * 74)

for what in PAGES:
    client.get(f"/api/scan/{what}/stock")
    t0 = time.perf_counter()
    r = client.get(f"/api/scan/{what}/stock")
    ms = (time.perf_counter() - t0) * 1000
    d = r.get_json()
    refs = sum(len(s["ids"]) for s in d["sections"])
    check(r.status_code == 200 and ms < 250,
          f"{what}: {ms:.0f} ms, {len(r.data) / 1024:.0f} kB "
          f"({refs:,} matches over {len(d['symbols']):,} symbols)")
    # Normalisation is the point: a symbol matching ten sections is sent once.
    check(len(d["symbols"]) <= refs,
          f"{what}: symbols are sent once and referenced by id "
          f"({len(d['symbols'])} ≤ {refs})")
    check(len(r.data) < WAS[what][0] * 1024 / 2,
          f"{what}: the payload is under half the HTML it replaces")

    # the same scan the page used to render, section by section
    scan = (db.strategy_scan("stock") if what == "strategies" else db.filter_scan("stock"))
    bucket = scan["by_strategy"] if what == "strategies" else scan["by_filter"]
    meta = db.STRATEGIES if what == "strategies" else db.FILTERS
    check(len(d["sections"]) == len(meta), f"{what}: every section is present")
    same = all(
        [i for i in sec["ids"]] == [row["id"] for row in bucket.get(sec["key"], [])]
        for sec in d["sections"])
    check(same, f"{what}: each section holds exactly the symbols the scan found")
    check(d["count"] == scan["count"] and d["scanned"] == scan["scanned"],
          f"{what}: the summary counts match the scan "
          f"({d['count']} matched of {d['scanned']} scanned)")

    # every referenced id resolves, and carries what the table renders
    ids = {i for sec in d["sections"] for i in sec["ids"]}
    check(all(str(i) in d["symbols"] for i in ids),
          f"{what}: every referenced id resolves to a symbol")
    sym = d["symbols"][str(next(iter(ids)))]
    check(all(k in sym for k in ("ticker", "name", "group", "latest", "rsi")),
          f"{what}: symbol carries what the table shows", ", ".join(sorted(sym)))
    check("matches" not in sym and "sub_group" not in sym,
          f"{what}: and nothing it does not (the scan's working fields are dropped)")

# strategies-only: the ⭐ picks
picks = client.get("/api/scan/strategies/stock").get_json()
check(picks["picks"] and all("score" in p and "signals" in p for p in picks["picks"]),
      f"strategies: the ⭐ picks are included ({len(picks['picks'])})")
check(all(str(p["id"]) in picks["symbols"] for p in picks["picks"]),
      "…referencing the same symbol table")
check(bool(picks.get("strat_names")), "…with the strategy names their tags need")

# filters-only: the categories the dropdown offers
cats = client.get("/api/scan/filters/stock").get_json()
check(cats.get("categories"), "filters: the category list is included")

# scope validation matches the pages'
grp = cats["groups"][0]
nar = client.get(f"/api/scan/filters/stock?group={grp}").get_json()
check(nar["group"] == grp and len(nar["symbols"]) <= len(cats["symbols"]),
      f"?group={grp} narrows the scan ({len(nar['symbols'])} of {len(cats['symbols'])} symbols)")
check(all(s["group"] == grp for s in nar["symbols"].values()),
      "…and every symbol really is in that group")
bogus = client.get("/api/scan/filters/stock?group=NOPE").get_json()
check(bogus["group"] is None, "an unknown group is ignored, as the page route does")
check(client.get("/api/scan/bogus/stock").status_code == 404, "an unknown page is a 404")
check(client.get("/api/scan/filters/bogus").status_code == 404, "an unknown kind is a 404")


# ===========================================================================
print()
print("=" * 74)
print("PART C — in a real browser")
print("=" * 74)

if not BROWSER:
    print("  SKIP  --no-browser")
else:
    import threading
    import werkzeug.serving

    srv = werkzeug.serving.make_server("127.0.0.1", 5098, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with A.app.test_request_context():
        cookie = A.app.session_interface.get_signing_serializer(A.app).dumps(
            {"_user_id": str(uid), "_fresh": True})

    try:
        for what, url in PAGES.items():
            proc = subprocess.run(
                ["node", "scan_check.mjs", "http://127.0.0.1:5098", cookie, url],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=FRONTEND, timeout=600, shell=(os.name == "nt"))
            if not proc.stdout.strip():
                check(False, f"{url}: the browser check ran", proc.stderr[:200])
                continue
            out = json.loads(proc.stdout.strip().splitlines()[-1])
            was_kb, was_nodes = WAS[what]

            check(not out["errors"], f"{url}: no JavaScript errors",
                  "; ".join(out["errors"])[:200])
            check(out["nodes"] < 2500,
                  f"{url}: {out['nodes']} DOM nodes on load (was {was_nodes:,})")
            check(out["interactiveMs"] < 2000,
                  f"{url}: interactive in {out['interactiveMs']} ms "
                  f"(HTML {out['htmlBytes'] / 1024:.0f} kB)")
            check(out["cards"] == 4, f"{url}: the summary cards are rendered")
            check(out["sections"] >= 15, f"{url}: every section header is present "
                                         f"({out['sections']})")
            check(out["tablesMounted"] <= 4,
                  f"{url}: only the sections in view have tables ({out['tablesMounted']})")
            check(out["tablesAfterScroll"] > out["tablesMounted"],
                  f"{url}: more mount as you scroll "
                  f"({out['tablesMounted']} → {out['tablesAfterScroll']}, "
                  f"{out['rowsAfterScroll']} rows)")
            check(out["nodesAfterScroll"] < was_nodes / 2,
                  f"{url}: still {out['nodesAfterScroll']} nodes after scrolling "
                  f"(the whole page used to be {was_nodes:,})")
            drift = abs(out["heightAfterScroll"] - out["height"]) / max(out["height"], 1) * 100
            check(drift < 12,
                  f"{url}: the scrollbar is honest — page height moved {drift:.1f}% "
                  f"as sections mounted")

            s = out["sorted"]
            check(s and all(s["asc"][i - 1] <= v for i, v in enumerate(s["asc"]) if i),
                  f"{url}: قیمت پایانی sorts ascending")
            check(s and all(s["desc"][i - 1] >= v for i, v in enumerate(s["desc"]) if i),
                  f"{url}: …and descending on the second click")

            check(out["documentLoadsOnFilter"] == 0,
                  f"{url}: changing «گروه» does not reload the page")
            check(out["filterMs"] < 2000, f"{url}: …and lands in {out['filterMs']} ms")
            check("group=" in out["filterUrl"],
                  f"{url}: the URL carries the filter ({out['filterUrl']})")
            check(out["sectionsWhenOneSelected"] == 1,
                  f"{url}: choosing one section shows exactly that one "
                  f"({out['sectionsWhenOneSelected']})")
            check(out["documentLoadsOnSelect"] == 0,
                  f"{url}: …with no fetch and no reload (the payload already has it)")
        # -------------------------------------------------------------------
        print()
        print("=" * 74)
        print("PART D — the screener island")
        print("=" * 74)

        api = client.get("/api/screener/stock").get_json()
        scan = db.score_scan("stock")
        check(len(api["rows"]) == len(scan["rows"]),
              f"/api/screener/stock returns the whole ranked list ({len(api['rows'])})")
        check([r["ticker"] for r in api["rows"]] == [r["ticker"] for r in scan["rows"]],
              "…in the scan's own ranking order")
        check(all(r["verdict"]["label"] for r in api["rows"][:20]),
              "…with the server's verdict band on every row")
        band = api["bands"][0]["key"]
        nar = client.get(f"/api/screener/stock?verdict={band}").get_json()
        check(0 < len(nar["rows"]) <= len(api["rows"]),
              f"?verdict={band} narrows it ({len(nar['rows'])} of {len(api['rows'])})")
        check(all(r["verdict"]["key"] == band for r in nar["rows"]),
              "…and every row really is in that band")
        check(client.get("/api/screener/bogus").status_code == 404, "an unknown kind is a 404")

        proc = subprocess.run(
            ["node", "screener_check.mjs", "http://127.0.0.1:5098", cookie],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=FRONTEND, timeout=600, shell=(os.name == "nt"))
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        if not out:
            check(False, "/screener: the browser check ran", proc.stderr[:200])
        else:
            was_kb, was_nodes = WAS["screener"]
            check(not out["errors"], "/screener: no JavaScript errors",
                  "; ".join(out["errors"])[:200])
            check(out["nodes"] < 2000,
                  f"/screener: {out['nodes']} DOM nodes (was {was_nodes:,})")
            check(5 < out["renderedRows"] < 60,
                  f"/screener: only the visible rows exist ({out['renderedRows']} of "
                  f"{len(api['rows'])})")
            check(out["interactiveMs"] < 2000,
                  f"/screener: interactive in {out['interactiveMs']} ms "
                  f"(HTML {out['htmlBytes'] / 1024:.0f} kB, was {was_kb:,} kB)")
            check(out["cards"] == 4, "/screener: the four summary cards are rendered")
            check(out["badges"] == out["renderedRows"] and out["vbadges"] == out["renderedRows"],
                  "/screener: score and verdict badges on every rendered row")
            check(out["bars"] == out["renderedRows"] * 2,
                  f"/screener: the روند and مومنتوم bars are drawn ({out['bars']})")
            check(out["stars"] == out["renderedRows"],
                  "/screener: the watchlist star is on every row")
            check(out["firstRank"] == db.to_persian(1),
                  f"/screener: the «#» column counts from ۱ ({out['firstRank']})")
            asc, desc = out["scoreAsc"], out["scoreDesc"]
            check(all(asc[i - 1] <= v for i, v in enumerate(asc) if i),
                  "/screener: امتیاز sorts ascending")
            check(all(desc[i - 1] >= v for i, v in enumerate(desc) if i),
                  "…and descending on the second click")
            d = out["defaultScores"]
            check(all(d[i - 1] >= v for i, v in enumerate(d) if i),
                  f"/screener: the default order is still highest-score-first "
                  f"({', '.join(f'{v:.1f}' for v in d[:3])})")
            check(out["filtered"] and all("فولاد" in t for t in out["filtered"]),
                  f"/screener: the symbol filter still filters ({', '.join(out['filtered'])})")
            check(out["documentLoadsOnVerdict"] == 0,
                  "/screener: changing «سیگنال» does not reload the page")
            check(out["verdictMs"] < 1500, f"…and lands in {out['verdictMs']} ms")
            check("verdict=" in out["verdictUrl"],
                  f"…with the band in the URL ({out['verdictUrl']})")
            check(len(set(out["verdictLabels"])) == 1,
                  f"…and only that band is listed ({out['verdictLabels'][0] if out['verdictLabels'] else '—'})")
            check(out["deepFirstIndex"] > 50 and out["deepNodes"] < 3000,
                  f"/screener: scrolling deep renders row {out['deepFirstIndex']} onward, "
                  f"DOM stays at {out['deepNodes']} nodes")
    finally:
        srv.shutdown()


print()
print("=" * 74)
print(("FAILED: " + ", ".join(FAIL)) if FAIL else "ALL CHECKS PASSED")
print("=" * 74)
sys.exit(1 if FAIL else 0)
