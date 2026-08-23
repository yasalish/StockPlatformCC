"""
verify_order09.py — verification for Order 09
(نوار پیمایش بالای جدول‌ها · تنظیمات · پوسته‌ها · نقشهٔ بازار · نبض بازار)

  A  Scrollbars — every wide table carries a mirrored horizontal scrollbar ABOVE
     it, exactly one per table, thicker than the browser default, sticky under
     the header, and it really scrolls the table.
  B  Themes — six of them; every block redefines every custom property `:root`
     defines; the light theme is unchanged to the pixel by the literal-to-token
     refactor; an unknown id falls back to light.
  C  Settings — prefs.py's contract, the user_prefs table, the API round trip,
     and that every preference on the screen actually drives something.
  D  Pages — /settings, /help, /about, /heatmap render, are login-gated, and the
     nav/user-menu/footer reach them.
  E  Market map & breadth — the data functions, the endpoint shape, and the
     drawn map in a browser.
  F  Regression — nothing that existed before order 09 changed: the old routes,
     the island bundles, the watchlist star, the Excel export.

Needs: the «Stock» database, node, and Chrome or Edge.
Run:  python verify_order09.py            (skip the browser with --no-browser)
"""
import io
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".tools"))
os.chdir(HERE)

BROWSER = "--no-browser" not in sys.argv
# --headed runs a visible Chrome. Only one check needs it, and it is a real one:
# headless Chrome draws OVERLAY scrollbars, which ignore ::-webkit-scrollbar
# sizing, so the thickness of the mirrored bar cannot be measured there at all.
HEADED = "--headed" in sys.argv
FAIL = []


def check(ok, label, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{('  — ' + detail) if detail else ''}")
    if not ok:
        FAIL.append(label)


def read(p):
    with io.open(p, encoding="utf-8") as f:
        return f.read()


FRONTEND = os.path.join(HERE, "frontend")
SHOTS = os.path.join(HERE, ".tools", "shots09")
os.makedirs(SHOTS, exist_ok=True)

ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
       "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
       "MSYS_NO_PATHCONV": "1"}

CSS = read("static/css/style.css")
APP = read("app.py")

import prefs                                     # noqa: E402
import db                                        # noqa: E402

# ===========================================================================
print("=" * 74)
print("PART A — the mirrored scrollbar above every table")
print("=" * 74)

tables_js = read("static/js/tables.js")
check(os.path.exists("static/js/tables.js"), "static/js/tables.js exists")
check("table-scroll-top" in tables_js and "insertBefore" in tables_js,
      "…and inserts the mirror BEFORE the scroller, not inside it")
check("MutationObserver" in tables_js,
      "…watches the DOM, so the Vue islands' tables are decorated too")
check("ResizeObserver" in tables_js,
      "…re-measures when a column width settles")
check("scrollWidth" in tables_js and "clientWidth" in tables_js,
      "…hides itself when the table already fits")

check("--sbar-h" in CSS, "--sbar-h controls the height of both bars")
sizes = dict(re.findall(r'\[data-sbar="(\w+)"\]\{\s*--sbar-h:(\d+)px', CSS))
check(sizes == {"md": "14", "lg": "20", "xl": "28"},
      "three thicknesses are offered", str(sizes))
default_h = re.search(r":root\{ --sbar-h:(\d+)px", CSS)
check(default_h and int(default_h.group(1)) >= 14,
      "the DEFAULT is thicker than the browser's ~8px",
      f"{default_h.group(1) if default_h else '?'}px")
check("::-webkit-scrollbar-thumb" in CSS and "--sbar-thumb" in CSS,
      "the thumb is themed, not left at the platform grey")
check("position:sticky;top:var(--topbar-h)" in CSS.replace(" ", "").replace("\n", ""),
      "the mirror is sticky under the site header")

# The islands must not carry a second implementation: two bars stacked on top of
# each other is what the old PerfGrid copy would produce now.
perf_vue = read("frontend/src/PerfGrid.vue")
check("table-scroll-top" not in perf_vue,
      "PerfGrid.vue no longer builds its own bar (it would be the second one)")
check(os.path.exists("static/dist/perf.js") and
      "table-scroll-top" not in read("static/dist/perf.js"),
      "…and the built bundle was rebuilt from that source")

# ===========================================================================
print()
print("=" * 74)
print("PART B — the theme catalogue")
print("=" * 74)

# Not a fixed count. This asserted 6, and eight more themes were added — a
# test that has to be edited every time the catalogue grows is a test that
# will be edited without being read. What matters is that there is a real
# catalogue and that both families are represented.
check(len(prefs.THEMES) >= 6, f"{len(prefs.THEMES)} themes in the catalogue")
_fams = {t["family"] for t in prefs.THEMES}
check(_fams == {"light", "dark"},
      "…covering both families", ", ".join(sorted(_fams)))
root_block = re.search(r":root\{\s*color-scheme:light;(.*?)\n\}", CSS, re.S)
check(bool(root_block), "the :root palette is intact")
root_props = set(re.findall(r"(--[a-z0-9-]+)\s*:", root_block.group(1)))
check(len(root_props) >= 25, f"{len(root_props)} custom properties define a theme")

for t in prefs.THEMES:
    if t["id"] == "light":
        # Only a real selector counts. The orientation comment above the palette
        # names [data-theme="light"] precisely in order to say it does NOT exist.
        check(not re.search(r'^\[data-theme="light"\]', CSS, re.M),
              "«روشن» IS :root — so an unknown data-theme falls back to it")
        continue
    block = re.search(r'\[data-theme="%s"\]\{(.*?)\n\}' % t["id"], CSS, re.S)
    if not block:
        check(False, f'theme «{t["label"]}» has a stylesheet block')
        continue
    have = set(re.findall(r"(--[a-z0-9-]+)\s*:", block.group(1)))
    missing = root_props - have
    check(not missing, f'theme «{t["label"]}» redefines every property',
          f"missing: {sorted(missing)}" if missing else "")

# The refactor that had to precede the themes: no light-surface literal may be
# left as the VALUE of an ordinary property, or it stays white on a dark page.
# A custom property may hold one — that is exactly what a token is.
#
# Comments are stripped first: the orientation note above the palette lists the
# very literals it tells you not to use, and matching those would make this
# check impossible to satisfy honestly. Hex words are matched whole, so #ffffff
# is not reported as a stray #fff.
LIGHT_LITERALS = ("#fff", "#ffffff", "#f6f2e6", "#f4ecd2", "#fff8e6", "#fff7f0",
                  "#fff7e8", "#fff2d6", "#f0ece0", "#cfd4da")
css_nocomments = re.sub(r"/\*.*?\*/", " ", CSS, flags=re.S)
leftovers = []
for prop, value in re.findall(r"([-a-zA-Z][-a-zA-Z0-9]*)\s*:\s*([^;{}]+)", css_nocomments):
    if prop.startswith("--"):
        continue
    low = value.strip().lower()
    for literal in LIGHT_LITERALS:
        if re.search(r"(?<![0-9a-f])" + re.escape(literal) + r"(?![0-9a-f])", low):
            leftovers.append(f"{prop}: {low[:50]}")
            break
check(not leftovers, "no light-surface literal is left as a rule's value",
      " | ".join(leftovers[:3]))

check("[data-updown=\"colorblind\"]" in CSS,
      "the colour-blind palette composes with every theme")
check("color-scheme:dark" in CSS,
      "dark themes declare color-scheme, so native controls follow")

base = read("templates/base.html")
check("boursenegar-theme" in base and "data-prefs" in base,
      "base.html applies the theme pre-paint, inline, in <head>")
head = base[:base.index("</head>")]
check(head.index("<script>") < head.index("css/style.css"),
      "…and the inline script runs BEFORE the stylesheet loads")

# ===========================================================================
print()
print("=" * 74)
print("PART C — تنظیمات: the preference catalogue and its storage")
print("=" * 74)

check(prefs.normalize({"theme": "neon"}) == {},
      "an invalid value is dropped, never raised (a stale tab must not 500)")
check(prefs.normalize({"rows_per_page": "100"})["rows_per_page"] == 100,
      "a form's string is coerced to the stored type")
check(prefs.family_of("nope") == "light",
      "an unknown theme reads as LIGHT — what :root actually renders")
check(set(prefs.client_payload({})) == set(prefs.DEFAULTS),
      "the browser payload carries the settings and nothing else")

cols = {r["column_name"] for r in db._rows(
    "SELECT column_name FROM information_schema.columns WHERE table_name = 'user_prefs'")}
check(cols, "the user_prefs table exists")
missing_cols = set(prefs.DEFAULTS) - cols
check(not missing_cols, "…with a column for every preference", str(sorted(missing_cols)))

# Column defaults must equal prefs.DEFAULTS, or a fresh account and a
# saved-then-reset account render differently.
defaults = {r["column_name"]: r["column_default"] for r in db._rows(
    "SELECT column_name, column_default FROM information_schema.columns "
    "WHERE table_name = 'user_prefs'")}
mismatch = []
for key, want in prefs.DEFAULTS.items():
    raw = (defaults.get(key) or "").split("::")[0].strip("'")
    got = {"true": True, "false": False}.get(raw, raw)
    if isinstance(want, bool):
        ok = got is want
    elif isinstance(want, int):
        ok = str(want) == str(got)
    else:
        ok = str(want) == str(got)
    if not ok:
        mismatch.append(f"{key}: column={got!r} prefs={want!r}")
check(not mismatch, "…and every column default equals prefs.DEFAULTS",
      " | ".join(mismatch))

check(os.path.exists("migrations/versions/0005_user_prefs_and_screens.py"),
      "the Alembic migration is written (0005 → 0004)")
mig = read("migrations/versions/0005_user_prefs_and_screens.py")
check('down_revision = "0004"' in mig, "…and chains onto the existing head")
check("saved_screens" in mig and "user_prefs" in mig, "…and creates both tables")

# --- the API round trip, through the real app, as a real user ---------------
import app as webapp                             # noqa: E402
import bn_liveserver as L                        # noqa: E402

anon = webapp.app.test_client()
for path in ("/settings", "/help", "/about", "/heatmap"):
    r = anon.get(path)
    check(r.status_code in (302, 401), f"{path} is behind the login gate",
          f"status {r.status_code}")

made_user = L.ensure_user()
uid = db._one("SELECT id FROM users WHERE username = %s", (L.USERNAME,))["id"]
try:
    with webapp.app.test_client() as c:
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True

        r = c.get("/api/me/prefs")
        #  Not the literal "light". This asserted the default theme's VALUE, so
        #  changing the app's default (light -> dark with the redesign) failed a
        #  check about the endpoint rather than about the default. What the
        #  endpoint promises is that a brand-new account reads back
        #  prefs.DEFAULTS — whatever those currently are.
        check(r.status_code == 200 and r.get_json()["theme"] == prefs.DEFAULTS["theme"],
              "GET /api/me/prefs answers with the defaults for a new account",
              f'got {r.get_json().get("theme")!r}, DEFAULTS says {prefs.DEFAULTS["theme"]!r}')

        r = c.patch("/api/me/prefs", json={"theme": "midnight", "zebra": True,
                                           "rows_per_page": "200", "bogus": 1})
        body = r.get_json()
        check(r.status_code == 200 and body["theme"] == "midnight" and body["zebra"] is True
              and body["rows_per_page"] == 200 and "bogus" not in body,
              "PATCH stores the known keys, coerces types and drops the rest")

        r = c.patch("/api/me/prefs", json={"theme": "no-such-theme"})
        check(r.status_code == 400 and "شناخته" in r.get_json().get("error", ""),
              "…and a payload with nothing recognisable gets a Persian message")

        r = c.get("/settings")
        html = r.get_data(as_text=True)
        check(r.status_code == 200, "/settings renders for a signed-in user")
        check('data-theme="midnight"' in html,
              "…with the saved theme already on <html> (no flash of the wrong one)")
        check(all(f'data-pref="{k}"' in html or k == "theme" for k in prefs.DEFAULTS),
              "…and every preference has a control on the page")
        check(html.count("data-theme-id=") == len(prefs.THEMES),
      f"…and a swatch for every one of the {len(prefs.THEMES)} themes",
      f'rendered {html.count("data-theme-id=")}')

        # saved screens
        r = c.post("/api/me/screens", json={"name": "فلزات یک‌ماهه", "kind": "stock",
                                            "page": "market", "query": "group=فلزات اساسی"})
        check(r.status_code == 201, "a filter preset can be saved")
        screen_id = r.get_json().get("id")
        r = c.post("/api/me/screens", json={"name": "فلزات یک‌ماهه", "kind": "stock",
                                            "page": "market", "query": "x=1"})
        check(r.status_code == 409, "…and the same name twice is a 409, not a duplicate")
        r = c.get("/api/me/screens")
        check(len(r.get_json()["screens"]) == 1, "…and it comes back in the list")
        check(c.delete(f"/api/me/screens/{screen_id}").status_code == 200,
              "…and can be deleted")
        check(c.delete("/api/me/screens/999999").status_code == 404,
              "…while someone else's id answers 404, not 403")

        # the map and breadth endpoints
        r = c.get("/api/heatmap/stock?period=d1")
        payload = r.get_json()
        check(r.status_code == 200 and len(payload["rows"]) > 100,
              f"/api/heatmap/stock returns {len(payload.get('rows', []))} symbols")
        check(payload["rows"][0].keys() == {"t", "n", "g", "c", "v", "p", "id"},
              "…with only the fields a tile draws")
        check(len(payload["groups"]) > 3 and payload["groups"][0]["value"] >=
              payload["groups"][-1]["value"],
              "…and groups ordered by traded value")
        check(c.get("/api/heatmap/nope").status_code == 404,
              "…and an unknown kind is a 404")

        r = c.get("/api/breadth/stock")
        b = r.get_json()
        check(r.status_code == 200 and b["up"] + b["down"] + b["flat"] == b["measured"],
              "/api/breadth adds up: up + down + flat == measured")

        for path in ("/help", "/about", "/heatmap", "/dashboard/data"):
            check(c.get(path).status_code == 200, f"{path} renders")

        # reset, so a re-run of this script starts from the defaults
        c.post("/api/me/prefs/reset")

    # =======================================================================
    print()
    print("=" * 74)
    print("PART D — the data behind نقشهٔ بازار و نبض بازار")
    print("=" * 74)

    t0 = time.perf_counter()
    session = db.last_session("stock")
    cold = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    db.last_session("stock")
    warm = (time.perf_counter() - t0) * 1000
    check(len(session) > 100, f"last_session() reads {len(session)} symbols "
                              f"({cold:.0f} ms cold, {warm:.0f} ms warm)")
    check(warm < 120, "…and is cached like every other analytic", f"{warm:.0f} ms")
    sample = next(iter(session.values()))
    check(set(sample) == {"jdate", "value", "volume", "chg"},
          "…with the last session's value/volume and the one-day change")

    rows, as_of, groups = db.market_map("stock", period="p20")
    check(len(rows) > 100 and len(groups) > 3,
          f"market_map(): {len(rows)} symbols in {len(groups)} groups as of {as_of}")
    check(all(g["avg"] is None or -100 < g["avg"] < 500 for g in groups),
          "…and every group average is a sane percentage")
    # The weighting is the point: a value-weighted average must differ from the
    # plain mean, or the tiles are lying about which symbols moved the group.
    big = max(groups, key=lambda g: g["count"])
    plain = [r["chg"] for r in rows if r["group"] == big["group"] and r["chg"] is not None]
    plain_avg = sum(plain) / len(plain) if plain else 0
    check(abs((big["avg"] or 0) - plain_avg) > 1e-9,
          "…and the group average is value-weighted, not a plain mean",
          f"«{big['group']}» weighted={big['avg']:.2f}٪ plain={plain_avg:.2f}٪")

    breadth = db.market_breadth("stock", period="d1")
    check(breadth["up"] + breadth["down"] + breadth["flat"] == breadth["measured"],
          f"breadth: {breadth['up']} صعودی / {breadth['down']} نزولی / "
          f"{breadth['flat']} بدون تغییر")
    check(len(breadth["best"]) == 5 and breadth["best"][0]["chg"] >= breadth["best"][-1]["chg"],
          "…and the extremes are ordered")

    # =======================================================================
    print()
    print("=" * 74)
    print("PART E — regression: nothing that worked before order 09 moved")
    print("=" * 74)

    for endpoint in ("index", "dashboard", "stocks_page", "etfs_page", "screener_page",
                     "performance_page", "strategies_page", "filters_page",
                     "watchlist_page", "update_page", "stock_detail", "etf_detail",
                     "export_gainer", "api_market", "api_screener", "api_scan",
                     "api_performance", "api_search", "api_ohlc", "api_watchlist_toggle"):
        check(any(r.endpoint == endpoint for r in webapp.app.url_map.iter_rules()),
              f"route {endpoint} still exists")

    appjs = read("static/js/app.js")
    check(all(name in appjs for name in
              ("initSearch", "initTable", "initTabs", "priceChart", "toggleWatch",
               "initNavLoader")),
          "app.js still exports everything it did — order 09 put its behaviour in "
          "new files beside it rather than restructuring it")
    check(subprocess.run(["git", "diff", "--quiet", "--", "static/js/app.js"],
                         capture_output=True).returncode == 0,
          "…in fact app.js is byte-identical to the committed version")
    for bundle in ("market.js", "perf.js", "scan.js", "screener.js"):
        check(os.path.exists(f"static/dist/{bundle}"), f"island bundle {bundle} is built")

    # =======================================================================
    if not BROWSER:
        print("\n(--no-browser: skipping the rendered checks)")
    else:
        print()
        print("=" * 74)
        print("PART F — rendered in a real browser")
        print("=" * 74)

        srv = L.LiveServer()
        srv.start()
        try:
            res = subprocess.run(
                ["node", "order09_check.mjs", srv.base, L.USERNAME, L.PASSWORD,
                 SHOTS.replace("\\", "/")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=FRONTEND, env={**ENV, **({"HEADED": "1"} if HEADED else {})},
                timeout=900)
            if res.returncode != 0:
                check(False, "the browser probe ran", res.stderr[-700:])
                data = {}
            else:
                data = json.loads(res.stdout[res.stdout.index("{"):])
        finally:
            srv.stop()

        if data:
            check(not data["errors"], "no JavaScript errors on any page",
                  " | ".join(data["errors"][:2]))
            check(not data.get("failedRequests"),
                  "no request on any page 404s",
                  " | ".join(data.get("failedRequests", [])[:3]))

            for path, info in data["pages"].items():
                if info["wide"]:
                    check(info["visibleMirrors"] >= 1,
                          f"{path}: a mirror bar is shown above the wide table",
                          f"{info['wide']} wide, {info['visibleMirrors']} bars")
                check(info["bars"] == info["scrollers"],
                      f"{path}: exactly one bar per table (no duplicates)",
                      f"{info['bars']} bars / {info['scrollers']} tables")
                check(info["barsAboveATable"] == info["bars"],
                      f"{path}: every bar sits directly above its table")
                check(info["sticky"] in (None, "sticky"),
                      f"{path}: the bar is sticky")
                # A sticky <thead> inside a horizontally-scrolling container is
                # offset from that container, not the viewport: any non-zero
                # `top` prints the header over the first rows of the table.
                check(info["headAboveBody"] in (None, True),
                      f"{path}: the table header is above the first row, not over it",
                      f"thead top={info['headStickyTop']}")

            stocks = data["pages"].get("/stocks", {})
            check(stocks.get("sync", {}) and stocks["sync"]["moved"],
                  "dragging the mirror scrolls the table under it",
                  json.dumps(stocks.get("sync")))
            check(stocks.get("sync", {}) and stocks["sync"]["mirrorFollows"],
                  "…and scrolling the table moves the mirror back")
            check(stocks.get("tools", 0) >= 1, "the table toolbar is rendered")
            check(stocks.get("sbarHeight") == "20px",
                  "…at the default 20px thickness", str(stocks.get("sbarHeight")))
            check((stocks.get("mirrorHeight") or 0) >= 18,
                  "…and the bar's box is really that tall ON SCREEN, not just in CSS",
                  f"{stocks.get('mirrorHeight')}px measured")
            if data.get("headed"):
                check((stocks.get("scrollbarPx") or 0) >= 14,
                      "…and a classic 20px scrollbar is actually painted in it",
                      f"{stocks.get('scrollbarPx')}px of the box is scrollbar")
            else:
                print(f"  NOTE  headless Chrome draws overlay scrollbars "
                      f"(scrollbarPx={stocks.get('scrollbarPx')}); re-run with "
                      f"--headed to measure the painted bar as a user sees it")
            check(stocks.get("topbarVar", "0px") != "0px",
                  "…and the header height was measured, not guessed",
                  str(stocks.get("topbarVar")))

            perf = data["pages"].get("/performance", {})
            check(perf.get("bars") == perf.get("scrollers") and perf.get("bars", 0) >= 1,
                  "/performance has ONE bar (the island's own copy is gone)",
                  f"{perf.get('bars')} bars")

            b = data.get("breadth") or {}
            check(b.get("present"), "the نبض بازار panel renders on the dashboard")
            check(b.get("up", 0) > 0 and b.get("down", 0) > 0,
                  "…with both segments drawn from the real counts")
            check(b.get("lists", 0) >= 10, "…plus the best/worst lists")

            hm = data.get("heatmap") or {}
            check(hm.get("tiles", 0) > 100,
                  f"نقشهٔ بازار draws {hm.get('tiles')} tiles in {hm.get('groups')} groups")
            check(hm.get("distinctColours", 0) > 8,
                  "…coloured on a real scale, not two flat colours",
                  f"{hm.get('distinctColours')} distinct colours")
            check(hm.get("distinctSizes", 0) > 5,
                  "…and sized by traded value, not all equal",
                  f"{hm.get('distinctSizes')} distinct sizes")
            check(hm.get("links") == hm.get("tiles"), "…every tile links to its symbol")
            check(0 < data.get("heatmapFiltered", 0) < hm.get("tiles", 1),
                  "…and the filter box narrows the map",
                  f"{data.get('heatmapFiltered')} of {hm.get('tiles')}")

            st = data.get("settings") or {}
            check(st.get("swatches") == len(prefs.THEMES),
                  f"the settings screen offers all {len(prefs.THEMES)} themes",
                  str(st.get("swatches")))
            check(st.get("switches", 0) >= 5 and st.get("segments", 0) >= 6,
                  "…plus the switches and segmented controls")
            check(st.get("active") == 1, "…with the current theme marked")

            #  These two used to assert `bodyBg == "rgb(11, 16, 32)"`, the exact
            #  value of the old «نیمه‌شب» surface. That is a test of the palette,
            #  not of the feature, and it failed the moment the palette was
            #  redesigned even though theme switching worked perfectly. What the
            #  feature promises is that the surface becomes DARK — so measure
            #  that, and it survives the next redesign too.
            def _dark(css_rgb):
                """True if an 'rgb(r, g, b)' string is a dark surface."""
                try:
                    r, g, b = (int(n) for n in
                               css_rgb[css_rgb.index("(") + 1:
                                       css_rgb.index(")")].split(",")[:3])
                except Exception:
                    return False
                #  Rec. 709 relative luminance. A dark theme's page surface sits
                #  far below 60/255; every light theme here is above 230.
                return (0.2126 * r + 0.7152 * g + 0.0722 * b) < 60

            click = data.get("afterClick") or {}
            check(click.get("theme") == "midnight", "clicking a swatch applies the theme")
            check(_dark(click.get("bodyBg") or ""),
                  "…and the page surface really goes dark",
                  str(click.get("bodyBg")))

            reload_ = data.get("afterReload") or {}
            check(reload_.get("theme") == "midnight",
                  "…and it survives a reload, from the account (not localStorage)")
            check(reload_.get("fromServer") == "server",
                  "…rendered by the server onto <html>, so there is no flash")

            dark = data.get("darkTable") or {}
            check(dark.get("theme") == "midnight", "the theme follows onto /stocks")
            check(_dark(dark.get("bodyBg") or ""),
                  "…the table page is dark", str(dark.get("bodyBg")))
            check(dark.get("cellInk") not in (None, "rgb(28, 40, 48)"),
                  "…and the table ink inverted with it (no dark-on-dark text)",
                  str(dark.get("cellInk")))
            check(dark.get("inputBg") not in (None, "rgb(255, 255, 255)"),
                  "…and the form controls are not still white",
                  str(dark.get("inputBg")))

            tabs = data.get("detailTabs") or {}
            check(tabs.get("bareTables") == 0,
                  "on the symbol page every table is in a scroll container "
                  "(none left without a bar)",
                  f"{tabs.get('bareTables')} bare tables")
            check(tabs.get("bars") == tabs.get("scrollers"),
                  "…each with exactly one bar",
                  f"{tabs.get('bars')} bars / {tabs.get('scrollers')} scrollers")

            detail = data.get("detail") or {}
            check(detail.get("rows") == 50,
                  "the OHLCV history table pages at «تعداد ردیف در هر صفحه» (۵۰)",
                  f"{detail.get('rows')} rows rendered")
            check(detail.get("stickyHead") == "sticky",
                  "…and its header is sticky while «سرستون چسبان» is on",
                  str(detail.get("stickyHead")))
            check(data.get("recentsAfterVisit", 0) >= 1,
                  "«بازدیدهای اخیر» remembers a symbol you opened",
                  f"{data.get('recentsAfterVisit')} chips")

            applied = data.get("prefsApplied") or {}
            check(applied.get("sbar") == "28px", "«خیلی ضخیم» thickens the scrollbar")
            check(applied.get("density") == "compact", "«فشرده» is applied")
            check(applied.get("persianDigitsLeft") is False,
                  "«لاتین» rewrites the Persian digits on the page")

            carried = data.get("prefsCarried") or {}
            check(carried.get("sbar") == "28px" and carried.get("density") == "compact",
                  "…and every one of them follows onto the next page, from the database")
            check(carried.get("persianDigitsInTable") is False,
                  "…including inside a Vue island that rendered after the switch")
            check(carried.get("cellPadding") == "5px",
                  "…and «فشرده» really tightens the rows", str(carried.get("cellPadding")))

            print(f"\n  screenshots: {SHOTS}")
            for s in data.get("shots", []):
                print("    " + os.path.basename(s))
finally:
    if made_user:
        conn = db.get_db()
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username = %s", (L.USERNAME,))
        finally:
            conn.autocommit = False
            db.release(conn)

# ===========================================================================
print()
print("=" * 74)
if FAIL:
    print(f"{len(FAIL)} CHECK(S) FAILED")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("ALL CHECKS PASSED")
print("=" * 74)
