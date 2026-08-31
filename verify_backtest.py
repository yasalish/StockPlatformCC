"""
verify_backtest.py — «بک‌تست فیلتر»: replaying a designed filter over history.

    python verify_backtest.py

Three parts, in the order a failure is cheapest to diagnose:

  A  the two properties that make a backtest worth believing — no look-ahead in
     the framed blocks, and a filter with no edge scoring no edge;
  B  the engine's own arithmetic — entry and exit pricing, unfillable fills,
     the calendar, and the guards against bad source data;
  C  the route, the page and the built bundle.

PART A IS THE POINT OF THIS FILE.

A screener backtest is a number that looks like a fact, and the two ways it
lies are both silent. It does not crash, it does not warn, it returns 61 % and
the user believes it.

The control test is the load-bearing one. It backtests a filter that matches
EVERY bar of EVERY symbol — a strategy with no selection at all, whose excess
return over "buy the whole market" is zero by construction. Any bias anywhere
in the pipeline shows up there as a non-zero «مازاد», and during development it
caught exactly that twice: once when the benchmark was a daily-rebalanced index
(Tehran's limit bands make rebalancing profitable on its own, worth −0.16 %),
and once when a session with no benchmark samples scored as a flat 0.0 % rather
than being excluded (worth +0.64 %). Neither was visible in any other test; both
would have flattered every filter a user ever wrote.
"""
import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))
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


def N(i, t, **p):
    return {"id": i, "type": t, "x": 0, "y": 0, "params": p}


def E(a, b, port="in", fp="out"):
    return {"from": a, "fromPort": fp, "to": b, "toPort": port}


print("=" * 74)
print("PART A — the two properties that make the number believable")
print("=" * 74)

import db                                          # noqa: E402
import filter_engine as fe                         # noqa: E402
import backtest as bt                              # noqa: E402

as_of = db.latest_date("stock")
check(as_of is not None, "the stock price table has a latest session", as_of)

# ---------------------------------------------------------------------------
# 1. NO LOOK-AHEAD IN A FRAMED BLOCK
#
# A weekly value is broadcast across its whole week, so a Saturday reads a
# candle that does not close until Wednesday. That is correct for the live
# screener (the newest bar's frame is partial and current) and is a time
# machine everywhere else. The test asks the only question that settles it: is
# the high this block reports at bar i a price that has actually printed by
# bar i?
# ---------------------------------------------------------------------------
TICKER = "فولاد"
TAIL = 220


def _high_graph(tf):
    return {"nodes": [N("x", "price", tf=tf, field="high", shift=0),
                      N("k", "const", value=-1e18),
                      N("c", "compare", op=">", tol=0),
                      N("o", "output", within=1, sort="price")],
            "edges": [E("x", "c", "a"), E("k", "c", "b"), E("c", "o", "in")]}


daily = fe.explain(_high_graph("D"), "stock", TICKER, tail=TAIL)["ports"]["x:out"]["tail"]
for tf, label in (("W", "هفتگی"), ("M", "ماهانه")):
    seen = set()
    leaks_live = leaks_causal = 0
    live = fe.explain(_high_graph(tf), "stock", TICKER, tail=TAIL,
                      causal=False)["ports"]["x:out"]["tail"]
    caus = fe.explain(_high_graph(tf), "stock", TICKER, tail=TAIL,
                      causal=True)["ports"]["x:out"]["tail"]
    for i, d in enumerate(daily):
        seen.add(d)
        if live[i] is not None and live[i] not in seen:
            leaks_live += 1
        if caus[i] is not None and caus[i] not in seen:
            leaks_causal += 1
    # The live mode MUST still leak — that is what "this week so far" means, and
    # a zero here would mean the screener had silently started answering a
    # different question than the one its users ask.
    check(leaks_live > 0,
          f"the {label} frame holds its finished candle across the week (live)",
          f"{leaks_live}/{TAIL} bars")
    check(leaks_causal == 0,
          f"…and reports NOTHING that has not printed yet under causal mode",
          f"{leaks_causal}/{TAIL} bars")

# The last bar is the last bar of its own frame either way, so the live screener
# has to be bit-identical with the flag on. This is what lets the backtest turn
# it on without a second code path through the engine.
for tf in ("W", "M"):
    a = fe.explain(_high_graph(tf), "stock", TICKER, tail=3, causal=False)
    b = fe.explain(_high_graph(tf), "stock", TICKER, tail=3, causal=True)
    check(a["ports"]["x:out"]["tail"][-1] == b["ports"]["x:out"]["tail"][-1],
          f"causal mode leaves the NEWEST {tf} bar untouched",
          str(a["ports"]["x:out"]["tail"][-1]))

# ---------------------------------------------------------------------------
# 2. THE CONTROL: A FILTER WITH NO EDGE MUST SCORE NO EDGE
# ---------------------------------------------------------------------------
CONTROL = {
    "nodes": [N("p", "price", tf="D", field="close", shift=0),
              N("k", "const", value=0), N("c", "compare", op=">", tol=0),
              N("o", "output", within=1, sort="price")],
    "edges": [E("p", "c", "a"), E("k", "c", "b"), E("c", "o", "in")],
}
ctl = bt.backtest(CONTROL, kind="stock", sessions=250, repeat=True,
                  cost=0.0, require_fill=False)
check(ctl["signals"] > 50_000, "the control filter fires on every bar of the market",
      f"{ctl['signals']:,} signals over {ctl['scanned']} symbols")
check(abs(ctl["exposure"] - 100.0) < 0.5, "…and is invested on every session",
      f"{ctl['exposure']:.1f}%")

worst = 0.0
for h in ctl["horizons"]:
    e = ctl["stats"][str(h)]["excess"]
    worst = max(worst, abs(e))
    check(abs(e) < 0.05, f"…and its «مازاد» at +{h} is zero", f"{e:+.4f}%")
check(worst < 0.05, "NO BIAS anywhere in the pipeline", f"worst |excess| {worst:.4f}%")

# Every trade must be scored against a real market average. A None benchmark is
# excluded rather than read as zero; if many are excluded the excess above is
# computed on a subset and is no longer the whole story.
for h in ctl["horizons"]:
    s = ctl["stats"][str(h)]
    check(s["benched"] >= s["n"] * 0.99,
          f"…and virtually every +{h} trade has a real benchmark",
          f"{s['benched']:,}/{s['n']:,}")

print()
print("=" * 74)
print("PART B — the arithmetic")
print("=" * 74)

EX = {e["key"]: e for e in fe.EXAMPLES}

# ---- entry is the NEXT bar's open, never the signal bar's close ------------
# Proved on the data rather than asserted: re-price the reported trades from the
# panel and check the entry equals the open of the session AFTER the signal.
r = bt.backtest(EX["golden"]["graph"], kind="stock", sessions=250)
check(r["signals"] > 0, "the golden-cross example produces trades", str(r["signals"]))
axis = _cal = bt._calendar("stock", as_of, r["bars"])
pos = {d: i for i, d in enumerate(axis)}
store = fe._load_columns("stock", as_of, r["bars"], ("o", "f"),
                         tickers=[t["ticker"] for t in r["trades"][:40]], dates=True)
matched = mismatched = 0
for t in r["trades"][:40]:
    jd = store[fe.DATE_COL].get(t["ticker"])
    if not jd or t["date"] not in jd:
        continue
    k = jd.index(t["date"])
    o = store["o"][t["ticker"]][k]
    if abs(o - t["entry"]) < 0.51 or abs((o or store["f"][t["ticker"]][k]) - t["entry"]) < 0.51:
        matched += 1
    else:
        mismatched += 1
check(matched and not mismatched,
      "every reported entry is the OPEN of the session it names",
      f"{matched} matched, {mismatched} not")

# ---- unfillable entries -----------------------------------------------------
# «صف خرید» is not a rounding error on this exchange. The queue example exists
# to find symbols locked at the ceiling, so almost none of its signals are
# tradeable — and a backtest that bought them would report a fantasy.
q_on = bt.backtest(EX["queue"]["graph"], kind="stock", sessions=250, require_fill=True)
q_off = bt.backtest(EX["queue"]["graph"], kind="stock", sessions=250, require_fill=False)
check(q_on["skipped"]["lock"] > 100,
      "«صف خرید» entries are dropped as unfillable",
      f"{q_on['skipped']['lock']:,} dropped")
check(q_on["signals"] < q_off["signals"],
      "…so the fill check materially shrinks that filter's trade count",
      f"{q_on['signals']} vs {q_off['signals']} unchecked")
check(q_on["stats"]["22"]["excess"] < q_off["stats"]["22"]["excess"],
      "…and the unchecked version reports the better, fictional number",
      f"{q_on['stats']['22']['excess']:+.2f}% vs {q_off['stats']['22']['excess']:+.2f}%")

# ---- the commission actually bites -----------------------------------------
free = bt.backtest(EX["rsibounce"]["graph"], kind="stock", sessions=250, cost=0.0)
paid = bt.backtest(EX["rsibounce"]["graph"], kind="stock", sessions=250, cost=2.0)
drop = free["stats"]["22"]["avg"] - paid["stats"]["22"]["avg"]
check(abs(drop - 2.0) < 0.01, "کارمزد is charged once, on the way in",
      f"{drop:.3f}% for a 2% cost")
check(abs(free["stats"]["22"]["excess"] - paid["stats"]["22"]["excess"]) < 0.01,
      "…and is charged to the benchmark too, so «مازاد» is unmoved by it",
      f"{free['stats']['22']['excess']:+.3f}% vs {paid['stats']['22']['excess']:+.3f}%")

# ---- one signal per run, unless asked otherwise ----------------------------
once = bt.backtest(EX["trend"]["graph"], kind="stock", sessions=250, repeat=False)
every = bt.backtest(EX["trend"]["graph"], kind="stock", sessions=250, repeat=True)
check(once["signals"] < every["signals"],
      "a run of true bars is ONE signal by default",
      f"{once['signals']:,} vs {every['signals']:,} with repeat")

# ---- the horizon cut-off ---------------------------------------------------
# The last max_h sessions cannot produce signals: their future has not happened.
check(r["to"] < r["as_of"],
      "the window stops short of today by the longest horizon",
      f"{r['to']} vs as_of {r['as_of']}")
check(len(r["curve"]) == len(r["dates"]) == len(r["bench_curve"]),
      "the curves and the date axis are the same length",
      f"{len(r['curve'])}")

# ---- bad source data --------------------------------------------------------
# A few symbols carry a discontinuous adjusted series («رفاه» is stored at 0.0
# and 1.0 in 1396 and in the 185,000s later), which reports a 185,305× return.
# One of those in a cross-sectional mean moved a day's market to +505 %.
deep = bt.backtest(EX["golden"]["graph"], kind="stock", sessions=1000)
for h in deep["horizons"]:
    b = deep["stats"][str(h)]["bench"]
    check(-30.0 < b < 60.0, f"the +{h} market benchmark is a plausible number",
          f"{b:+.2f}%")

# ---- both kinds and a scoped group -----------------------------------------
etf = bt.backtest(EX["rsibounce"]["graph"], kind="etf", sessions=250)
check(etf["signals"] > 0 and etf["errors"] == 0, "صندوق‌ها backtest too",
      f"{etf['signals']} signals, {etf['scanned']} funds")
grp = bt.backtest(EX["rsibounce"]["graph"], kind="stock", sessions=250,
                  group="فلزات اساسی")
check(0 < grp["scanned"] < r["scanned"], "a group scope narrows the universe",
      f"{grp['scanned']} of {r['scanned']}")

# ---- every example runs without a single symbol raising --------------------
broken = []
for e in fe.EXAMPLES:
    try:
        out = bt.backtest(e["graph"], kind="stock", sessions=250)
        if out["errors"]:
            broken.append(f"{e['key']}: {out['errors']} symbols raised")
    except Exception as exc:                                 # noqa: BLE001
        broken.append(f"{e['key']}: {type(exc).__name__}: {exc}")
check(not broken, "every ready-made example backtests cleanly",
      "; ".join(broken)[:200] or f"{len(fe.EXAMPLES)} examples")

# ---- a graph that cannot leave room for a window is refused -----------------
try:
    bt.backtest({"nodes": [N("x", "sma", tf="M", n=500, src="final", method="sma"),
                           N("k", "const", value=-1e18),
                           N("c", "compare", op=">", tol=0),
                           N("o", "output", within=1, sort="price")],
                 "edges": [E("x", "c", "a"), E("k", "c", "b"), E("c", "o", "in")]},
                kind="stock", sessions=1000)
    check(False, "a graph too deep to leave a window is refused")
except bt.BacktestError as exc:
    check(True, "a graph too deep to leave a window is refused", str(exc)[:60])
except Exception as exc:                                     # noqa: BLE001
    check(False, "a graph too deep to leave a window is refused",
          f"{type(exc).__name__}: {exc}")

print()
print("=" * 74)
print("PART C — the route, the page and the bundle")
print("=" * 74)

import app as A                                    # noqa: E402

uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")["id"]
client = A.app.test_client()
with client.session_transaction() as sess:
    sess["_user_id"] = str(uid)
    sess["_fresh"] = True

page = client.get("/filter-backtest")
html = page.get_data(as_text=True)
check(page.status_code == 200, "/filter-backtest renders", str(page.status_code))
check('id="designer-backtest"' in html, "…with the island's mount point")
check("dist/designer_backtest.js" in html, "…and the built bundle")
check(os.path.exists(os.path.join(ROOT, "static/dist/designer_backtest.js")),
      "…which exists on disk (run `npm run build` in frontend/)")

res = client.post("/api/designer/backtest",
                  json={"graph": EX["rsibounce"]["graph"], "kind": "stock",
                        "sessions": 250, "cost": 1.2, "hold": 22})
check(res.status_code == 200, "POST /api/designer/backtest", str(res.status_code))
body = res.get_json() or {}
check(body.get("signals", 0) > 0, "…returns signals", str(body.get("signals")))
check(len(body.get("trades") or []) > 0, "…and a trade table",
      str(len(body.get("trades") or [])))

bad = client.post("/api/designer/backtest",
                  json={"graph": {"nodes": [], "edges": []}, "kind": "stock"})
check(bad.status_code == 400, "an empty graph is a 400 with a Persian message",
      (bad.get_json() or {}).get("error", "")[:50])
check(A.app.test_client().post(
    "/api/designer/backtest", json={"graph": CONTROL}).status_code == 401,
    "a signed-out caller gets 401")

# The nav keeps «طراحی فیلتر» lit on all three pages of the workflow.
base = read("templates/base.html")
check("filter_backtest_page" in base, "the nav highlights the designer tab here too")

print()
print("=" * 74)
print(f"{PASS} passed, {FAIL} failed")
print("=" * 74)
sys.exit(1 if FAIL else 0)
