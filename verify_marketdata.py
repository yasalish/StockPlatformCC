"""
verify_marketdata.py — شاخص، حقیقی/حقوقی، دیده‌بان و دلار

    python verify_marketdata.py                # everything except the network
    python verify_marketdata.py --live         # …and hit TSETMC for real
    python verify_marketdata.py --no-browser   # skip the Chrome/Edge part

Five parts, in the order a failure is cheapest to diagnose:

  A  the schema and the arithmetic — the tables, and the four derived numbers
     the money-flow page is built on;
  B  the fetch layer — the work lists, the dispatch, and the two facts about
     finpy_tse that this integration DEPENDS on and cannot see fail quietly;
  C  the routes, the pages and the navigation;
  D  --live only: one real fetch of each dataset;
  E  a real browser — the grouped nav menus and the order-book drawers, neither
     of which a server-rendered assertion can see.

PART B IS THE POINT OF THIS FILE.

Two things about the installed finpy_tse are load-bearing here, undocumented,
and would break silently:

  1. Get_RI_History RAISES with its own defaults. `alt=False` — the default —
     dies with `AttributeError: Can only use .dt accessor with datetimelike
     values` on every call, because TSETMC's primary endpoint now returns a
     shape finpy's date handling cannot parse. Every حقیقی/حقوقی row in this
     platform exists because we pass alt=True. If a future finpy flips that
     default, or a refactor here drops the argument, the whole dataset stops
     arriving and the only symptom is an empty page.

  2. Get_SectorIndex_History matches sector names against a HARD-CODED list of
     forty strings, and on a miss falls back to scraping a Google search page
     for the web-id. The printed guide says it matches loosely; it does not. If
     market_data.SECTOR_INDICES drifts from finpy's list, forty index fetches
     silently become forty Google scrapes.

Both are checked here against the installed package, not against the PDF.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
LIVE = "--live" in sys.argv
PASS = FAIL = 0


def check(ok, label, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok    {label}" + (f"   [{detail}]" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"   [{detail}]" if detail else ""))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


print("=" * 74)
print("PART A — the schema and the four derived numbers")
print("=" * 74)

import db                                          # noqa: E402
import market_data as md                           # noqa: E402

md.ensure_tables()

for table, keys in (("ri_history", ("ticker", "date")),
                    ("index_history", ("index_key", "date")),
                    ("usd_rial", ("date",)),
                    ("market_snapshot", ("ticker", "j_date")),
                    ("order_book", ("ticker", "depth")),
                    ("queue_history", ("ticker", "date")),
                    ("intraday_orderbook",
                     ("ticker", "date", "time", "seq", "depth")),
                    ("intraday_trades", ("ticker", "date", "time", "seq")),
                    ("shareholders", ("ticker", "holder"))):
    exists = db._one("SELECT to_regclass(%s) t", (f"public.{table}",))["t"]
    check(bool(exists), f"{table} exists")
    if not exists:
        continue
    # The primary key is what makes a re-fetch a replace rather than a
    # duplicate. Under acks_late a worker killed after writing and before
    # acknowledging is handed the same work again; without the key that run
    # doubles the rows and nothing complains.
    cols = [r["a"] for r in db._rows(
        """SELECT a.attname AS a
             FROM pg_index i
             JOIN pg_attribute a ON a.attrelid = i.indrelid
                                AND a.attnum = ANY(i.indkey)
            WHERE i.indrelid = %s::regclass AND i.indisprimary""", (table,))]
    check(set(cols) == set(keys),
          f"{table} is keyed on {', '.join(keys)}", ", ".join(sorted(cols)) or "none")

# ---------------------------------------------------------------------------
# THE FOUR NUMBERS
#
# Checked against hand-computed values rather than against themselves, because
# every one of them is a ratio or a difference that looks plausible when it is
# wrong. The سرانه pair in particular: dividing by the wrong count gives a
# number in the right order of magnitude.
# ---------------------------------------------------------------------------
BUY, SELL, NBUY, NSELL = 2_000_000_000.0, 1_000_000_000.0, 100.0, 200.0
pc_buy, pc_sell = BUY / NBUY, SELL / NSELL
check(pc_buy == 20_000_000.0, "سرانهٔ خرید = ارزش خرید ÷ تعداد خریداران", f"{pc_buy:,.0f}")
check(pc_sell == 5_000_000.0, "سرانهٔ فروش = ارزش فروش ÷ تعداد فروشندگان", f"{pc_sell:,.0f}")
check(pc_buy / pc_sell == 4.0, "قدرت خریدار = سرانهٔ خرید ÷ سرانهٔ فروش", "4.0")
check(BUY - SELL == 1_000_000_000.0, "خالص ورود پول = خرید − فروش (نه حجم)")

# A session with no retail buyer has an UNDEFINED per-capita, not a zero. Zero
# would sort every dead symbol to the top of «ضعیف‌ترین قدرت خریدار» and bury
# the ones a user is looking for.
rows = md.flow_history("stock", "__nonexistent__", 5)
check(rows == [], "flow_history on an unknown symbol returns empty, not an error")

# money_shorthand: the unit is chosen per value, and a rial amount is shown in
# تومان. 10^13 rial = 10^12 تومان = 1 همت.
check(md.rial_short(1e13) == "۱ همت", "۱۰¹³ ریال = ۱ همت", md.rial_short(1e13))
check(md.rial_short(1e10) == "۱ میلیارد تومان", "۱۰¹⁰ ریال = ۱ میلیارد تومان",
      md.rial_short(1e10))
check(md.rial_short(None) == "", "rial_short(None) is empty, not «۰»")
check(md.rial_short(0) == "۰", "rial_short(0) is «۰» — zero is a real answer")
check(md.rial_short(-1e13).startswith("−"), "a negative flow keeps its sign",
      md.rial_short(-1e13))
check(md.count_short(1.5e9) == "۱.۵ میلیارد", "counts get count words, never تومان",
      md.count_short(1.5e9))

# Every read helper must survive an empty table: these pages ship before the
# first fetch has ever run, and a TypeError on an empty database is the one
# failure a new user is guaranteed to hit.
for label, fn in (("index_rows", lambda: md.index_rows()),
                  ("index_rows(sectors)", lambda: md.index_rows(sectors=True)),
                  ("usd_summary", md.usd_summary),
                  ("board", md.board),
                  ("board_totals", md.board_totals),
                  ("board_sectors", md.board_sectors),
                  ("money_flow", lambda: md.money_flow(days=5, limit=5)),
                  ("flow_totals", lambda: md.flow_totals(days=1)),
                  ("flow_by_sector", lambda: md.flow_by_sector(days=5)),
                  ("freshness", md.freshness),
                  ("order_book", lambda: md.order_book("__none__")),
                  ("fundamentals", lambda: md.fundamentals("__none__")),
                  ("queue_history", lambda: md.queue_history("stock", "__none__")),
                  ("shareholders", lambda: md.shareholders("__none__")),
                  ("intraday_coverage", lambda: md.intraday_coverage("__none__")),
                  ("trades", lambda: md.trades("__none__", "1405-01-01"))):
    try:
        fn()
        check(True, f"{label}() runs against the live database")
    except Exception as exc:                                 # noqa: BLE001
        check(False, f"{label}() runs against the live database",
              f"{type(exc).__name__}: {exc}")

# Sorts reach SQL through a fixed map, never string interpolation — `order`
# arrives from a query string.
check("--" not in "".join(md._FLOW_ORDER.values()) and
      "--" not in "".join(md._BOARD_ORDER.values()),
      "no sort clause carries a SQL comment")
check(all(k in md._FLOW_ORDER for k, _ in md.FLOW_SORTS),
      "every offered flow sort has an SQL clause")
check(all(k in md._BOARD_ORDER for k, _ in md.BOARD_SORTS),
      "every offered board sort has an SQL clause")
check(md.money_flow(order="'; DROP TABLE ri_history; --")["rows"] is not None,
      "an unknown sort key falls back instead of reaching SQL")

print()
print("=" * 74)
print("PART B — the fetch layer, and the two finpy facts it depends on")
print("=" * 74)

import tse_fetch                                   # noqa: E402
import market                                      # noqa: E402

for kind in ("stock", "etf", "stock_ri", "etf_ri", "index", "usd", "watch",
             "symbols", "stock_queue", "etf_queue", "stock_ob", "stock_trades",
             "shareholders"):
    check(kind in tse_fetch.KINDS, f"kind {kind!r} is registered")
    ds = tse_fetch.KINDS[kind]["dataset"]
    check(ds in tse_fetch._HANDLERS, f"kind {kind!r} dispatches to a handler", ds)

check(tse_fetch.is_price_kind("stock") and tse_fetch.is_price_kind("etf"),
      "the price kinds are recognised as price kinds")
check(not any(tse_fetch.is_price_kind(k)
              for k in ("stock_ri", "index", "usd", "watch", "symbols",
                        "stock_queue", "stock_ob", "stock_trades", "shareholders")),
      "the new kinds are NOT price kinds — they must skip the analytics rebuild")

# THE HEAVY GUARD. These three are one request PER SYMBOL-DAY: «ریز معاملات» for
# the whole market over a month is ~24,000 requests. The form must not let that
# start by leaving a box empty.
check(set(market.HEAVY_KINDS) ==
      {"stock_queue", "etf_queue", "stock_ob", "stock_trades"},
      "the per-symbol-day datasets are marked heavy",
      "، ".join(market.HEAVY_KINDS))
check(all(tse_fetch.KINDS[k].get("heavy") for k in market.HEAVY_KINDS),
      "…and the fetch layer agrees with the form about which they are")

work = tse_fetch.reference_tickers("index")
check(len(work) == len(md.MARKET_INDICES) + len(md.SECTOR_NAMES),
      "the index work list is 10 market + 40 sector indices", str(len(work)))
check(all(isinstance(t, str) and t for _e, t in work),
      "every index work item names itself")
check(len(tse_fetch.reference_tickers("watch")) == 1,
      "the snapshot job is one unit of work")

# ---- FACT 1: Get_RI_History needs alt=True --------------------------------
src = read("tse_fetch.py")
check("alt=True" in src, "the RI fetch passes alt=True")

try:
    import inspect
    import finpy_tse as fpy
    sig = inspect.signature(fpy.Get_RI_History)
    check("alt" in sig.parameters, "the installed Get_RI_History still takes `alt`")
    # If finpy ever fixes the primary endpoint and flips this default, alt=True
    # is still correct — but the note in tse_fetch.py explaining WHY would have
    # gone stale, so it is worth knowing.
    check(sig.parameters["alt"].default is False,
          "…and still defaults to the broken path (alt=False)",
          str(sig.parameters["alt"].default))
except ImportError:
    check(False, "finpy_tse is importable", "not installed")

# ---- FACT 2: the sector names must be finpy's own -------------------------
try:
    import finpy_tse as fpy
    import inspect
    resolver = None
    for name, obj in vars(fpy).items():
        if "Sector_WebID" in name and callable(obj):
            resolver = obj
            break
    check(resolver is not None, "finpy's sector lookup is reachable for inspection")
    if resolver is not None:
        body = inspect.getsource(resolver)
        missing = [s for s in md.SECTOR_NAMES if f"'{s}'" not in body]
        # A name we ship that finpy does not know falls through to its Google
        # scrape — slow, fragile, and silent.
        check(not missing,
              "every SECTOR_INDICES name is in finpy's own lookup table",
              ("missing: " + "، ".join(missing[:4])) if missing else
              f"{len(md.SECTOR_NAMES)} names")
except Exception as exc:                                     # noqa: BLE001
    check(False, "finpy's sector list could be compared", f"{type(exc).__name__}: {exc}")

check(len(set(md.SECTOR_NAMES)) == len(md.SECTOR_NAMES),
      "no duplicate sector names")
mapped = [g for _n, g in md.SECTOR_INDICES if g]
check(len(set(mapped)) == len(mapped),
      "no two sector indices claim the same گروه صنعت")
known_groups = set(db.stock_sectors())
if known_groups:
    stray = [g for g in mapped if g not in known_groups]
    check(not stray, "every mapped گروه صنعت exists in `stocks`",
          "، ".join(stray[:3]) if stray else f"{len(mapped)} mapped")

# The column lists the handlers read must match the frames finpy returns. A
# mismatch is caught at fetch time by _require(), but only once someone runs it.
check(len(tse_fetch._RI_COLUMNS) == len(tse_fetch._RI_SOURCE) + 3,
      "RI columns = source columns + (kind, entity_id, ticker)")
check(len(tse_fetch._INDEX_COLUMNS) == len(tse_fetch._INDEX_SOURCE) + 2,
      "index columns = source columns + (index_key, name)")
check(len(tse_fetch._WATCH_COLUMNS) == len(tse_fetch._WATCH_SOURCE) + 4,
      "watch columns = source columns + (ticker, j_date, date, captured_at)")
check(len(tse_fetch._USD_COLUMNS) == len(tse_fetch._USD_SOURCE),
      "dollar columns match one-for-one")
check(len(tse_fetch._LIST_COLUMNS) == len(tse_fetch._LIST_SOURCE),
      "stock-list columns match one-for-one")
# The installed package spells these with a hyphen; the printed guide uses an
# underscore. Reading the guide's spelling returns nothing at all.
check("BQ-Value" in tse_fetch._WATCH_SOURCE and "SQ-Value" in tse_fetch._WATCH_SOURCE,
      "the queue columns use the installed package's hyphen spelling")

check(set(market.RUNNABLE_KINDS) == set(tse_fetch.KINDS),
      "every fetchable kind is offered on the update form")
check(all(d["group"] in market.DATASET_GROUPS for d in market.DATASET_CHOICES),
      "every dataset belongs to a declared group")
check(all(d["note"].strip() for d in market.DATASET_CHOICES),
      "every dataset explains itself on the form")

print()
print("=" * 74)
print("PART C — the routes, the pages and the navigation")
print("=" * 74)

import app as A                                    # noqa: E402

row = db._one("SELECT id FROM users ORDER BY id LIMIT 1")
if not row:
    check(False, "a user exists to sign the test client in as")
else:
    uid = row["id"]
    client = A.app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(uid)
        sess["_fresh"] = True

    for path, marker, label in (
            ("/indices", "شاخص‌ها", "/indices"),
            ("/moneyflow", "پول حقیقی و حقوقی", "/moneyflow"),
            ("/live", "تابلوی زنده", "/live"),
            ("/update", "پوشش داده", "/update")):
        page = client.get(path)
        html = page.get_data(as_text=True)
        check(page.status_code == 200, f"{label} renders", str(page.status_code))
        check(marker in html, f"…and is the right page")

    # The two JSON endpoints.
    r = client.get("/api/indices/series/cwi")
    check(r.status_code == 200 and "points" in r.get_json(),
          "/api/indices/series/<key> answers", str(r.status_code))
    r = client.get("/api/orderbook/" + "فولاد")
    check(r.status_code == 200 and "rows" in r.get_json(),
          "/api/orderbook/<ticker> answers", str(r.status_code))

    # Filters must round-trip rather than 500 on an unexpected value.
    for qs in ("?order=bogus", "?queue=bogus", "?sector=nope", "?q=%27%22",
               "?order=pe_asc&queue=buy"):
        check(client.get("/live" + qs).status_code == 200, f"/live{qs} is handled")
    for qs in ("?days=999", "?order=bogus", "?kind=nope", "?days=20&order=power_desc"):
        check(client.get("/moneyflow" + qs).status_code == 200,
              f"/moneyflow{qs} is handled")
    check(client.get("/indices?focus=nonsense").status_code == 200,
          "/indices with an unknown focus is handled")

    # A signed-out visitor is sent to the login page, like every other page.
    anon = A.app.test_client()
    for path in ("/indices", "/moneyflow", "/live"):
        check(anon.get(path).status_code == 302, f"{path} is behind the login")

base = read("templates/base.html")
for endpoint in ("indices_page", "moneyflow_page", "live_page"):
    check(f"url_for('{endpoint}')" in base, f"the nav links to {endpoint}")
check('data-role="nav-menu"' in base, "the nav is grouped into menus")
check(base.count('data-role="nav-trigger"') == 2, "…two of them",
      str(base.count('data-role="nav-trigger"')))

ui = read("static/js/ui.js")
check("navMenus()" in ui, "the nav menus are wired up in ui.js")
check("dropdowns(" in ui, "…through the same dropdown helper as the user menu")

css = read("static/css/ui.css")
check("overflow:visible" in css.split(".topnav{")[1].split("}")[0],
      "the nav bar no longer clips its own dropdowns")
check(".nav-pop{" in css and "position:fixed" in css,
      "…and the panels escape the scroller on narrow screens")

app_js = read("static/js/app.js")
check("dataset.nosort" in app_js,
      "initTable keeps attached rows out of the sort")
check("reattach()" in app_js,
      "…and re-parks them under their own symbol afterwards")

live = read("templates/live.html")
check("data-nosort" in live and 'data-for="{{ r.ticker }}"' in live,
      "the order-book drawers use that primitive")

upd = read("templates/update.html")
check("optgroup" in upd, "the update form groups its data types")
check("ds-note" in upd, "…and explains the selected one")
check("dataset_groups" in upd,
      "…driven by market.DATASET_GROUPS, not a hard-coded list")
check("lbl-heavy" in upd, "…and asks before a per-symbol-day run over the market")

# EVERY dataset that can be WRITTEN from this page must be DELETABLE from it.
# Before this, a wrong-year index sweep could be created from the UI and undone
# only with psql.
writable = {tse_fetch.KINDS[k]["dataset"] for k in market.RUNNABLE_KINDS}
writable.discard("price")                      # db.delete_price_history owns those
writable.discard("symbols")                    # the reference list is never "deleted"
missing = sorted(writable - set(md.DELETABLE))
check(not missing, "every non-price dataset can be deleted from the page",
      "، ".join(missing) if missing else f"{len(md.DELETABLE)} datasets")
for ds, (table, _tc, _dc, _label) in md.DELETABLE.items():
    check(db._one("SELECT to_regclass(%s) t", (f"public.{table}",))["t"] is not None,
          f"…«{ds}» points at a real table", table)
# The safety property the price delete has, kept here: no dates and no explicit
# all_history must RAISE, never be read as "no bounds, so everything".
try:
    md.delete_dataset("ri")
    check(False, "a rangeless dataset delete raises rather than wiping the table")
except ValueError:
    check(True, "a rangeless dataset delete raises rather than wiping the table")
except Exception as exc:                                     # noqa: BLE001
    check(False, "a rangeless dataset delete raises rather than wiping the table",
          f"{type(exc).__name__}: {exc}")
try:
    md.delete_dataset("nonsense", all_history=True)
    check(False, "an unknown dataset is refused")
except ValueError:
    check(True, "an unknown dataset is refused")

tasks_src = read("tasks.py")
check("is_price_kind" in tasks_src,
      "finalize_update skips the analytics rebuild for non-price runs")

print()
if LIVE:
    print("=" * 74)
    print("PART D — one real fetch of each dataset (--live)")
    print("=" * 74)
    import time
    # Derived from the price table, never hard-coded. A literal window goes
    # stale silently: asking TSETMC for last year's dates returns real rows for
    # last year, so every check below still passes while the rows land a year
    # away from every price bar and join to nothing.
    hi = db.latest_date("stock") or "1405-01-01"
    y, m, d = (int(x) for x in hi.split("-"))
    lo = f"{y:04d}-{max(1, m - 1):02d}-01"
    print(f"  (window {lo} … {hi}, taken from the price table)")
    cases = [
        ("stock_ri", 1, "فولاد", "حقیقی/حقوقی یک نماد"),
        ("index", 0, "cwi", "شاخص کل"),
        ("index", 0, md.sector_key("فلزات اساسی"), "شاخص یک گروه صنعت"),
        ("usd", 0, "دلار آزاد", "قیمت دلار"),
        ("watch", 0, "دیده‌بان بازار", "دیده‌بان و عمق بازار"),
        ("shareholders", 1, "فولاد", "سهامداران عمده"),
    ]
    for kind, eid, item, label in cases:
        t0 = time.time()
        try:
            n = tse_fetch.fetch_and_store(kind, eid, item, lo, hi)
            check(n > 0, label, f"{n} ردیف در {time.time() - t0:.1f} ثانیه")
        except Exception as exc:                             # noqa: BLE001
            check(False, label, f"{type(exc).__name__}: {str(exc)[:90]}")

    # THE ROWS MUST LAND ON THE PRICE CALENDAR.
    #
    # The designer joins ri_history to the price table on (ticker, date), so a
    # fetch that succeeds but stores dates the price table does not have is
    # worse than a failure: every حقیقی/حقوقی block reads None and every filter
    # using one matches nothing, silently. This is the check that would have
    # caught a stale date window.
    hit = db._one(
        """SELECT COUNT(*) n FROM ri_history r
             JOIN stockpricehistory p
               ON p.ticker = r.ticker AND p.date = r.date
            WHERE r.ticker = %s""", ("فولاد",))
    check((hit or {}).get("n", 0) > 0,
          "the fetched حقیقی/حقوقی rows join to real price bars",
          f"{(hit or {}).get('n', 0)} matched sessions")
    idx = db._one("SELECT MAX(j_date) d FROM index_history WHERE index_key = 'cwi'")
    check((idx or {}).get("d") == hi,
          "the index sweep reaches the price table's latest session",
          f"index {(idx or {}).get('d')} vs prices {hi}")

    # ---- the intraday family, on ONE symbol and ONE session -----------------
    #
    # These are the three finpy reports as «data is not available» because its
    # positional column rename raises on TSETMC's added `title` column. Fetching
    # them at all is the check; the tick tape then gets a much stronger one.
    row = db._one("SELECT stockid FROM stocks WHERE ticker = %s", ("فولاد",))
    sid = (row or {}).get("stockid") or 1
    session = db._one(
        """SELECT j_date FROM stockpricehistory WHERE ticker = %s
            ORDER BY date DESC OFFSET 3 LIMIT 1""", ("فولاد",))
    jd = (session or {}).get("j_date")
    if not jd:
        check(False, "a session exists to fetch an intraday tape for")
    else:
        for kind, label in (("stock_queue", "سابقهٔ صف"),
                            ("stock_ob", "عمق بازار درون‌روز"),
                            ("stock_trades", "ریز معاملات")):
            t0 = time.time()
            try:
                n = tse_fetch.fetch_and_store(kind, sid, "فولاد", jd, jd)
                check(n > 0, label, f"{n} ردیف در {time.time() - t0:.1f} ثانیه")
            except tse_fetch.NoDataError:
                # TSETMC does not keep the tape for every session. Reported, not
                # failed: it is a real answer about that day, not a broken fetch.
                check(True, label, f"TSETMC has no tape for {jd} — a real answer")
            except Exception as exc:                         # noqa: BLE001
                check(False, label, f"{type(exc).__name__}: {str(exc)[:90]}")

        # THE STRONGEST CHECK IN THIS FILE.
        #
        # The tick tape and the daily bar come from two different TSETMC
        # endpoints and are parsed by completely separate code, so if the tape's
        # min price, max price and summed volume all equal the daily bar's low,
        # high and volume, the parsing is right — column mapping, the cancelled
        # flag, the time filter and all. Nothing else here can prove that.
        agree = db._one(
            """SELECT p.low, p.high, p.volume,
                      t.lo, t.hi, t.vol
                 FROM stockpricehistory p
                 JOIN (SELECT j_date, MIN(price) lo, MAX(price) hi,
                              SUM(volume) vol
                         FROM intraday_trades
                        WHERE ticker = %s AND NOT canceled
                        GROUP BY j_date) t ON t.j_date = p.j_date
                WHERE p.ticker = %s LIMIT 1""", ("فولاد", "فولاد"))
        if agree:
            check(float(agree["lo"]) == float(agree["low"]) and
                  float(agree["hi"]) == float(agree["high"]) and
                  int(agree["vol"]) == int(agree["volume"]),
                  "the tick tape reproduces the daily bar exactly (low/high/volume)",
                  f"{agree['lo']}–{agree['hi']} vol {agree['vol']:,}")
        else:
            check(True, "no tick tape stored to cross-check against the daily bar")

        # The queue's own sanity: the permitted band cannot be inverted. finpy's
        # Get_Queue_History assigns psGelStaMax to Day_LL and the minimum to
        # Day_UL — they are swapped in its output, and ours must not be.
        bad = db._one("SELECT COUNT(*) n FROM queue_history WHERE day_ul <= day_ll")
        check((bad or {}).get("n", 0) == 0,
              "every stored session has day_ul > day_ll (finpy swaps these)",
              f"{(bad or {}).get('n', 0)} inverted rows")

    # A bad index key must fail as ITSELF, not as a retryable outage: finpy
    # signals an unresolvable name by printing and returning None.
    try:
        tse_fetch.fetch_and_store("index", 0, md.sector_key("بی‌معنی"), lo, hi)
        check(False, "an unknown index name is rejected")
    except tse_fetch.TransientFetchError as exc:
        check(False, "an unknown index name is rejected, not retried forever",
              f"got TransientFetchError: {str(exc)[:60]}")
    except tse_fetch.FetchError:
        check(True, "an unknown index name is rejected as a permanent failure")
    print()
else:
    print("(PART D skipped — pass --live to fetch from TSETMC for real)")
    print()

print("=" * 74)
print("PART E — in a real browser")
print("=" * 74)

# Two behaviours here exist ONLY in a browser, and both were broken when first
# written — which is the argument for this part rather than more Python.
#
#   * The grouped nav menus have to actually open. The bar they live in was an
#     `overflow-x:auto` scroller, and a scroll container clips an absolutely
#     positioned child on BOTH axes, so the panel would have been un-hidden and
#     invisible. Its measured height is what says otherwise.
#   * The order-book drawers have to survive a column sort still attached to
#     their own symbol. `data-nosort` is a valueless attribute, which reads back
#     as the empty string — falsy — so the first version excluded nothing, the
#     comparator reached for a column the drawer's single colspan cell does not
#     have, and the TypeError aborted the sort. The table then LOOKED correct,
#     because a sort that throws before it moves anything leaves the rows where
#     they were. That is why `sortActuallyReordered` is asserted too: without
#     it, "every drawer is beside its row" passes for a sort that never ran.
if "--no-browser" in sys.argv:
    print("  SKIP  --no-browser")
elif not os.path.exists(os.path.join(ROOT, "frontend", "marketdata_check.mjs")):
    print("  SKIP  frontend/marketdata_check.mjs is missing")
else:
    import json
    import subprocess
    import threading
    try:
        import werkzeug.serving
        srv = werkzeug.serving.make_server("127.0.0.1", 5099, A.app, threaded=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        with A.app.test_request_context():
            cookie = A.app.session_interface.get_signing_serializer(A.app).dumps(
                {"_user_id": str(uid), "_fresh": True})
        try:
            proc = subprocess.run(
                ["node", "marketdata_check.mjs", "http://127.0.0.1:5099", cookie],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=os.path.join(ROOT, "frontend"), timeout=600,
                shell=(os.name == "nt"))
            out = (json.loads(proc.stdout.strip().splitlines()[-1])
                   if proc.stdout.strip() else {})
        finally:
            srv.shutdown()

        if not out:
            check(False, "the browser check ran",
                  (proc.stderr or "no output")[:160])
        else:
            check(not out["errors"], "no JavaScript errors on any of the pages",
                  "; ".join(out["errors"])[:200])
            check(out["navTriggers"] == 2, "the nav carries both grouped menus",
                  str(out["navTriggers"]))
            check(out["navOpens"] and out["navPopHeight"] > 100,
                  "…a menu opens to its full height, unclipped by the nav bar",
                  f"{out['navPopHeight']}px")
            check(out["navCloses"], "…and Escape closes it")
            check(out["indexChartSvg"] > 0, "«شاخص‌ها» draws its chart")
            check(out.get("chartTitleChanged") and out.get("chartStillDrawn"),
                  "…and picking another index redraws it")
            check(out.get("focusInUrl"), "…leaving the chosen index in the URL")
            check(out["flowSortable"] >= 10, "«پول حقیقی و حقوقی» is sortable",
                  f"{out['flowSortable']} columns")
            if out["liveRows"] > 2:
                check(out["drawerOpens"] and out["drawerHasBook"],
                      "«تابلوی زنده» opens an order-book drawer on click")
                check(out["sortActuallyReordered"] and out["sortedAscending"],
                      "…a column sort actually runs and orders the rows")
                check(out["drawersStillPaired"],
                      "…and every drawer is still beside its own symbol")
                check(out["openDrawerFollowedItsRow"],
                      "…including the open one")
            check(out["datasetOptions"] == len(market.DATASET_CHOICES) and
                  out["datasetGroups"] == len(market.DATASET_GROUPS),
                  "«به‌روزرسانی» offers every data type, grouped",
                  f"{out['datasetOptions']} in {out['datasetGroups']} groups")
            check(out["coverageCells"] >= 8, "…and shows a coverage cell per dataset",
                  str(out["coverageCells"]))
            check(out["snapshotHidesDates"] and out["noteChangedForSnapshot"],
                  "…a snapshot type hides the date range and the symbol box")
            check(out["datedShowsDates"], "…and a dated type brings them back")
    except Exception as exc:                                 # noqa: BLE001
        check(False, "the browser check ran", f"{type(exc).__name__}: {exc}")

print()
print("=" * 74)
print(f"{PASS} passed, {FAIL} failed")
print("=" * 74)
sys.exit(1 if FAIL else 0)
