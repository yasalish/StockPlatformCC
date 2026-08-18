"""
verify_cache_degraded.py — the app must stay fast when Redis is NOT there.

Order 04 moved the analytics cache into Redis. Every developer machine here runs
without one, and that path had two defects that made the whole site feel broken:

  1. A Redis port that ACCEPTS and then never answers — which is what a local
     VPN/proxy client intercepting 127.0.0.1 does — cost redis-py three command
     timeouts. Measured: 15-19 seconds added to whichever page load happened to
     land after the retry window expired.
  2. Degraded meant UNCACHED, so every request recomputed the ~780-row
     multi-period scan behind /performance, /filters and /strategies.

This script proves both are fixed, and it needs NO Redis — it stands up a fake
one in-process (a socket that accepts and hangs, the worst case) so the failure
mode is reproduced rather than assumed.

Run:  python verify_cache_degraded.py
"""
import os
import socket
import sys
import threading
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


# --------------------------------------------------------------------------
# A black-hole server: accepts the TCP connection, then never sends a byte.
# A refusing (closed) port fails instantly and would not reproduce the bug.
# --------------------------------------------------------------------------
class BlackHole:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.held = []
        self.stop = False
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
                self.held.append(conn)          # hold it open, answer nothing
            except OSError:
                return

    def close(self):
        self.stop = True
        for c in self.held:
            try:
                c.close()
            except OSError:
                pass
        self.sock.close()


hole = BlackHole()
import cache

cache.REDIS_URL = ""
cache.REDIS_HOST = "127.0.0.1"
cache.REDIS_PORT = hole.port
cache.REDIS_PASSWORD = None
cache._client_obj = None
cache._down_until = 0.0
cache._warned = False
cache._local_clear()

print("=" * 74)
print(f"PART A — a Redis that accepts and hangs (127.0.0.1:{hole.port})")
print("=" * 74)

runs = [0]


def producer():
    runs[0] += 1
    return {"rows": [{"ticker": "فولاد", "gain": 12.5}], "n": runs[0]}


t_first, v1 = ms(cache.get_or_set, "verify", ("k1",), producer)
budget = (cache.CONNECT_TIMEOUT + cache.SOCKET_TIMEOUT) * 2 * 1000 + 500
check(t_first < budget,
      f"the FIRST read discovers the outage within its timeout budget "
      f"({t_first:.0f} ms, budget {budget:.0f} ms)")
check(t_first < 6000, f"and nowhere near the 15-19 s it used to cost ({t_first:.0f} ms)")
check(v1["rows"][0]["ticker"] == "فولاد", "the value is correct while degraded")

t_second, v2 = ms(cache.get_or_set, "verify", ("k1",), producer)
check(t_second < 50, f"the SECOND read never touches the network ({t_second:.1f} ms)")
check(runs[0] == 1, f"and is served by the in-process fallback ({runs[0]} producer run)")
check(v1 == v2 and v1 is not v2,
      "each read gets an equal but separate object (a caller cannot corrupt the entry)")

t_avail, up = ms(cache.available)
check(up is False and t_avail < 50,
      f"available() answers from the breaker for /readyz ({t_avail:.1f} ms)")

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("PART B — the retry NEVER lands on a request")
print("=" * 74)

cache.RETRY_AFTER = 0.5                      # so the window expires during the test
cache._down_until = time.time()              # pretend it just expired
worst = 0.0
for i in range(6):
    t, _ = ms(cache.get_or_set, "verify", ("k2",), producer)
    worst = max(worst, t)
    time.sleep(0.2)                          # cross at least one retry window
check(worst < 100,
      f"reads stay fast across the retry window — the probe is on a thread "
      f"({worst:.1f} ms worst of 6)")
check(any(t.name == "redis-probe" for t in threading.enumerate()) or cache._probing is False,
      "a background probe ran (or finished) without blocking any read")

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("PART C — the fallback is bounded, expiring and invalidated")
print("=" * 74)

cache._local_clear()
cache.get_or_set("verify", ("ttl",), producer)
check(cache.local_stats()["entries"] == 1, "an entry is held")
check(cache.local_stats()["bytes"] > 0, f"and accounted in bytes ({cache.local_stats()['bytes']})")

cache.bump_version()
check(cache.local_stats()["entries"] == 0,
      "clear_cache()/bump_version() drops it, so a data update is never served stale")

real_ttl = cache.LOCAL_TTL
try:
    cache.LOCAL_TTL = 1
    before = runs[0]
    cache.get_or_set("verify", ("exp",), producer)
    cache.get_or_set("verify", ("exp",), producer)
    check(runs[0] == before + 1, "a fresh entry is reused")
    time.sleep(1.2)
    cache.get_or_set("verify", ("exp",), producer)
    check(runs[0] == before + 2, "and expires on its TTL rather than living forever")
finally:
    cache.LOCAL_TTL = real_ttl

real_max = cache.LOCAL_MAX_BYTES
try:
    cache.LOCAL_MAX_BYTES = 4096
    cache._local_clear()
    for i in range(50):
        cache.get_or_set("verify", (f"big{i}",), lambda: {"pad": "x" * 500})
    st = cache.local_stats()
    check(st["bytes"] <= 4096, f"the fallback stays inside its byte budget ({st})")
    check(st["entries"] < 50, "evicting least-recently-used entries rather than growing")
finally:
    cache.LOCAL_MAX_BYTES = real_max
    cache._local_clear()

# --------------------------------------------------------------------------
print()
print("=" * 74)
print("PART D — the pages themselves (needs the «Stock» database)")
print("=" * 74)

try:
    import db
    db._one("SELECT 1")
except Exception as e:
    print(f"  SKIP  database unreachable ({str(e)[:60]})")
else:
    import app as A
    uid = db._one("SELECT id FROM users ORDER BY id LIMIT 1")["id"]
    c = A.app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    for url, limit in (("/performance", 1200), ("/filters", 800),
                       ("/strategies", 900), ("/stocks", 400)):
        c.get(url)                                     # warm the fallback
        t, r = ms(c.get, url)
        check(r.status_code == 200 and t < limit,
              f"{url} renders in {t:.0f} ms with no Redis (limit {limit} ms)")

hole.close()
print()
print("=" * 74)
print(("FAILED: " + ", ".join(FAIL)) if FAIL else "ALL CHECKS PASSED")
print("=" * 74)
sys.exit(1 if FAIL else 0)
