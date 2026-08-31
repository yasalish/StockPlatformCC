"""
verify_designer.py — «طراحی فیلتر»: the node-graph filter designer.

    python verify_designer.py [--no-browser]

Four parts, in the order a failure is cheapest to diagnose:

  A  the engine, against the live price panel — indicators cross-checked
     against db.py's own, the graph interpreter's semantics, and the limits
     that stop a hand-edited payload from costing an hour of CPU;
  B  the wiring — routes, the catalogue, saving, and the errors a user can act
     on;
  C  the built bundle and the rendered page;
  D  a real browser DRIVING the canvas: drag a chip, pull a wire, cut a wire,
     undo, press «اجرا» and FOLLOW THE NAVIGATION to /filter-designer/result,
     read the table there, open «چرا؟», prove the diagram on that page is
     read-only, and come back to the editor with the graph intact.

Part D is the one that matters most and is the reason this file exists. A node
editor's failure mode is not a stack trace — it is a page that renders perfectly
and does nothing when you drag on it, which no server-side test can see. Two of
the bugs the checks below now pin were exactly that: an SVG sized 1×1 with
`overflow: visible`, which painted every wire and let none of them be clicked,
and a grid row with only a `min-height`, which let the palette stretch the board
to twice the window height so the opening auto-fit framed the graph off-screen.
"""
import json
import os
import re
import subprocess
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BROWSER = "--no-browser" not in sys.argv
ROOT = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(ROOT, "frontend")

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
print("PART A — the engine")
print("=" * 74)

import db                                          # noqa: E402
import filter_engine as fe                         # noqa: E402


def _parses(expr):
    """Does the formula language accept this text?"""
    try:
        fe.compile_formula(expr)
        return True
    except fe.GraphError:
        return False



as_of = db.latest_date("stock")
check(as_of is not None, "the stock price table has a latest session", as_of)

# ── indicators: the series forms must agree with db.py's last-value forms ────
# If they drift, the ATR a designed filter compares against is not the ATR the
# security page prints, and nobody ever finds out why the two disagree.
panel = fe._panel("stock", as_of, 300, ("c", "h", "l"))
checked = mismatch = 0
worst = ""
for ticker, bars in list(panel.items())[:200]:
    H, L, C = bars["h"], bars["l"], bars["c"]
    if len(C) < 120:
        continue
    checked += 1
    adx, pdi, ndi = fe._adx_series(H, L, C, 14)
    pairs = (
        ("ATR", fe._atr_series(H, L, C, 14)[-1], db._atr_last(H, L, C, 14)),
        ("ADX", adx[-1], db._adx_last(H, L, C, 14)[0]),
        ("+DI", pdi[-1], db._adx_last(H, L, C, 14)[1]),
        ("−DI", ndi[-1], db._adx_last(H, L, C, 14)[2]),
    )
    for name, mine, theirs in pairs:
        if mine is None or theirs is None:
            if mine is not theirs:
                mismatch += 1
                worst = f"{name}/{ticker}: {mine} vs {theirs}"
        elif abs(mine - theirs) > max(1e-9, abs(theirs) * 1e-9):
            mismatch += 1
            worst = f"{name}/{ticker}: {mine} vs {theirs}"
check(checked > 50, "enough symbols to cross-check indicators", f"{checked}")
check(mismatch == 0, "ATR / ADX / ±DI match db.py's own implementations exactly", worst)

# ── no price in the panel may be zero ──────────────────────────────────────
# 347 rows in this database record adj_open = adj_high = adj_low = 0 next to a
# valid settlement price. The loader used to turn those into a price of zero
# rials, and «شکست کانال دانچیان» duly reported ثاژن as a breakout because its
# twenty-bar highest high came out as 0 and 6,300 > 0. The repair in
# _load_columns turns such a bar into a flat one at its settlement price; this
# is what stops it regressing.
_zero = []
for _col in ("o", "h", "l", "c", "f"):
    _p = fe._panel("stock", as_of, 300, ("c", "f", _col))
    for _tk, _b in _p.items():
        bad = sum(1 for x in _b[_col] if x is None or x <= 0)
        if bad:
            _zero.append(f"{_tk}.{_col}×{bad}")
check(not _zero, "no price column in the panel contains a zero or a null",
      "; ".join(_zero[:6]))
_raw = db._one("""SELECT count(*) c FROM stockpricehistory
                  WHERE adj_close > 0 AND adj_final > 0
                    AND (adj_high <= 0 OR adj_high IS NULL OR adj_low <= 0
                         OR adj_low IS NULL OR adj_open <= 0 OR adj_open IS NULL)""")
check(_raw["c"] > 0,
      "…and the raw table really does still contain such rows, so the check means something",
      f"{_raw['c']} rows repaired on load")

# ── the interpreter's semantics ─────────────────────────────────────────────
def graph(nodes, edges):
    return {"nodes": nodes, "edges": edges}


N, E = fe._node, fe._edge

# close > open, on the last candle only.
g = graph([N("a", "price", 0, 0, field="close", shift=0),
           N("b", "price", 0, 0, field="open", shift=0),
           N("c", "compare", 0, 0, op=">"),
           N("o", "output", 0, 0, within=1)],
          [E("a", "c", "a"), E("b", "c", "b"), E("c", "o")])
up = fe.run(g, kind="stock")
check(up["scanned"] > 300, "a market-wide run scans the whole board", f"{up['scanned']} symbols")
check(0 < up["count"] < up["scanned"],
      "«close > open» matches some symbols but not all", f"{up['count']}")

# The negation must partition the market with it: every symbol is in exactly one
# of the two, which is only true if the interpreter's booleans are total.
g2 = json.loads(json.dumps(g))
g2["nodes"][2]["params"]["op"] = "<="
down = fe.run(g2, kind="stock")
check(up["count"] + down["count"] == up["scanned"],
      "«close > open» and «close ≤ open» partition the scanned symbols",
      f"{up['count']} + {down['count']} = {up['count'] + down['count']} vs {up['scanned']}")

# `within` widens the result and never narrows it.
g3 = json.loads(json.dumps(g))
g3["nodes"][3]["params"]["within"] = 10
wide = fe.run(g3, kind="stock")
check(wide["count"] >= up["count"],
      "«در N کندل اخیر» can only widen the match set",
      f"within=1 → {up['count']}, within=10 → {wide['count']}")

# A constant compared against itself is true everywhere; against a bigger one,
# nowhere. The two together prove constants broadcast and comparisons run.
def const_pair(op, x, y):
    return fe.run(graph([N("k1", "const", 0, 0, value=x), N("k2", "const", 0, 0, value=y),
                         N("c", "compare", 0, 0, op=op), N("o", "output", 0, 0, within=1)],
                        [E("k1", "c", "a"), E("k2", "c", "b"), E("c", "o")]), kind="stock")


check(const_pair(">", 2, 1)["count"] == const_pair(">", 2, 1)["scanned"],
      "a constant condition that is true matches every scanned symbol")
check(const_pair(">", 1, 2)["count"] == 0,
      "a constant condition that is false matches none")

# Common-subexpression elimination must not change the answer — the reference
# product's own layouts repeat one indicator chip five or six times.
dup = graph([N("i1", "ichimoku", 0, 0, tenkan=9, kijun=26, spanb=52),
             N("i2", "ichimoku", 0, 0, tenkan=9, kijun=26, spanb=52),
             N("p", "price", 0, 0, field="final", shift=0),
             N("c1", "compare", 0, 0, op=">"), N("c2", "compare", 0, 0, op=">"),
             N("and", "and", 0, 0), N("o", "output", 0, 0, within=1)],
            [E("i1", "c1", "b", from_port="spana"), E("p", "c1", "a"),
             E("i2", "c2", "b", from_port="spana"), E("p", "c2", "a"),
             E("c1", "and"), E("c2", "and"), E("and", "o")])
one = graph([N("i1", "ichimoku", 0, 0, tenkan=9, kijun=26, spanb=52),
             N("p", "price", 0, 0, field="final", shift=0),
             N("c1", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=1)],
            [E("i1", "c1", "b", from_port="spana"), E("p", "c1", "a"), E("c1", "o")])
check(fe.run(dup, kind="stock")["count"] == fe.run(one, kind="stock")["count"],
      "duplicated identical chips give the identical result (CSE is sound)")

# Signatures: the same node drawn twice shares one computation.
nodes, _, order, out = fe.normalise(dup)
check(nodes["i1"]["sig"] == nodes["i2"]["sig"],
      "two identical Ichimoku chips get one signature")
check(nodes["c1"]["sig"] == nodes["c2"]["sig"],
      "…and so do the comparisons built on them")

# ---------------------------------------------------------------------------
# EVERY BLOCK IN THE CATALOGUE ACTUALLY RUNS
#
# The single most valuable check in this file. A node type is four things —
# a catalogue entry, an `_eval_node` branch, a line in bars_needed() and a line
# in fields_needed() — and forgetting any ONE of the last three produces a chip
# that sits in the palette looking perfect and raises the moment it is wired.
# Two of them fail silently rather than loudly: a missing fields_needed() entry
# is a KeyError on a column the panel did not load, which run() swallows per
# symbol, so the filter simply matches nothing.
#
# So every block is wired into a real graph and run against the real market. It
# costs about a minute and it is the reason a seventy-block catalogue can be
# extended without fear.
# ---------------------------------------------------------------------------
# Load every column ONCE before timing anything. Without this the first block
# that needs `volume` pays for the query and is recorded as the slow one — which
# is exactly how this check first accused Aroon of taking ten minutes when the
# function itself does the whole market in 221 ms.
for _warm in ("open", "high", "low", "close", "final", "volume", "value", "count"):
    fe.run(graph([N("w", "price", 0, 0, field=_warm, shift=0),
                  N("k", "const", 0, 0, value=-1e18),
                  N("c", "compare", 0, 0, op=">", tol=0),
                  N("o", "output", 0, 0, within=1)],
                 [E("w", "c", "a"), E("k", "c", "b"), E("c", "o")]), kind="stock")

_broken = []
_slow = []
_ran = 0
for _spec in fe.NODE_TYPES:
    _t = _spec["type"]
    if _t in fe.SINK_TYPES:
        continue                                   # sinks — exercised everywhere else
    _params = {q["id"]: q["default"] for q in _spec["params"]}
    _nodes = [N("x", _t, 0, 0, **_params), N("o", "output", 0, 0, within=3)]
    _edges = []
    for _i, _port in enumerate(_spec["inputs"]):
        _src = f"in{_i}"
        if _port["kind"] == "bool":
            _nodes += [N(_src + "p", "price", 0, 0, field="close", shift=0),
                       N(_src + "k", "const", 0, 0, value=0),
                       N(_src, "compare", 0, 0, op=">", tol=0)]
            _edges += [E(_src + "p", _src, "a"), E(_src + "k", _src, "b")]
        elif _port["kind"] == "text":
            _nodes.append(N(_src, "symbol", 0, 0, field="group"))
        else:
            _nodes.append(N(_src, "price", 0, 0, field="close", shift=0))
        _edges.append(E(_src, "x", _port["id"]))
    _out = _spec["outputs"][0]
    if _out["kind"] == "bool":
        _edges.append(E("x", "o", "in", from_port=_out["id"]))
    else:
        _nodes += [N("k2", "const", 0, 0, value=0), N("cmp", "compare", 0, 0, op=">", tol=0)]
        _edges += [E("x", "cmp", "a", from_port=_out["id"]), E("k2", "cmp", "b"),
                   E("cmp", "o", "in")]
    _variants = [{}]
    # EVERY option of every dropdown, not just the defaults. «تابلو» was a dead
    # option nobody exercised; «٪B روی درصد تغییر» raised on every symbol in the
    # market. Both were one dropdown value away from the tested path.
    for _q in _spec["params"]:
        if _q["type"] == "select":
            _variants += [{_q["id"]: _o["v"]} for _o in _q["options"]
                          if _o["v"] != _q["default"]]

    try:
        _g = {"nodes": _nodes, "edges": _edges}
        _r = fe.run(_g, kind="stock")            # once to settle any cache
        _t0 = time.perf_counter()
        fe.run(_g, kind="stock")                 # …and this one is the measurement
        _ms = (time.perf_counter() - _t0) * 1000
        _ran += 1
        if _ms > 2500:
            _slow.append(f"{_t} {_ms:.0f}ms")
        if _r["scanned"] < 300:
            _broken.append(f"{_t}: scanned only {_r['scanned']}")
        # …and against ETFs, which are a different table and a different
        # metadata shape. Everything here was written and tested against stocks;
        # the only thing that proves it works for صندوق‌ها is running it there.
        _re = fe.run(_g, kind="etf")
        if _re["scanned"] < 100:
            _broken.append(f"{_t} (etf): scanned only {_re['scanned']}")

        # …and every dropdown value, asserting on `errors` rather than on an
        # exception: run() catches per-symbol failures so one bad symbol cannot
        # kill a scan, which means a broken block does not raise — it quietly
        # matches nothing. The count is the only way to see it.
        for _v in _variants:
            _p2 = dict(_params)
            _p2.update(_v)
            _n2 = [N("x", _t, 0, 0, **_p2) if _nd["id"] == "x" else _nd for _nd in _nodes]
            _r2 = fe.run({"nodes": _n2, "edges": _edges}, kind="stock")
            if _r2["errors"]:
                _broken.append(f"{_t} {_v}: {_r2['errors']} symbols raised")
    except Exception as _e:                                  # noqa: BLE001
        _broken.append(f"{_t}: {type(_e).__name__}: {_e}")

check(_ran >= 60, "every block in the catalogue was wired up and run", f"{_ran} blocks")
check(not _broken,
      "…every one against BOTH سهام and صندوق‌ها, and every dropdown value "
      "without a single symbol raising",
      "; ".join(_broken)[:260])
# One chip must not cost more than the rest of a graph put together. This
# caught MFI at 3.8 s and linear regression at 4.1 s, both O(n·window) where an
# incremental form is O(n); both are now under 150 ms.
check(not _slow, "…and none of them is pathologically slow (>2.5 s of compute)",
      "; ".join(_slow))

# Sub-groups: the palette's second level is server-side data, so it is checked
# here rather than in the browser.
_nosub = [n["type"] for n in fe.NODE_TYPES if not n.get("sub")]
check(not _nosub, "every block declares a palette sub-group", ",".join(_nosub))
_cats = {c["key"] for c in fe.CATEGORIES}
check(all(n["cat"] in _cats for n in fe.NODE_TYPES),
      "…inside a declared category")
check(len(fe.NODE_TYPES) >= 60, "the catalogue is a working set, not a demo",
      f"{len(fe.NODE_TYPES)} blocks in {len(fe.CATEGORIES)} categories")

# «ستون خروجی» decides the table's order. Before it carried a direction the
# answer was always "biggest first", which is wrong for every «فاصله تا…» column
# anyone writes.
def _dist_graph(sort):
    return graph([N("p", "price", 0, 0, field="final", shift=0),
                  N("h", "price", 0, 0, field="high", shift=0),
                  N("a", "agg", 0, 0, op="MAX", n=240),
                  N("m", "math", 0, 0, op="C%"),
                  N("k", "const", 0, 0, value=-8),
                  N("c", "compare", 0, 0, op=">=", tol=0),
                  N("o", "output", 0, 0, within=1),
                  N("col", "column", 0, 0, label="فاصله", digits=2, sort=sort)],
                 [E("h", "a", "a"), E("p", "m", "a"), E("a", "m", "b"),
                  E("m", "c", "a"), E("k", "c", "b"), E("c", "o"), E("m", "col", "a")])


_desc = fe.run(_dist_graph("desc"), kind="stock")["rows"]
_asc = fe.run(_dist_graph("asc"), kind="stock")["rows"]
check(len(_desc) > 10 and len(_asc) == len(_desc),
      "the sort direction does not change WHICH symbols match", f"{len(_desc)}")
check(_desc[0]["vals"]["col"] > _asc[0]["vals"]["col"],
      "…but «نزولی» and «صعودی» really do order the table differently",
      f"{_desc[0]['vals']['col']:.2f} vs {_asc[0]['vals']['col']:.2f}")
check(all(_asc[i]["vals"]["col"] <= _asc[i + 1]["vals"]["col"] for i in range(len(_asc) - 1)),
      "…and ascending is genuinely ascending, all the way down")

# The candle patterns must mean the same thing here as on /filters, or one page
# contradicts the other about «کندل پوشای صعودی».
_as_of = db.latest_date("stock")
_panel = fe._panel("stock", _as_of, 150, ("o", "h", "l", "c", "f"))
_agree = _checked = 0
for _tk, _b in list(_panel.items())[:250]:
    if len(_b["c"]) < 40:
        continue
    _checked += 1
    _keys, _ = db._eval_filters(_b["o"], _b["h"], _b["l"], _b["c"], _b["f"])
    for _pat in ("bull_engulf", "bear_engulf", "hammer", "shooting_star", "doji",
                 "piercing", "dark_cloud", "morning_star", "evening_star",
                 "three_white", "three_black"):
        _mine = fe._candle_series(_b["o"], _b["h"], _b["l"], _b["c"], _b["f"], _pat)[-1]
        if _mine != (_pat in _keys):
            _agree += 1
check(_checked > 100, "enough symbols to cross-check the candlestick patterns", f"{_checked}")
check(_agree == 0,
      "every candlestick pattern agrees with db._eval_filters on the last bar",
      f"{_agree} disagreements")

# ── the ready-made examples all run ─────────────────────────────────────────
for ex in fe.EXAMPLES:
    t0 = time.perf_counter()
    try:
        r = fe.run(ex["graph"], kind="stock")
        ms = (time.perf_counter() - t0) * 1000
        check(r["scanned"] > 300, f"example «{ex['name']}» runs",
              f"{r['count']} matched / {r['scanned']} scanned, {ms:.0f} ms, {r['bars']} bars")
    except Exception as e:                                   # noqa: BLE001
        check(False, f"example «{ex['name']}» runs", str(e)[:120])

# The bar budget must follow the graph, not a fixed maximum.
def budget(g):
    nodes, _edges, _order, out_node = fe.normalise(g)
    return fe.bars_needed(nodes, out_node)


shallow, deep = budget(one), budget(fe.EXAMPLES[1]["graph"])
check(shallow < deep, "bars_needed() asks for less history for a shallower graph",
      f"{shallow} vs {deep} (SMA 200)")

# Only the columns the graph reads are loaded.
check(set(fe.fields_needed(fe.normalise(one)[0])) == {"c", "f", "h", "l"},
      "fields_needed() narrows the SELECT to the columns actually read",
      str(fe.fields_needed(fe.normalise(one)[0])))

# ── no dropdown option may be dead ─────────────────────────────────────────
# «تابلو» was offered by the symbol node for a week before anyone noticed it
# returned an empty string for every stock: the option was in the catalogue and
# the column was on the `stocks` table, but _meta() never selected it. An option
# that silently answers nothing is worse than a missing one — the filter runs,
# matches nothing, and the user blames their own logic.
_meta_stock = fe._meta("stock")
_dead = []
for _opt in fe.NODE_BY_TYPE["symbol"]["params"][0]["options"]:
    _field = _opt["v"]
    _filled = sum(1 for m in _meta_stock.values() if (m.get(_field) or "").strip())
    if _filled < len(_meta_stock) * 0.5:
        _dead.append(f"{_field} ({_filled}/{len(_meta_stock)})")
check(not _dead,
      "every «اطلاعات نماد» field the palette offers has data behind it",
      "; ".join(_dead))
# The record is cached in REDIS, and cache.bump_version() only fires on a data
# update — never on a deploy. Without a shape tag in the key, a release that adds
# a field to _meta() serves the old dict for up to six hours, which is «تابلو»
# shipping broken all over again.
check(fe._META_SHAPE in str(fe._meta.__doc__ or "") or bool(fe._META_SHAPE),
      "the metadata cache key carries a shape tag", fe._META_SHAPE)
_src_fe = read("filter_engine.py")
check('cache.get_or_set("designer_meta", (_META_SHAPE, kind)' in _src_fe,
      "…and it is actually part of the key, not just declared")
# And it must actually narrow a scan, not just be non-empty.
_panel_graph = graph([N("s", "symbol", 0, 0, field="panel"),
                      N("m", "textmatch", 0, 0, op="ncontains", value="پایه"),
                      N("o", "output", 0, 0, within=1)],
                     [E("s", "m", "a"), E("m", "o")])
_pr = fe.run(_panel_graph, kind="stock")
check(0 < _pr["count"] < _pr["scanned"],
      "…and «تابلو» really does exclude بازار پایه",
      f"{_pr['count']} of {_pr['scanned']} kept")

# ── the «چرا؟» payload ──────────────────────────────────────────────────────
res = fe.run(fe.EXAMPLES[1]["graph"], kind="stock")
if res["rows"]:
    ex1 = fe.explain(fe.EXAMPLES[1]["graph"], "stock", res["rows"][0]["ticker"])
    check(ex1["matched"], "explain() agrees with run() that the symbol matched",
          ex1["ticker"])
    check(len(ex1["ports"]) >= 5, "explain() returns a value for every wired port",
          f"{len(ex1['ports'])} ports")
    check(0 <= ex1["at"] < ex1["within"] + 1,
          "explain() names the bar the filter fired on", f"at={ex1['at']}")
    # The bar it names has to be INSIDE the strip the browser is given, or the
    # canvas cannot show the value it is trying to explain.
    tails = [v for v in ex1["ports"].values() if v["kind"] in ("bool", "num")]
    check(all(len(v["tail"]) > ex1["at"] for v in tails),
          "…and that bar is inside the value strip sent to the browser",
          f"at={ex1['at']}, tail={len(tails[0]['tail'])}")
else:
    check(False, "the golden-cross example matched at least one symbol to explain")

# ── errors a user can act on, not stack traces ─────────────────────────────
def err(g):
    try:
        fe.run(g, kind="stock")
        return None
    except fe.GraphError as e:
        return str(e)


check(err({"nodes": [], "edges": []}) is not None,
      "a graph with no «خروجی فیلتر» is refused with a Persian message",
      err({"nodes": [], "edges": []}))
loop = graph([N("u", "unary", 0, 0, op="abs", scale=1), N("o", "output", 0, 0, within=1)],
             [E("u", "u", "a"), E("u", "o")])
check(err(loop) is not None, "a cycle is refused", err(loop))
two_out = graph([N("k", "const", 0, 0, value=1), N("o1", "output", 0, 0, within=1),
                 N("o2", "output", 0, 0, within=1)], [E("k", "o1"), E("k", "o2")])
check(err(two_out) is not None, "two output nodes are refused", err(two_out))
check(err(graph([N("x", "not_a_node", 0, 0), N("o", "output", 0, 0, within=1)], [])) is not None,
      "an unknown node type is refused")

# ── the limits ──────────────────────────────────────────────────────────────
huge = graph([N(f"k{i}", "const", 0, 0, value=1) for i in range(fe.MAX_NODES + 5)] +
             [N("o", "output", 0, 0, within=1)], [])
check(err(huge) is not None, "a graph over the node limit is refused",
      f"MAX_NODES={fe.MAX_NODES}")

# Out-of-range parameters are CLAMPED, not rejected: a slider that arrives as
# 10**9 is a curious user, and an error they cannot act on is worse than the
# documented ceiling.
clamped, _, _, _ = fe.normalise(graph(
    [N("s", "sma", 0, 0, n=10 ** 9, src="final", shift=-5),
     N("p", "price", 0, 0, field="close", shift=0),
     N("c", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=9999)],
    [E("s", "c", "a"), E("p", "c", "b"), E("c", "o")]))
check(clamped["s"]["params"]["n"] == fe.MAX_PERIOD,
      "an absurd period is clamped to the catalogue's maximum",
      str(clamped["s"]["params"]["n"]))
check(clamped["s"]["params"]["shift"] == 0,
      "a NEGATIVE shift is clamped to 0 — no filter may read a future bar")
check(clamped["o"]["params"]["within"] == fe.MAX_WITHIN,
      "«در N کندل اخیر» is clamped", str(clamped["o"]["params"]["within"]))

# An unknown select value falls back to the default rather than reaching a dict
# lookup as a KeyError.
odd, _, _, _ = fe.normalise(graph(
    [N("p", "price", 0, 0, field="'; DROP TABLE stocks; --", shift=0),
     N("k", "const", 0, 0, value=0), N("c", "compare", 0, 0, op="><"),
     N("o", "output", 0, 0, within=1)],
    [E("p", "c", "a"), E("k", "c", "b"), E("c", "o")]))
check(odd["p"]["params"]["field"] == "close" and odd["c"]["params"]["op"] == ">",
      "an unknown select value falls back to the catalogue default")

# ── the catalogue is coherent ───────────────────────────────────────────────
cat = fe.catalog()
cats = {c["key"] for c in cat["categories"]}
check(all(n["cat"] in cats for n in cat["nodes"]),
      "every node type belongs to a declared category")
bad_title = [n["type"] for n in cat["nodes"]
             for key in re.findall(r"\{~?(\w+)\}", n["title"])
             if key not in {p["id"] for p in n["params"]}]
check(not bad_title, "every {placeholder} in a chip caption names a real parameter",
      ",".join(bad_title))
dupes = [n["type"] for n in cat["nodes"] if [m["type"] for m in cat["nodes"]].count(n["type"]) > 1]
check(not dupes, "no duplicate node types", ",".join(dupes))
check(all(fe.NODE_BY_TYPE.get(n["type"]) for ex in fe.EXAMPLES
          for n in ex["graph"]["nodes"]),
      "every node used by an example exists in the catalogue")

# The whole point of Part A's last check: db.py's price-field vocabulary.
fields = {f["v"]: f["l"] for f in fe.PRICE_FIELDS}
check("پایانی" in fields["final"] and "آخرین معامله" in fields["close"],
      "«پایانی» is `final` and «آخرین معامله» is `close`, as everywhere else here")


# ===========================================================================
print()
print("=" * 74)
print("PART B — routes, saving and the rendered page")
print("=" * 74)

import app as A                                    # noqa: E402

uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")["id"]
client = A.app.test_client()
with client.session_transaction() as sess:
    sess["_user_id"] = str(uid)
    sess["_fresh"] = True

page = client.get("/filter-designer")
html = page.get_data(as_text=True)
check(page.status_code == 200, "/filter-designer renders", str(page.status_code))
check('id="designer-app"' in html, "…with the island's mount point")
check("dist/designer.js" in html, "…and the built bundle")
check("css/designer.css" in html, "…and its stylesheet")
check("طراحی فیلتر" in read("templates/base.html"),
      "the top navigation links to it")

# The results page: a shell that carries only an id. The graph reaches it
# through the draft or the database, never through this request.
res_page = client.get("/filter-designer/result")
res_html = res_page.get_data(as_text=True)
check(res_page.status_code == 200, "/filter-designer/result renders")
check('id="designer-result"' in res_html, "…with the results island's mount point")
check("dist/designer_result.js" in res_html, "…and its own bundle, not the editor's")
check('data-filter-id=""' in res_html, "…and no filter id when the URL names none")
with_id = client.get("/filter-designer/result?filter=42&kind=etf").get_data(as_text=True)
check('data-filter-id="42"' in with_id and 'data-kind="etf"' in with_id,
      "…and passes ?filter / ?kind through to the island")
check('data-filter-id=""' in client.get(
          "/filter-designer/result?filter=drop%20table").get_data(as_text=True),
      "…while a non-numeric ?filter is dropped rather than echoed")

cat_res = client.get("/api/designer/catalog?kind=stock")
payload = cat_res.get_json()
check(cat_res.status_code == 200, "/api/designer/catalog answers")
check(len(payload["nodes"]) >= 30, "…with the whole palette", f"{len(payload['nodes'])} nodes")
check(len(payload["examples"]) >= 8, "…and the ready-made examples",
      f"{len(payload['examples'])}")
check(all(n.get("sub") for n in payload["nodes"]),
      "…each carrying the sub-group the palette's second level is built from")
check(len(payload["groups"]) > 10, "…and the industry groups for the scope bar")

run_res = client.post("/api/designer/run",
                      json={"graph": fe.EXAMPLES[0]["graph"], "kind": "stock"})
out = run_res.get_json()
check(run_res.status_code == 200, "/api/designer/run answers")
check(out["scanned"] > 300 and out["count"] >= 0, "…having scanned the market",
      f"{out['count']}/{out['scanned']} in {out['server_ms']:.0f} ms")

# Scoping to one industry group must shrink the universe, not the logic.
grp = payload["groups"][0]
scoped = client.post("/api/designer/run",
                     json={"graph": fe.EXAMPLES[0]["graph"], "kind": "stock",
                           "group": grp}).get_json()
check(scoped["scanned"] < out["scanned"], f"…and «{grp}» narrows the scan",
      f"{scoped['scanned']} of {out['scanned']}")

bad = client.post("/api/designer/run", json={"graph": {"nodes": [], "edges": []}})
check(bad.status_code == 400 and "error" in bad.get_json(),
      "an unrunnable graph is a 400 with a message, not a 500",
      bad.get_json().get("error", "")[:60])
check(client.post("/api/designer/run", data="not json",
                  content_type="application/json").status_code == 400,
      "a non-JSON body is a 400")

# ETFs are a different table and a different metadata shape.
etf = client.post("/api/designer/run",
                  json={"graph": fe.EXAMPLES[0]["graph"], "kind": "etf"}).get_json()
check(etf["scanned"] > 0, "the same graph runs against صندوق‌ها", f"{etf['scanned']} scanned")

# ── saved filters ───────────────────────────────────────────────────────────
NAME = "__verify_designer__"
for row in fe.list_filters(uid):                     # a previous failed run
    if row["name"] == NAME:
        fe.delete_filter(uid, row["id"])

saved = client.post("/api/designer/filters",
                    json={"graph": fe.EXAMPLES[2]["graph"], "kind": "stock", "name": NAME})
check(saved.status_code == 200, "a filter can be saved")
fid = saved.get_json()["id"]
got = client.get(f"/api/designer/filters/{fid}").get_json()
check(got["name"] == NAME, "…listed and reopened by id")
check(len(got["graph"]["nodes"]) == len(fe.EXAMPLES[2]["graph"]["nodes"]),
      "…with every node preserved")
check(all("x" in n and "y" in n for n in got["graph"]["nodes"]),
      "…including the chip COORDINATES, so it reopens as it was drawn")

again = client.post("/api/designer/filters",
                    json={"graph": fe.EXAMPLES[3]["graph"], "kind": "etf", "name": NAME})
check(again.get_json()["id"] == fid, "saving under the same name updates in place")
check(len([f for f in fe.list_filters(uid) if f["name"] == NAME]) == 1,
      "…and does not leave a duplicate")
check(client.post("/api/designer/filters",
                  json={"graph": fe.EXAMPLES[0]["graph"], "kind": "stock",
                        "name": ""}).status_code == 400,
      "a filter with no name is refused")
check(client.post("/api/designer/filters",
                  json={"graph": {"nodes": [], "edges": []}, "kind": "stock",
                        "name": "broken"}).status_code == 400,
      "an unrunnable graph is never written to the table")
check(client.delete(f"/api/designer/filters/{fid}").status_code == 200,
      "a saved filter can be deleted")
check(client.get(f"/api/designer/filters/{fid}").status_code == 404,
      "…and is gone afterwards")

# One user must not be able to read another's.
other = db._one("SELECT id FROM users WHERE id <> %s ORDER BY id LIMIT 1", (uid,))
if other:
    mine = fe.save_filter(uid, NAME, "stock", fe.EXAMPLES[0]["graph"])
    check(fe.get_filter(other["id"], mine) is None,
          "a saved filter is private to the account that owns it")
    check(fe.delete_filter(other["id"], mine) == 0,
          "…and cannot be deleted by anyone else")
    fe.delete_filter(uid, mine)
else:
    print("  ..    only one account in the database; ownership check skipped")

# The table itself.
cols = {r["column_name"] for r in db._rows(
    "SELECT column_name FROM information_schema.columns WHERE table_name = 'custom_filters'")}
check({"user_id", "name", "kind", "graph", "created_at", "updated_at"} <= cols,
      "the custom_filters table has the expected columns")
check(bool(db._one("SELECT 1 AS x FROM pg_indexes WHERE indexname = "
                   "'idx_custom_filters_user'")),
      "…and the per-user index the picker reads through")

# Anonymous access.
anon = A.app.test_client()
check(anon.get("/filter-designer").status_code in (301, 302),
      "a signed-out visitor is sent to the login page")
check(anon.post("/api/designer/run", json={"graph": fe.EXAMPLES[0]["graph"]}).status_code == 401,
      "…and the run endpoint answers 401, not a scan")

# Help.
help_html = client.get("/help").get_data(as_text=True)
check('id="designer"' in help_html, "the help page documents the designer")
check("نتیجه" in help_html, "…and says where the results appear")
check("Ctrl" in help_html and "Delete" in help_html,
      "…including its keyboard shortcuts")


# ===========================================================================
print()
print("=" * 74)
print("PART C — the built bundle")
print("=" * 74)

newest_src = max(os.path.getmtime(os.path.join(FRONTEND, "src", "designer", f))
                 for f in os.listdir(os.path.join(FRONTEND, "src", "designer")))
for bundle in ("designer", "designer_result"):
    path = os.path.join(ROOT, "static", "dist", f"{bundle}.js")
    check(os.path.exists(path), f"static/dist/{bundle}.js exists")
    if os.path.exists(path):
        check(os.path.getmtime(path) >= newest_src,
              f"…and {bundle}.js is newer than every source file it is built from",
              time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(path))))
        check(f"{bundle}: resolve" in read("frontend/vite.config.ts"),
              f"…and the {bundle} entry is declared in vite.config.ts")

# The two entries must genuinely be two: the results page has no business
# shipping the palette, the inspector or undo.
editor = read("static/dist/designer.js")
results = read("static/dist/designer_result.js")
check("dz-palette" in editor, "the editor bundle carries the palette")
check("dz-palette" not in results,
      "…and the results bundle does not — they are separate entries, not one")
check("designer/run" in read("static/dist/designer_result.js") or
      "designer/run" in read("static/dist/chunk-draft-" + next(
          f.split("chunk-draft-")[1] for f in os.listdir(os.path.join(ROOT, "static", "dist"))
          if f.startswith("chunk-draft-") and f.endswith(".js")))
      or "designer/run" in results,
      "…and the results bundle is what calls /api/designer/run")

css = read("static/css/designer.css")
check("height: clamp(" in css and ".dz-stage {" in css,
      "the stage has a bounded height (the palette must not stretch the board)")
check("min-height: 62vh" not in css,
      "…and the board no longer states a competing height of its own")
check(".dz-board.is-readonly" in css and ".dz-node.is-ro" in css,
      "the read-only canvas has its own cursors, so it cannot look draggable")


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

    srv = werkzeug.serving.make_server("127.0.0.1", 5088, A.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with A.app.test_request_context():
        cookie = A.app.session_interface.get_signing_serializer(A.app).dumps(
            {"_user_id": str(uid), "_fresh": True})

    proc = None
    try:
        proc = subprocess.run(
            ["node", "designer_check.mjs", "http://127.0.0.1:5088", cookie],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=FRONTEND, timeout=900, shell=(os.name == "nt"))
        lines = (proc.stdout or "").strip().splitlines()
        b = json.loads(lines[-1]) if lines else {}
    except Exception as e:                                   # noqa: BLE001
        b = {}
        check(False, "the browser check ran",
              f"{e} {proc.stderr[:300] if proc else ''}")
    finally:
        srv.shutdown()

    if b:
        check(not b["errors"], "no uncaught JavaScript errors", "; ".join(b["errors"])[:200])
        check(not b["console"], "no console errors", "; ".join(b["console"])[:200])

        # ── it drew ─────────────────────────────────────────────────────────
        check(b["nodes"] == 17 and b["wires"] == 16,
              "the opening example draws every chip and every wire",
              f"{b['nodes']} chips / {b['wires']} wires")
        check(b["captions"] == b["nodes"],
              "every chip carries its category caption underneath")
        check(b["inPorts"] == 12 and b["outPorts"] == 16,
              "…and the right number of input and output dots",
              f"{b['inPorts']} in / {b['outPorts']} out")
        check(len(b["fills"]) >= 4,
              "chips are filled by category, not all one colour",
              f"{len(b['fills'])} distinct fills")
        # The reference product's own vocabulary has to be what is on screen.
        joined = " ".join(b["titles"])
        # «MA» not «SMA»: the moving-average block now carries the guide's
        # «روش میانگین متحرک», so its caption names the method the user chose.
        for want in ("close", "open", "a < b", "a > b", "And", "MA"):
            check(want in joined, f"the board reads «{want}» the way آسان‌بورس does")
        check(b["shelves"] >= 9 and b["parts"] >= 60,
              "the palette lists every category and every part",
              f"{b['shelves']} shelves / {b['parts']} parts")
        check(b["subheads"] >= 8,
              "…and breaks the long categories into sub-shelves",
              f"{b['subheads']} sub-shelves")
        for _want in ("میانگین‌ها", "نوسان‌نما", "حجم"):
            check(_want in b["subLabels"], f"…including «{_want}»")

        # ── it fits the window ──────────────────────────────────────────────
        check(b["boardH"] <= b["viewportH"],
              "the board fits inside the window",
              f"{b['boardH']}px board / {b['viewportH']}px window")
        check(b["chipsInBoard"] == b["nodes"],
              "…and the opening auto-fit puts every chip inside it",
              f"{b['chipsInBoard']} of {b['nodes']}")
        check(not b["hScroll"], "the page does not scroll sideways")
        check(b["ltr"] == "ltr",
              "the canvas is LTR inside the RTL page, so the graph flows to the output")

        # ── it can be used ──────────────────────────────────────────────────
        check(abs(b["dragDx"] - 90) <= 12 and abs(b["dragDy"] - 68) <= 12,
              "a chip follows the mouse when dragged",
              f"moved {b['dragDx']},{b['dragDy']} for a 90,68 gesture")
        check(b["dragSelected"] == 1 and b["inspectorFields"] > 0,
              "…and grabbing it opens its parameters in the inspector")
        check(b["addedNode"] == 2, "clicking the palette adds a chip to the board")
        check(b["liveWire"] == 1, "dragging from an output draws a wire on the cursor")
        check(b["wiredDelta"] == 1, "…and dropping it on an input connects the two")
        check(b["hotWire"] == 1, "hovering a wire highlights it")
        check(b["afterCut"] == 0, "…and clicking it cuts the connection")
        check(b["afterUndoNodes"] == b["nodes"], "Ctrl+Z undoes back to the start",
              f"{b['afterUndoNodes']} chips")
        check(b["zoomChanged"], "the zoom controls work")

        # ── it runs, ON A PAGE OF ITS OWN ───────────────────────────────────
        check(b["navigated"] == "/filter-designer/result",
              "«اجرا» NAVIGATES to the results page rather than filling a panel",
              b["navigated"])
        check(b["resultRows"] > 0, "…and that page runs the filter and fills the table",
              f"{b['resultRows']} rows in {b['runMs']} ms")
        check(b["resultCols"] >= 5,
              "…including the user's own «ستون خروجی» column", f"{b['resultCols']} columns")
        check("۷" in b["countLabel"] or "7" in b["countLabel"],
              "…and says how many symbols were scanned", b["countLabel"])
        check(b["hasCsv"] >= 1, "…and offers the matches as a CSV")
        check(b["backLink"] >= 1, "…and a way back to the canvas")
        check(b["graphHiddenAtFirst"],
              "the diagram starts COLLAPSED — the table is what this page is for")

        # ── «فیلترهای دیگر»: the rail ───────────────────────────────────────
        check(b["railItems"] >= 8, "the results page lists the other filters",
              f"{b['railItems']} in the rail")
        check(b["railActive"] == 1, "…marking the one that produced this table")
        check(b["railSwitchedCount"] != b["countLabel"].strip(),
              "…and picking another one re-runs it",
              f"{b['countLabel'].strip()} → {b['railSwitchedCount']}")
        check(b["railStillSamePage"],
              "…in place, without leaving the results page")
        check(b["railRows"] > 0, "…filling the table with the new filter's matches",
              f"{b['railRows']} rows")
        check(b["railActiveAfter"] == 1,
              "…and the rail follows, marking the new one")
        check(not b["resultHScroll"], "the results page does not scroll sideways")

        # ── a big result is VIRTUALIZED, like /screener and /performance ────
        check(b["bigMatched"] > 100,
              "a broad filter matches hundreds of symbols",
              f"{b['bigMatched']} matched")
        check(b["bigDomRows"] < b["bigMatched"] / 3,
              "…and the table keeps only a window of them in the DOM",
              f"{b['bigDomRows']} rows rendered of {b['bigMatched']}")
        check(b["bigPads"] >= 1,
              "…standing in for the rest with spacer rows")
        check(b["bigCols"] == b["bigHeaders"],
              "…and the <colgroup> table-layout:fixed needs matches the headers",
              f"{b['bigCols']} cols / {b['bigHeaders']} headers")

        # ── it explains ─────────────────────────────────────────────────────
        check(b["graphOpened"], "«چرا؟» opens the diagram it is about to paint")
        check(b["verdicts"] > 0, "…and paints a verdict on every chip",
              f"{b['verdicts']} badges")
        check(b["verdictOn"] > 0,
              "…at least one of them a ✓ — the values are read at the bar that FIRED, "
              "not at the last bar", f"{b['verdictOn']} true")
        check(b["tintedWires"] > 0, "…and the satisfied wires are tinted")
        check(b["resultChipsInBoard"] == b["resultNodes"],
              "…with the whole diagram framed inside its panel",
              f"{b['resultChipsInBoard']} of {b['resultNodes']}")
        check(b["explainRows"] >= 5,
              "the same answer is listed as a table, for the numbers",
              f"{b['explainRows']} rows")
        check(b["explainVerdictCells"] > 0, "…with the conditions marked برقرار / نیست")

        # ── the diagram there is a REFERENCE, not a workspace ───────────────
        check(not b["readonlyChipMoved"],
              "dragging a chip on the results diagram does not move it")
        check(b["readonlyPanned"], "…it pans the board instead")
        check(b["readonlySelected"] == 0, "…and selects nothing")
        check(b["readonlyEdgesAfterClick"] == b["readonlyEdges"],
              "clicking a wire there does not cut it",
              f"{b['readonlyEdges']} → {b['readonlyEdgesAfterClick']}")

        # ── and back ────────────────────────────────────────────────────────
        # NOT exampleNodes: the rail switched filters above, and the draft is
        # meant to follow — «بازگشت» opens the filter you were just LOOKING at,
        # not the one you arrived with. That is the behaviour, so assert it.
        check(b["backNodes"] == b["resultNodes"],
              "«بازگشت به طراحی» opens the filter the rail switched to",
              f"{b['backNodes']} chips, matching the diagram's {b['resultNodes']}")
        check(b["backEditable"] > 0, "…on the editable canvas, palette and all")
        check(b["backName"] and b["backName"] in b["railTargetName"],
              "…under that filter's own name", f"«{b['backName']}»")

        # ── it survives the theme ───────────────────────────────────────────
        check(b["darkBoardBg"] != "rgba(0, 0, 0, 0)",
              "the board paints its own background in dark mode", b["darkBoardBg"])
        check(b["darkFillsFromPalette"],
              "…and every chip is still painted its CATEGORY's colour in dark mode",
              " ".join(b["darkFills"])[:120])
        check(len(b["darkFills"]) >= 3,
              "…across several categories, not one", f"{len(b['darkFills'])} fills")


# ===========================================================================
print()
print("=" * 74)
print("PART E — «تایم فریم»، «برگشت به عقب» and the guide's own blocks")
print("=" * 74)
#
# Everything in this part exists because it is the kind of defect the rest of
# the suite cannot see: a filter that returns an empty table, or a plausible
# one, while quietly computing the wrong thing. A weekly average off by one
# bucket, a shift applied twice, a formula that reads a field nobody loaded —
# none of those raise, and all of them are wrong answers with a straight face.

import datetime                                     # noqa: E402
import collections                                  # noqa: E402

# ── the two properties, on every block that the guide puts them on ──────────
no_tf = sorted(t for t in fe.FRAMED_TYPES
               if not any(p["id"] == "tf" for p in fe.NODE_BY_TYPE[t]["params"]))
no_sh = sorted(t for t in fe.SHIFTED_TYPES
               if not any(p["id"] == "shift" for p in fe.NODE_BY_TYPE[t]["params"]))
check(not no_tf, "every framed block carries «تایم فریم»", ",".join(no_tf))
check(not no_sh, "every framed block carries «برگشت به عقب»", ",".join(no_sh))
check(len(fe.FRAMED_TYPES) > 40,
      "…and that is most of the catalogue, not a handful",
      f"{len(fe.FRAMED_TYPES)} of {len(fe.NODE_TYPES)}")

# A block must not carry the shift TWICE — once in its own params and once from
# the loop that attaches them. Two dials that do one thing is how a filter ends
# up shifted by two when the user asked for one.
dbl = [n["type"] for n in fe.NODE_TYPES
       if sum(1 for p in n["params"] if p["id"] == "shift") > 1
       or sum(1 for p in n["params"] if p["id"] == "tf") > 1]
check(not dbl, "no block carries either property twice", ",".join(dbl))

# ── the weekly frame IS the week ────────────────────────────────────────────
# Computed independently from the raw table, not from the engine's own helpers.
tk = "فولاد"
raw = db._rows("""SELECT date, adj_final, adj_high, volume FROM stockpricehistory
                  WHERE ticker = %s AND adj_close > 0 AND adj_final > 0
                  ORDER BY date DESC LIMIT 120""", (tk,))
raw = list(reversed(raw))
weeks = collections.OrderedDict()
for r in raw:
    key = (r["date"] - datetime.date(2000, 1, 1)).days // 7
    w = weeks.setdefault(key, {"f": None, "h": 0.0, "v": 0})
    w["f"] = float(r["adj_final"])                  # last of the week
    w["h"] = max(w["h"], float(r["adj_high"]))
    w["v"] += int(r["volume"] or 0)
hand = list(weeks.values())

wk_graph = graph([N("f", "price", 0, 0, field="final", tf="W"),
                  N("h", "price", 0, 0, field="high", tf="W"),
                  N("v", "price", 0, 0, field="volume", tf="W"),
                  N("k", "const", 0, 0, value=-1e12),
                  N("c", "compare", 0, 0, op=">"),
                  N("o", "output", 0, 0, within=1)],
                 [E("f", "c", "a"), E("k", "c", "b"), E("c", "o")])
ex = fe.explain(wk_graph, "stock", tk, tail=6)
eng_f = [x for x in ex["ports"]["f:out"]["tail"] if x is not None]
eng_h = [x for x in ex["ports"]["h:out"]["tail"] if x is not None]
eng_v = [x for x in ex["ports"]["v:out"]["tail"] if x is not None]
check(eng_f and round(eng_f[-1], 4) == round(hand[-1]["f"], 4),
      "the weekly «پایانی» is the week's LAST session, not its average",
      f"{eng_f[-1]:.1f} vs {hand[-1]['f']:.1f}")
check(eng_h and round(eng_h[-1], 4) == round(hand[-1]["h"], 4),
      "…the weekly high is the week's MAXIMUM",
      f"{eng_h[-1]:.1f} vs {hand[-1]['h']:.1f}")
check(eng_v and round(eng_v[-1]) == hand[-1]["v"],
      "…and the weekly volume is the week's SUM",
      f"{eng_v[-1]:.0f} vs {hand[-1]['v']}")

# The frame value is HELD across its own sessions — that is what makes a weekly
# average comparable against a daily price on every bar, and it is the property
# that silently breaks if the index map is ever rebuilt per column.
runs = ex["ports"]["f:out"]["tail"]
check(len(set(x for x in runs if x is not None)) < len([x for x in runs if x is not None]),
      "…and it is held across every session of that week")

# A week starts on SATURDAY. Any other choice splits the Tehran trading week in
# two, and the average of half a week is a number with no meaning at all.
sat = [r["date"] for r in raw if r["date"].weekday() == 5]
check(all(((d - datetime.date(2000, 1, 1)).days // 7)
          != ((d - datetime.timedelta(days=1) - datetime.date(2000, 1, 1)).days // 7)
          for d in sat[-8:]),
      "the weekly bucket rolls over on Saturday, not on Monday")

# ── the monthly frame is the JALALI month ───────────────────────────────────
mo_graph = json.loads(json.dumps(wk_graph))
for node in mo_graph["nodes"]:
    if node["params"].get("tf") == "W":
        node["params"]["tf"] = "M"
mo = fe.explain(mo_graph, "stock", tk, tail=8)
jm = db._rows("""SELECT j_date, adj_final FROM stockpricehistory
                 WHERE ticker = %s AND adj_close > 0 ORDER BY date DESC LIMIT 1""", (tk,))
last_final = float(jm[0]["adj_final"])
eng_mo = [x for x in mo["ports"]["f:out"]["tail"] if x is not None]
check(eng_mo and round(eng_mo[-1], 4) == round(last_final, 4),
      "the monthly «پایانی» is the month's last session so far", jm[0]["j_date"])

# ── «برگشت به عقب» is one bar back, once ────────────────────────────────────
sh = graph([N("a", "sma", 0, 0, n=10, method="sma", src="final", shift=0),
            N("b", "sma", 0, 0, n=10, method="sma", src="final", shift=3),
            N("k", "const", 0, 0, value=-1e12),
            N("c", "compare", 0, 0, op=">"),
            N("o", "output", 0, 0, within=1)],
           [E("a", "c", "a"), E("k", "c", "b"), E("c", "o")])
sx = fe.explain(sh, "stock", tk, tail=12)
plain, moved = sx["ports"]["a:out"]["tail"], sx["ports"]["b:out"]["tail"]
check(plain[:-3] == moved[3:],
      "«برگشت به عقب ۳» is the same series delayed by exactly three bars — no more")

# The same must be true of a block that used to apply its own shift internally,
# because that is the one that can now do it twice.
sh2 = json.loads(json.dumps(sh))
sh2["nodes"][0].update({"type": "price", "params": {"field": "final", "shift": 0}})
sh2["nodes"][1].update({"type": "price", "params": {"field": "final", "shift": 2}})
px = fe.explain(sh2, "stock", tk, tail=12)
check(px["ports"]["a:out"]["tail"][:-2] == px["ports"]["b:out"]["tail"][2:],
      "…and «داده قیمت» shifts once, not twice")

# The catalogue-wide sweep in PART A already runs every dropdown value of every
# block against both سهام and صندوق‌ها and asserts on `errors`; adding «تایم
# فریم» and «برگشت به عقب» to the catalogue put the two new properties inside
# that sweep automatically, which is the point of attaching them from the
# category rather than by hand.

# ── «فرمول‌نویسی» ───────────────────────────────────────────────────────────
ok_exprs = ["1+2*3", "(high-low)/final*100", "min(a,b)", "abs(-close)",
            "if(close>final,1,0)", "2^10", "sqrt(volume)", "-close", "pi*2"]
bad_exprs = ["", "close +", "1+", "(1", "1)", "close & 1", "__import__('os')",
             "close.__class__", "foo(1)", "min(1)", "1 if 2 else 3", "a[0]",
             "1;2", "close" + "+1" * 200]
parse_fail = [e for e in ok_exprs if not _parses(e)]
parse_pass = [e for e in bad_exprs if _parses(e)]
check(not parse_fail, "the formula language accepts what it documents",
      " | ".join(parse_fail))
check(not parse_pass, "…and refuses everything else, with a message",
      " | ".join(parse_pass))
# Non-chaining: `a<b<c` looks like a range and is not one, so it must be a
# syntax error rather than a silent `(a<b)<c` that compares 1 against a price.
check(not _parses("close<final<high"),
      "…including a chained comparison, which reads as a range and is not one")

# The parser is the security boundary: there is no eval() behind it, so an
# expression can only ever name what the two tables allow.
# Parsed, not grepped. A substring test matched `_eval_node(`; a regex with a
# word boundary then matched the DOCSTRING that says «There is no eval()» —
# both would have passed whatever the file actually did. The syntax tree is the
# only reading of this question that cannot be fooled by prose.
import ast                                          # noqa: E402

_dangerous = {"eval", "exec", "compile", "__import__", "getattr", "setattr"}
_calls = {n.func.id for n in ast.walk(ast.parse(read("filter_engine.py")))
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
check(not (_calls & _dangerous),
      "…and no eval()/exec()/getattr() exists anywhere in the engine to fall back on",
      ",".join(sorted(_calls & _dangerous)))

# The value must equal the same thing built out of boxes.
fml = graph([N("f", "formula", 0, 0, expr="(high-low)/final*100"),
             N("k", "const", 0, 0, value=-1e12), N("c", "compare", 0, 0, op=">"),
             N("o", "output", 0, 0, within=1)],
            [E("f", "c", "a"), E("k", "c", "b"), E("c", "o")])
box = graph([N("h", "price", 0, 0, field="high"), N("l", "price", 0, 0, field="low"),
             N("fi", "price", 0, 0, field="final"),
             N("d", "math", 0, 0, op="-"), N("r", "math", 0, 0, op="/"),
             N("h1", "const", 0, 0, value=100.0), N("m", "math", 0, 0, op="*"),
             N("k", "const", 0, 0, value=-1e12), N("c", "compare", 0, 0, op=">"),
             N("o", "output", 0, 0, within=1)],
            [E("h", "d", "a"), E("l", "d", "b"), E("d", "r", "a"), E("fi", "r", "b"),
             E("r", "m", "a"), E("h1", "m", "b"), E("m", "c", "a"), E("k", "c", "b"),
             E("c", "o")])
fx = fe.explain(fml, "stock", tk, tail=4)["ports"]["f:out"]["tail"]
bx = fe.explain(box, "stock", tk, tail=4)["ports"]["m:out"]["tail"]
check([round(v, 9) for v in fx] == [round(v, 9) for v in bx],
      "a formula computes what the same expression built out of boxes computes")

# It must read only the columns it names — the whole reason fields_needed()
# walks the AST instead of loading everything.
fnodes, _, _, _ = fe.normalise(fml)
need = set(fe.fields_needed(fnodes))
check("v" not in need and {"h", "l", "f"} <= need,
      "…and loads only the price columns the expression names", ",".join(sorted(need)))

# ── «سقف و کف مجاز» ─────────────────────────────────────────────────────────
q = fe.run(graph([N("p", "pricelimit", 0, 0, band="auto"),
                  N("o", "output", 0, 0, within=1, sort="value"),
                  N("col", "column", 0, 0, label="d", digits=4, sort="asc")],
                 [E("p", "o", "in", "buyq"), E("p", "col", "a", "dup")]), kind="stock")
check(q["count"] > 0, "«صف خرید» finds symbols on the last session", f"{q['count']}")
check(all(abs(r["vals"]["col"]) < 0.2 for r in q["rows"]),
      "…and every one of them really is sitting on its ceiling",
      f"worst {max((abs(r['vals']['col']) for r in q['rows']), default=0):.3f}%")
# The bug this pins: a one-sided test called every reopening («بازگشایی»), which
# trades with NO limit and routinely closes far above yesterday's ceiling, a buy
# queue — the one session on which there is no queue at all.
check(not any(r["vals"]["col"] < -0.2 for r in q["rows"]),
      "…and a symbol that reopened above its old ceiling is not called a queue")

bands = {r["ticker"]: r for r in q["rows"]}
mkt = {r["ticker"]: r["market"] for r in db._rows("SELECT ticker, market FROM stocks")}
moves = []
for t, r in list(bands.items())[:40]:
    hist = db._rows("""SELECT adj_close, adj_final FROM stockpricehistory
                       WHERE ticker=%s AND adj_close>0 ORDER BY date DESC LIMIT 2""", (t,))
    if len(hist) == 2 and float(hist[1]["adj_final"]):
        moves.append((mkt.get(t), (float(hist[0]["adj_close"])
                                   / float(hist[1]["adj_final"]) - 1) * 100))
wrong = [(m, v) for m, v in moves
         if m in fe._BAND_BY_MARKET and abs(v - fe._BAND_BY_MARKET[m]) > 0.05]
check(not wrong,
      "«خودکار» picks each symbol's own band — پایه زرد moves 3٪, بورس 5٪",
      str(wrong[:2]))

# ── «حمایت و مقاومت» does not read the future ───────────────────────────────
srg = graph([N("s", "srlevel", 0, 0, k=3), N("k", "const", 0, 0, value=-1e12),
             N("c", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=1)],
            [E("s", "c", "a", "res"), E("k", "c", "b"), E("c", "o")])
bars = fe._panel("stock", as_of, 300, fe.fields_needed(fe.normalise(srg)[0]))[tk]
res_full, _ = fe._swing_levels(bars["h"], bars["l"], bars["c"], 3)
cut = 40
res_short, _ = fe._swing_levels(bars["h"][:-cut], bars["l"][:-cut], bars["c"][:-cut], 3)
check(res_full[:len(res_short)] == res_short,
      "a level computed today is the same level it was before the later bars "
      "existed — no lookahead")

# ── «فهرست نمادها» ──────────────────────────────────────────────────────────
uni = fe.run(graph([N("u", "universe", 0, 0, market="پایه زرد"),
                    N("o", "output", 0, 0, within=1)], [E("u", "o")]), kind="stock")
all_yellow = {r["ticker"] for r in db._rows(
    "SELECT ticker FROM stocks WHERE market = 'پایه زرد'")}
got = {r["ticker"] for r in uni["rows"]}
check(got and got <= all_yellow,
      "«فهرست نمادها» restricts the scan to the market it names",
      f"{len(got)} of {len(all_yellow)}")
uni2 = fe.run(graph([N("u", "universe", 0, 0, tickers="فولاد، وبملت"),
                     N("o", "output", 0, 0, within=1)], [E("u", "o")]), kind="stock")
check({r["ticker"] for r in uni2["rows"]} == {"فولاد", "وبملت"},
      "…and a Persian-comma ticker list matches exactly those tickers")

# ── «برچسب سیگنال» ──────────────────────────────────────────────────────────
sig = fe.run(graph([N("a", "price", 0, 0, field="close"), N("b", "price", 0, 0, field="open"),
                    N("c", "compare", 0, 0, op=">"),
                    N("s1", "signal", 0, 0, signal="buy"),
                    N("o", "output", 0, 0, within=1)],
                   [E("a", "c", "a"), E("b", "c", "b"), E("c", "o"), E("c", "s1", "in")]),
             kind="stock")
sigcol = [c for c in sig["columns"] if c["id"] == fe.SIGNAL_COL]
check(len(sigcol) == 1 and sigcol[0]["type"] == "text",
      "«برچسب سیگنال» adds ONE text column however many signal blocks there are")
check(sig["rows"] and all(r["vals"][fe.SIGNAL_COL] == "خرید" for r in sig["rows"]),
      "…carrying the label whose condition held on the bar that matched")

# ── «مرتب‌سازی» on «خروجی فیلتر» ────────────────────────────────────────────
def sorted_by(mode):
    g = graph([N("a", "price", 0, 0, field="close"), N("b", "price", 0, 0, field="open"),
               N("c", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=1, sort=mode)],
              [E("a", "c", "a"), E("b", "c", "b"), E("c", "o")])
    return fe.run(g, kind="stock")["rows"]

by_ticker = [r["ticker"] for r in sorted_by("ticker")]
check(by_ticker == sorted(by_ticker), "«مرتب‌سازی: نام نماد» really is alphabetical")
by_price = [r["latest"] for r in sorted_by("price")]
check(by_price == sorted(by_price, reverse=True), "«مرتب‌سازی: قیمت پایانی» is descending")
check(len(sorted_by("value")) == len(by_price),
      "…and the default («ارزش معاملات») returns the same match set, reordered")

# ── «توضیحات» changes nothing ───────────────────────────────────────────────
plain_g = graph([N("a", "price", 0, 0, field="close"), N("b", "price", 0, 0, field="open"),
                 N("c", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=1)],
                [E("a", "c", "a"), E("b", "c", "b"), E("c", "o")])
noted = json.loads(json.dumps(plain_g))
noted["nodes"].append(N("nt", "note", 0, 0, text="یادداشت"))
check(fe.run(plain_g, kind="stock")["count"] == fe.run(noted, kind="stock")["count"],
      "a «توضیحات» note on the canvas cannot change the result")
check(fe.bars_needed(*fe.normalise(noted)[::3]) == fe.bars_needed(*fe.normalise(plain_g)[::3]),
      "…and cannot widen the read window either")

# ── the read window ─────────────────────────────────────────────────────────
def window(tf, n=20):
    g = graph([N("m", "sma", 0, 0, n=n, tf=tf), N("k", "const", 0, 0, value=0),
               N("c", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=1)],
              [E("m", "c", "a"), E("k", "c", "b"), E("c", "o")])
    nodes, _, _, out = fe.normalise(g)
    return fe._raw_bars_needed(nodes, out)

check(window("D") < window("W") < window("M"),
      "«تایم فریم» multiplies the read window rather than ignoring it",
      f"D={window('D')} W={window('W')} M={window('M')}")
check(window("W") >= window("D", 20) * 4,
      "…by about five sessions a week", f"{window('W')} vs {window('D')}")
deep = fe.run(graph([N("m", "sma", 0, 0, n=300, tf="M"), N("k", "const", 0, 0, value=0),
                     N("c", "compare", 0, 0, op=">"), N("o", "output", 0, 0, within=1)],
                    [E("m", "c", "a"), E("k", "c", "b"), E("c", "o")]), kind="stock")
check(deep["clipped"] is True,
      "a graph that wants more history than exists SAYS so instead of returning "
      "an empty table")
check(fe.run(plain_g, kind="stock")["clipped"] is False,
      "…and an ordinary graph does not")

# ── «بلاک هشدار» ────────────────────────────────────────────────────────────
def _clear_events(user_id, rule):
    """db._rows() is a SELECT helper — it calls fetchall(), which raises on a
    DELETE. The probe below has to leave the notification feed exactly as it
    found it, so the cleanup gets its own connection."""
    conn = db.get_db()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM alert_events WHERE user_id=%s AND rule=%s",
                        (user_id, rule))
        conn.commit()
    finally:
        db.release(conn)



alert_graph = graph([N("u", "universe", 0, 0, tickers="فولاد"),
                     N("o", "output", 0, 0, within=1), N("al", "alert", 0, 0,
                                                         once="1", msg="«{ticker}» — {filter}")],
                    [E("u", "o"), E("u", "al", "in")])
fid = fe.save_filter(uid, "verify-alert-probe", "stock", alert_graph)
try:
    armed = [f["id"] for f in fe.alerting_filters()]
    check(fid in armed, "a saved graph with an «هشدار» block is armed by that block alone")
    listed = [f for f in fe.list_filters(uid) if f["id"] == fid]
    check(listed and listed[0]["alert"], "…and the picker can see that it is armed")

    _clear_events(uid, fe._alert_rule(fid))
    first = fe.evaluate_filter_alerts()
    events = db._rows("SELECT ticker, message FROM alert_events WHERE user_id=%s AND rule=%s",
                      (uid, fe._alert_rule(fid)))
    check(any(e["ticker"] == "فولاد" for e in events),
          "…and one pass records the symbols it matched", f"{first['fired']} events")
    check(any("فولاد" in (e["message"] or "") and "verify-alert-probe" in (e["message"] or "")
              for e in events),
          "…with the user's own message template filled in")
    second = fe.evaluate_filter_alerts()
    again = db._rows("SELECT count(*) c FROM alert_events WHERE user_id=%s AND rule=%s",
                     (uid, fe._alert_rule(fid)))[0]["c"]
    check(again == len(events),
          "…and a second pass on the same session repeats nothing",
          f"{len(events)} then {again}")
    # Not merely "writes nothing" — it must not RUN. This task fires every three
    # hours and each armed filter is a market-wide scan; without the session
    # marker the quiet case (same symbols as this morning) is indistinguishable
    # from "never ran" and costs eight scans a day to write nothing.
    check(second["skipped"] == 1 and second["checked"] == 0,
          "…and does not re-scan the market to find that out",
          f"checked={second['checked']} skipped={second['skipped']}")
    # Editing the graph re-arms it: what the OLD graph matched says nothing
    # about what this one matches.
    fe.save_filter(uid, "verify-alert-probe", "stock", alert_graph, filter_id=fid)
    check(db._one("SELECT alert_jd FROM custom_filters WHERE id=%s", (fid,))["alert_jd"] is None,
          "…and saving a change to the graph arms it again")
    # A user-supplied template is TEXT, never a format string: `{0.__class__}`
    # in str.format() is a way to walk the object graph out of a template.
    check("{ticker.__class__}" == fe._alert_text("{ticker.__class__}", "f",
                                                 {"ticker": "x", "name": "y"}),
          "an unknown placeholder in a message template is left alone, not evaluated")
finally:
    _clear_events(uid, fe._alert_rule(fid))
    fe.delete_filter(uid, fid)


print()
print("=" * 74)
print(f"{PASS} passed, {FAIL} failed")
print("=" * 74)
sys.exit(1 if FAIL else 0)
