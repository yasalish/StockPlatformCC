"""
verify_new_tab.py — clicking a symbol opens its page in a NEW TAB

Every table on the platform navigated in place: reading six symbols off
/performance meant six round trips through a list whose filter, sort and scroll
position the back button rebuilds from the top. A row now opens the security in
a new tab and leaves the list exactly as it was.

  A  Source — no table still navigates with location.href, and the ticker and
     name are real <a target="_blank" rel="noopener"> elements rather than
     JavaScript-only click targets.
  B  Rendered — the pages really ship those anchors (the Vue bundles are built
     artifacts, so this is the check that the build was actually run).
  C  Browser — node + Chrome/Edge, on all six pages: click the ticker, the name
     and a plain cell; assert a tab opened at the right URL, that EXACTLY one
     did, and that the list page itself never moved. The watchlist star is
     clicked too — it must still toggle, not open anything.

Needs: the «Stock» database. Part C also needs node and Chrome or Edge; pass
--no-browser to skip it.

Run:  python verify_new_tab.py
"""
import json
import os
import re
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

#  The report prints Persian tickers and box-drawing characters; a Windows
#  console defaults to cp1252 and would raise UnicodeEncodeError on the first
#  one, losing the whole run's results to a traceback.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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


# ===========================================================================
print("=" * 74)
print("PART A — no table navigates in place any more")
print("=" * 74)

GRIDS = ["frontend/src/MarketGrid.vue", "frontend/src/PerfGrid.vue",
         "frontend/src/ScreenerGrid.vue", "frontend/src/ScanTable.vue"]

for g in GRIDS:
    src = read(g)
    name = os.path.basename(g)
    check("window.location.href" not in src,
          f"{name}: no in-place navigation left")
    check('target="_blank"' in src and 'rel="noopener"' in src,
          f"{name}: the ticker is a real new-tab link")
    check('from "./nav"' in src and "openDetail(" in src,
          f"{name}: row clicks go through the shared helper")
    # @click.stop on the anchor is what stops the row handler opening a SECOND
    # tab on top of the one the link itself opens.
    check("@click.stop" in src, f"{name}: the link stops the row handler")

nav = read("frontend/src/nav.ts")
check('window.open(href, "_blank")' in nav and "win.opener = null" in nav,
      "nav.ts severs `opener` instead of passing the noopener feature",
      "window.open(…, 'noopener') returns null, which cannot be told from a "
      "blocked popup — the fallback would then fire on every click")
check("else window.location.href = href" in nav,
      "…and a blocked popup still lands somewhere rather than swallowing the click")

for t in ("templates/watchlist.html", "templates/_dashboard_data.html"):
    src = read(t)
    check("onclick=\"location.href" not in src,
          f"{os.path.basename(t)}: the inline onclick navigation is gone")
    check("data-href=" in src, f"{os.path.basename(t)}: rows carry data-href")
    check('class="row-link"' in src and 'target="_blank"' in src,
          f"{os.path.basename(t)}: the ticker is a link")

dash = read("templates/_dashboard_data.html")
check(dash.count('href="{{ detail_base }}{{ r.id }}" target="_blank"') == 2,
      "_dashboard_data.html: the برترین‌ها/ضعیف‌ترین‌ها mini-lists open a new tab too",
      "they sit directly above tables that do, and they are the same click")

appjs = read("static/js/app.js")
check("initRowLinks" in appjs and "tr.clickable[data-href]" in appjs,
      "app.js has the one delegated handler the Jinja tables share")
check('e.target.closest("a, button, .watch-star")' in appjs,
      "…and it yields to the star and to the links inside the row")
check(".row-link" in read("static/css/ui.css"),
      "the links are styled as table text, not as browser-default links")


# ===========================================================================
print()
print("=" * 74)
print("PART B — the built bundles and the rendered pages carry it")
print("=" * 74)

import app as A
import db

uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")["id"]
client = A.app.test_client()
with client.session_transaction() as s:
    s["_user_id"] = str(uid)
    s["_fresh"] = True

#  /watchlist shows the USER'S OWN symbols, so on an empty watchlist there is
#  nothing to render and nothing to click, and every check below it would pass
#  on no rows at all. Seed one of each kind — only what is missing — and put the
#  watchlist back exactly as it was found, whatever happens to this run.
seeded = []
have = set(db.watch_keys(uid))
for _kind, _tbl, _idcol in (("stock", "stocks", "stockid"), ("etf", "etf", "id")):
    _r = db._one(f"SELECT {_idcol} AS eid, ticker FROM {_tbl} ORDER BY {_idcol} LIMIT 1")
    if _r and f"{_kind}:{_r['ticker']}" not in have:
        db.toggle_watch(uid, _kind, _r["ticker"], _r["eid"])
        seeded.append((_kind, _r["ticker"], _r["eid"]))
if seeded:
    print("  ..    watchlist seeded with " + "، ".join(t for _, t, _ in seeded)
          + " for the duration of this run")

import atexit
atexit.register(lambda: [db.toggle_watch(uid, k, t, e) for k, t, e in seeded])

# The islands are compiled: source alone proves nothing about what the browser
# runs. Vue compiles :href/target/rel into a props object, so look for the
# marker the compiler cannot drop rather than for the template text.
for bundle in ("market", "perf", "screener", "scan"):
    js = read(f"static/dist/{bundle}.js")
    built = os.path.getmtime(f"static/dist/{bundle}.js")
    check("row-link" in js and "noopener" in js,
          f"static/dist/{bundle}.js is built from the new source",
          time.strftime("%Y-%m-%d %H:%M", time.localtime(built)))

for path, must in (("/watchlist", 'class="row-link"'),
                   ("/dashboard/data", 'class="row-link"')):
    body = client.get(path).get_data(as_text=True)
    check(must in body, f"{path} renders the symbol as a link")
    check("onclick=\"location.href" not in body,
          f"{path} has no inline navigation left")


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

    proc = None
    try:
        proc = subprocess.run(
            ["node", "newtab_check.mjs", "http://127.0.0.1:5098", cookie],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=FRONTEND, timeout=900, shell=(os.name == "nt"))
        out = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
    except Exception as e:
        out = {}
        check(False, "the browser check ran",
              f"{e} {proc.stderr[:300] if proc else ''}")
    finally:
        srv.shutdown()

    if out:
        check(not out["errors"], "no JavaScript errors on any page",
              "; ".join(out["errors"])[:300])

        DETAIL = re.compile(r"/(stock|etf)/\d+$")
        for key, rep in out["pages"].items():
            print(f"\n  — {key} ({rep['path']}, first row: "
                  f"{(rep.get('rowTicker') or '?').strip()})")
            if rep.get("error"):
                check(False, f"{key}: the page loaded", rep["error"])
                continue
            for what in ("ticker", "name", "row"):
                r = rep.get(what) or {}
                if not r.get("found"):
                    # /screener and the dashboard lists have no name column;
                    # a missing target there is the table, not the feature.
                    print(f"      skip  {what}: no such cell on this table")
                    continue
                check(r["openedCount"] == 1,
                      f"{key}: clicking the {what} opens exactly one tab",
                      f"opened {r['openedCount']}")
                check(bool(r["openedUrl"]) and DETAIL.search(r["openedUrl"] or ""),
                      f"{key}: …and it is the security page",
                      str(r["openedUrl"]))
                if r.get("expected"):
                    check(r["openedUrl"] == r["expected"],
                          f"{key}: …the one the {what} points at",
                          f"{r['openedUrl']} vs {r['expected']}")
                check(not r["listNavigated"],
                      f"{key}: the list itself stayed put", r["listUrl"])
            if rep.get("starSkipped"):
                # Un-starring on /watchlist removes the row on purpose, so
                # there is nothing to click back on. Tested on the other pages.
                print("      skip  star: un-starring removes the row here")
            elif "starOpened" in rep:
                check(rep["starOpened"] == 0 and not rep["starNavigated"],
                      f"{key}: the دیده‌بان star still just toggles")


# ===========================================================================
print()
print("=" * 74)
print(f"{len(FAIL)} FAILED" if FAIL else "ALL CHECKS PASSED")
for f in FAIL:
    print("  x", f)
print("=" * 74)
sys.exit(1 if FAIL else 0)
