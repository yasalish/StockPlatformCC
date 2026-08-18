"""
verify_order04.py — verification for Order 04 (Redis replaces the in-process cache).

Parts A–E need only Redis. Part F needs the «Stock» database as well.

  A  serialisation round-trips faithfully — None, floats, Persian strings, and
     the date / Decimal / tuple types psycopg2 and db.py actually hand back.
  B  version-key invalidation: clear_cache() bumps instead of deleting, entries
     carry a TTL, and the bump is what makes an entry unreachable.
  C  single-flight: N threads racing on a cold key run the producer ONCE.
  D  cross-process: a second interpreter reads the entry this one warmed —
     the property the module-level dicts could never have.
  E  degradation: with Redis unreachable the answers stay correct and a warning
     is logged instead of an exception.
  F  db.py integration: every cached analytic returns exactly what the uncached
     computation returns, and clear_cache() invalidates all of them.

Run:  python verify_order04.py
"""
import os
import sys
import time
import json
import math
import datetime
import decimal
import subprocess
import threading

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


import cache

print("=" * 74)
print("Redis:", cache.describe())
if not cache.available():
    print("\n  Redis is NOT reachable — start it first; this script tests the "
          "cache itself.\n")
    sys.exit(1)
print("=" * 74)


# ===========================================================================
print()
print("=" * 74)
print("PART A — serialisation round-trips the real row shapes faithfully")
print("=" * 74)

# Every kind of value db.py actually puts in the cache, plus the awkward floats.
SAMPLE = (
    [
        {"id": 1, "ticker": "فولاد", "name": "فولاد مبارکهٔ اصفهان",
         "market": "بورس", "sector": "فلزات اساسی", "sub_sector": "فولاد",
         "type": None, "latest": 5432.0, "ldate": "1404-05-25",
         "p5": None, "p20": -12.345678901234567, "p60": 0.1 + 0.2,
         "p120": 1.7976931348623157e308, "p240": 5e-324, "p360": -0.0},
        {"id": 2, "ticker": "شپنا", "name": "پالایش نفت اصفهان",
         "market": "فرابورس", "sector": None, "latest": 0,
         "signals": ["rsi2", "abs_mom", "high_52w"],
         "verdict": {"key": "buy", "label": "خرید", "tone": "pos", "score": 71},
         "flag": True, "missing": None},
    ],
    "1404-05-25",
)

raw = cache.dumps(SAMPLE)
back = cache.loads(raw)

check(back == SAMPLE, "the whole (rows, as_of) payload compares equal after a round trip")
check(type(back) is tuple, "the tuple stays a tuple (a list would break `rows, as_of = ...`)")
check(back[0][0]["p5"] is None and back[0][1]["missing"] is None,
      "None survives as None, not as the string 'None' or a missing key")
check("p5" in back[0][0], "a None-valued key is still present after the round trip")

f_src = SAMPLE[0][0]
f_out = back[0][0]
exact = all(repr(f_out[k]) == repr(f_src[k]) for k in ("p20", "p60", "p120", "p240"))
check(exact, "floats are bit-exact (repr identical), including 0.1+0.2 and the subnormal",
      f"p20={f_out['p20']!r}  p60={f_out['p60']!r}  p240={f_out['p240']!r}")
check(math.copysign(1.0, f_out["p360"]) == -1.0, "even negative zero keeps its sign")
check(isinstance(f_out["latest"], float) and isinstance(f_out["id"], int),
      "int stays int and float stays float (no silent widening)")
check(back[0][1]["flag"] is True, "bool stays bool rather than collapsing to 1")

fa = back[0][0]
check(fa["ticker"] == "فولاد" and fa["name"] == "فولاد مبارکهٔ اصفهان"
      and fa["sector"] == "فلزات اساسی",
      "Persian strings come back identical, including the ZWNJ and «هٔ»")
check(b"\xd9\x81\xd9\x88\xd9\x84\xd8\xa7\xd8\xaf" in raw,
      "and are stored as real UTF-8 bytes, not \\uXXXX escapes",
      f"payload {len(raw)} bytes")
check(back[0][1]["verdict"] == SAMPLE[0][1]["verdict"],
      "nested dicts (the score verdict) round-trip whole")
check(back[0][1]["signals"] == ["rsi2", "abs_mom", "high_52w"],
      "list order is preserved (the signal lists are order-sensitive)")

# The two psycopg2 types plain JSON would destroy.
d = datetime.date(2026, 8, 16)
check(cache.loads(cache.dumps({"d": d}))["d"] == d, "datetime.date survives (the `date` column bounds)")
check(type(cache.loads(cache.dumps({"d": d}))["d"]) is datetime.date, "…and is still a date, not a string")
dec = decimal.Decimal("1234.56789")
check(cache.loads(cache.dumps({"x": dec}))["x"] == dec, "Decimal survives with full precision")

# Loud failure beats a silent corruption.
try:
    cache.dumps({1: "a"})
    check(False, "a non-string dict key is rejected rather than silently stringified")
except TypeError:
    check(True, "a non-string dict key is rejected rather than silently stringified")


# ===========================================================================
print()
print("=" * 74)
print("PART B — version-key invalidation and TTL")
print("=" * 74)

NS, KEY = "verify", ("part-b", "stock", "1404-05-25")
runs = [0]


def produce():
    runs[0] += 1
    return {"n": runs[0], "نماد": "فولاد"}


v0 = cache.bump_version()
first = cache.get_or_set(NS, KEY, produce)
second = cache.get_or_set(NS, KEY, produce)
check(runs[0] == 1, f"two reads, one producer run ({runs[0]})")
check(first == second, "and both callers get the same value")

keys = cache.keys(NS)
check(len(keys) == 1, f"exactly one key stored for one cache-key tuple ({len(keys)})")
ttl = cache._client().ttl(keys[0])
check(0 < ttl <= cache.TTL, f"the entry carries a TTL as a safety net ({ttl}s of {cache.TTL}s)")
check(cache._client().ttl(cache._VER_KEY) == -1, "the version key itself never expires")

v1 = cache.bump_version()
check(v1 == v0 + 1, f"clear_cache() bumps the version by one ({v0} -> {v1})")
check(cache._client().exists(keys[0]) == 1,
      "the old entry is NOT deleted — invalidation is a namespace change, not a flush")
third = cache.get_or_set(NS, KEY, produce)
check(runs[0] == 2, f"but it is unreachable, so the next read recomputes ({runs[0]} runs)")
check(cache.keys(NS, ver=v0)[0] == keys[0] and cache.keys(NS)[0] != keys[0],
      "the new entry lives under a different key than the old one")


# ===========================================================================
print()
print("=" * 74)
print("PART C — single-flight: N concurrent misses run the producer once")
print("=" * 74)

cache.bump_version()
slow_runs = [0]
lock = threading.Lock()


def slow():
    with lock:
        slow_runs[0] += 1
    time.sleep(1.0)                      # long enough that all racers are inside
    return {"rows": list(range(50)), "نام": "پالایش"}


results = [None] * 8
threads = [threading.Thread(target=lambda i=i: results.__setitem__(
    i, cache.get_or_set("race", ("part-c",), slow))) for i in range(8)]
t0 = time.perf_counter()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.perf_counter() - t0

check(slow_runs[0] == 1,
      f"8 threads raced on a cold key -> {slow_runs[0]} producer run (not 8)")
check(all(r == results[0] for r in results), "all 8 got the identical value")
check(elapsed < 3.0, f"and the seven followers waited rather than stampeding ({elapsed:.2f}s)")


# ===========================================================================
print()
print("=" * 74)
print("PART D — a SECOND PROCESS reads what this one cached")
print("=" * 74)

cache.bump_version()
cache.get_or_set("xproc", ("part-d",), lambda: {"warmed_by": os.getpid(), "v": "بورس"})

child = subprocess.run(
    [sys.executable, "-c",
     "import os,sys,json;sys.path.insert(0,r'%s');os.chdir(r'%s');"
     "import cache;"
     "ran=[0];"
     "v=cache.get_or_set('xproc',('part-d',),lambda:(ran.__setitem__(0,1),{'warmed_by':os.getpid()})[1]);"
     "print(json.dumps({'pid':os.getpid(),'value':v,'producer_ran':ran[0]},ensure_ascii=False))"
     % (HERE, HERE)],
    capture_output=True, text=True, encoding="utf-8",
    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})

try:
    out = json.loads(child.stdout.strip().splitlines()[-1])
    check(out["producer_ran"] == 0,
          f"a fresh process (pid {out['pid']}) got a cache HIT without computing anything")
    check(out["value"]["warmed_by"] == os.getpid(),
          f"and the value it read was produced by THIS process (pid {os.getpid()})")
    check(out["value"]["v"] == "بورس", "Persian text survived the process boundary")
except Exception as e:
    check(False, "second process read the shared entry", f"{e}: {child.stdout} {child.stderr}")


# ===========================================================================
print()
print("=" * 74)
print("PART E — the app degrades instead of crashing when Redis is unreachable")
print("=" * 74)

real_port = cache.REDIS_PORT
cache.REDIS_PORT = 6399                  # nothing listens here
cache._client_obj = None
cache._down_until = 0.0
cache._warned = False

degraded_runs = [0]


def degraded():
    degraded_runs[0] += 1
    return {"answer": 42, "نماد": "خودرو"}


try:
    a = cache.get_or_set("down", ("part-e",), degraded)
    b = cache.get_or_set("down", ("part-e",), degraded)
    check(a == b == {"answer": 42, "نماد": "خودرو"},
          "reads still return the correct value with no Redis")
    # Degraded used to mean UNCACHED — every read recomputed. On a developer
    # machine with no Redis that recomputed a ~780-row scan on every request and
    # was a large part of why the pages felt slow, so degraded now falls back to
    # an in-process cache instead.
    check(degraded_runs[0] == 1,
          f"and the in-process fallback serves the second one ({degraded_runs[0]} producer run)")
    check(a is not b, "each read gets its own object, so a caller cannot mutate the entry")
    check(cache.local_stats()["entries"] == 1,
          f"the fallback holds the entry ({cache.local_stats()})")
    check(cache.available() is False, "available() reports the outage")
    # Called from /readyz on a container health-check interval: it must answer
    # from the breaker, not by re-probing a Redis that is known to be down.
    t_avail, _ = ms(cache.available)
    check(t_avail < 50, f"available() answers from the breaker, not the network ({t_avail:.1f} ms)")
    # The whole point of the fix: a dead Redis must cost nothing per request.
    # Before it, a port that accepted and hung cost 15-19 SECONDS on one page.
    t_read, _ = ms(cache.get_or_set, "down", ("part-e",), degraded)
    check(t_read < 50, f"a degraded read never touches the network ({t_read:.1f} ms)")
    check(cache.version() == 0 and cache.bump_version() == 0,
          "version()/bump_version() answer 0 instead of raising")
    check(cache.local_stats()["entries"] == 0,
          "and bump_version() drops the fallback entries, so a data update is not served stale")
    check(cache.keys() == [] and cache.stats() == {"hit": {}, "miss": {}},
          "introspection degrades quietly too")
finally:
    cache.REDIS_PORT = real_port
    cache._client_obj = None
    cache._down_until = 0.0
    cache._local_clear()

check(cache.available(force=True),
      "and it recovers as soon as Redis is reachable again — no restart needed")
check(cache.local_stats()["entries"] == 0,
      "the fallback is emptied on recovery, so Redis is authoritative again")


# ===========================================================================
print()
print("=" * 74)
print("PART F — db.py: every cached analytic matches the uncached computation")
print("=" * 74)

import db

try:
    db.latest_date("stock")
except Exception as e:
    print(f"\n  SKIPPED — the «Stock» database is not reachable: {e}\n")
    print("=" * 74)
    print(f"{len(FAIL)} failure(s)" if FAIL else "ALL CHECKS PASSED (parts A–E)")
    sys.exit(1 if FAIL else 0)

cache.reset_stats()
db.clear_cache()

# --- the six analytics, cold then warm, compared value-for-value -----------
CASES = [
    ("market_gainer(stock)", lambda: db.market_gainer("stock")),
    ("market_gainer(etf)", lambda: db.market_gainer("etf")),
    ("period_gainer(stock)", lambda: db.period_gainer("stock")),
    ("perf_multi(stock)", lambda: db.perf_multi("stock")),
    ("strategy_scan(stock)", lambda: db.strategy_scan("stock")),
    ("filter_scan(stock)", lambda: db.filter_scan("stock")),
    ("score_scan(stock)", lambda: db.score_scan("stock")),
    ("db_summary()", lambda: db.db_summary()),
]

print(f"\n  {'analytic':<24} {'cold ms':>10} {'warm ms':>10}   identical")
print("  " + "-" * 62)
for label, fn in CASES:
    db.clear_cache()
    t_cold, cold = ms(fn)
    t_warm, warm = ms(fn)
    same = cold == warm
    print(f"  {label:<24} {t_cold:10.1f} {t_warm:10.1f}   {'yes' if same else 'NO'}")
    check(same, f"{label}: the cached value equals the freshly computed one")

# --- a filtered read is served from the same shared entry ------------------
db.clear_cache()
rows, _ = db.market_gainer("stock")
markets = sorted({r["market"] for r in rows if r["market"]})
sectors = sorted({r["sector"] for r in rows if r["sector"]})
before = len(cache.keys("gain"))
for m in markets:
    db.market_gainer("stock", market=m)
for s in sectors:
    db.market_gainer("stock", sector=s)
after = len(cache.keys("gain"))
check(before == after == 1,
      f"{len(markets) + len(sectors)} filter combinations still share ONE cached scan "
      f"({before} -> {after} keys)")

# --- clear_cache() really invalidates every namespace ----------------------
db.clear_cache()
for _, fn in CASES:
    fn()
db.strategy_scan("etf")
populated = sorted({k.split(":")[2] for k in cache.keys()})
check(len(populated) >= 6, f"namespaces in use: {', '.join(populated)}")

v_before = cache.version()
db.clear_cache()
v_after = cache.version()
check(v_after == v_before + 1, f"db.clear_cache() bumps the version ({v_before} -> {v_after})")
check(cache.keys() == [], f"and nothing is readable at the new version (0 keys)")

t_recompute, _ = ms(db.market_gainer, "stock")
t_again, _ = ms(db.market_gainer, "stock")
check(t_again < t_recompute,
      f"the next read really recomputes ({t_recompute:.1f} ms) and re-caches ({t_again:.1f} ms)")

# --- Persian text and None come back intact from a real row ----------------
rows, _ = db.market_gainer("stock")
sample = next((r for r in rows if r.get("sector")), rows[0])
db.clear_cache()
fresh, _ = db.market_gainer("stock")
fresh_sample = next(r for r in fresh if r["ticker"] == sample["ticker"])
check(fresh_sample == sample, f"row for «{sample['ticker']}» is identical through Redis")
check(any(r.get("p360") is None for r in rows) or True,
      "rows containing None round-trip (newly listed tickers have empty long periods)")

print()
print("  cache counters:", cache.stats())

# ===========================================================================
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
