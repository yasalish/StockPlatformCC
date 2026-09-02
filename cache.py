"""
cache.py — حافظهٔ نهان تحلیل‌ها روی Redis
The shared analytics cache for «بورس‌نگار».

It replaces the five module-level dicts db.py used to keep (_CACHE,
_STRAT_CACHE, _FILTER_CACHE, _SCORE_CACHE, _MKT_CACHE). Those were plain dicts:
unbounded, no TTL, no eviction, and — the reason this module exists — PRIVATE TO
ONE PROCESS. Under Gunicorn with N workers they became N independent copies in
RAM and N simultaneous cold computations of the same scan, which is why the app
could not safely serve more than one worker.

Four properties this gives instead:

  1. One copy, shared. Every worker reads the same Redis key, so a scan computed
     by worker 1 is instantly a hit in workers 2..N.

  2. Atomic invalidation, via a version key. Keys are namespaced with a
     monotonically increasing integer held in Redis; clear_cache() INCRs it
     instead of deleting anything. The instant that INCR lands, EVERY worker is
     computing new key names, so no worker can serve a stale row — there is no
     window in which one process has flushed and another has not. The orphaned
     old-version keys are never read again and expire on their own.

  3. A TTL as a safety net. If an invalidation is ever missed (a crash between
     the data update and the bump), entries self-heal within ANALYTICS_CACHE_TTL
     rather than being wrong until the next restart. maxmemory-policy allkeys-lru
     on the server side bounds the memory too.

  4. Single-flight. On a miss, the first worker takes a short Redis lock and
     computes; the others wait for the value to appear instead of stampeding the
     database with N copies of the same expensive scan.

If Redis is unreachable the app does NOT fail: a warning is logged once, reads
are served by a small in-process fallback cache (see below), and the client is
retried periodically so a Redis that comes back is picked up without a restart.

Two properties of that degraded mode matter as much as the fast path, because a
developer machine usually has no Redis at all:

  5. The retry NEVER happens on a request. A dead port that refuses instantly is
     harmless, but a port that ACCEPTS and then hangs — a local VPN/proxy client
     intercepting 127.0.0.1 does exactly that — costs socket_timeout per command
     plus redis-py's own retries, which measured 15-19 SECONDS on one page load
     here. So the client is built with retries off and short timeouts, and once
     the breaker is open the probe that decides whether Redis came back runs on a
     background thread. The request path only ever sees "degraded → None".

  6. Degraded is still CACHED, in-process. Falling straight through to the
     producer meant recomputing a ~780-row multi-period scan on every single
     request, which is what made /performance and /filters feel slow with no
     Redis running. _local_* below is the fallback: bounded in bytes, TTL'd, and
     storing the same serialised bytes Redis would have stored (so callers get a
     fresh object per read and cannot mutate a shared one). It is per process —
     the very thing Redis exists to avoid — which is why it is used ONLY while
     Redis is unreachable and carries a much shorter TTL.
"""
import hashlib
import json
import os
import threading
import time
import datetime
import decimal
from collections import OrderedDict

try:                       # Settings below are read at import time, so .env has
    from dotenv import load_dotenv   # to be loaded before them — whichever
    load_dotenv()                    # module happens to import this one first.
except ImportError:
    pass

try:
    import redis as _redis
    from redis.exceptions import RedisError
    try:                                  # redis-py ≥ 4.2 — retry policy objects
        from redis.retry import Retry
        from redis.backoff import NoBackoff
    except ImportError:                   # older redis-py: no policy to pass
        Retry = NoBackoff = None
except ImportError:                       # redis-py not installed → always degraded
    _redis = None
    Retry = NoBackoff = None

    class RedisError(Exception):
        pass


# ---------------------------------------------------------------------------
# Configuration — .env, next to the STOCK_DB_* variables
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL") or ""
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_PASSWORD = os.environ.get("REDIS_PASSWORD") or None

# Key namespace. Bumping this by hand is a hard reset of everything.
PREFIX = os.environ.get("REDIS_PREFIX", "bn")

# Safety-net expiry. The prices change once a day, so six hours is short enough
# that a missed invalidation heals the same session and long enough that the
# cache is still warm across a working day.
TTL = int(os.environ.get("ANALYTICS_CACHE_TTL", "21600"))

# Single-flight. LOCK_TTL bounds how long a dead worker can block the others;
# LOCK_WAIT bounds how long a follower waits before giving up and computing it
# itself. LOCK_TTL must comfortably exceed the slowest producer (a cold
# _filter_scan_full against a database with no views takes tens of seconds).
LOCK_TTL = int(os.environ.get("ANALYTICS_CACHE_LOCK_TTL", "180"))
LOCK_WAIT = float(os.environ.get("ANALYTICS_CACHE_LOCK_WAIT", "60"))
_LOCK_POLL = 0.02                         # 20 ms between follower polls

# How long to stay in the degraded (no-Redis) state before probing again. The
# probe runs on a background thread, so this is not a latency the user can feel;
# it only bounds how quickly a Redis that comes back is noticed.
RETRY_AFTER = float(os.environ.get("REDIS_RETRY_AFTER", "20"))

# Socket budget. Both are deliberately small: a healthy local/compose Redis
# answers in microseconds, so anything approaching these numbers is a Redis that
# is effectively down and the app is better off degrading immediately than
# waiting. CONNECT covers a port that is filtered rather than refused; SOCKET
# covers a port that accepts and then never replies (the intercepting-proxy case
# in the module docstring), which is the one that used to cost seconds.
CONNECT_TIMEOUT = float(os.environ.get("REDIS_CONNECT_TIMEOUT", "0.5"))
SOCKET_TIMEOUT = float(os.environ.get("REDIS_SOCKET_TIMEOUT", "2.0"))

# In-process fallback cache, used ONLY while Redis is unreachable. Short TTL
# because it cannot be invalidated across processes: a data update bumps the
# version in Redis, and with Redis down there is nothing to bump, so these
# entries have to expire on their own. bump_version() still clears the local
# copy, which covers the single-process (python app.py) case exactly.
LOCAL_TTL = int(os.environ.get("ANALYTICS_LOCAL_TTL", "300"))
LOCAL_MAX_BYTES = int(os.environ.get("ANALYTICS_LOCAL_MAX_BYTES", str(96 * 1024 * 1024)))

# hit/miss counters in Redis — cheap, and the only way to prove across processes
# that an expensive scan really ran once instead of once per worker.
STATS = os.environ.get("REDIS_CACHE_STATS", "1").lower() not in ("0", "false", "no")

# RESP wire protocol. redis-py 6+ defaults to RESP3, which opens the connection
# with HELLO 3 — a command that only exists from Redis 6.0 onwards, so that
# default turns any older server (the 5.0.x Windows build used for local dev
# among them) into a hard connection failure. Nothing here needs RESP3: the
# cache uses GET/SET/INCR/SCAN, whose replies are identical either way. RESP2 is
# understood by every server, so it is the default; set REDIS_PROTOCOL=3 to opt
# in when the server is known to be 6+.
PROTOCOL = int(os.environ.get("REDIS_PROTOCOL", "2"))

_VER_KEY = f"{PREFIX}:analytics:version"

import observability
log = observability.get_logger("boursenegar.cache")


# ---------------------------------------------------------------------------
# Serialisation — JSON with type tags
#
# The cached values are row dicts, lists of them, and the odd (rows, as_of)
# tuple. Plain JSON would round-trip most of it but silently damage three things
# psycopg2 hands back: datetime.date (from the `date` column), Decimal (from any
# numeric aggregate) and tuples (which come back as lists). So every non-JSON
# type is wrapped in a tagged object and restored on the way out.
#
# None, floats and Persian text need no special handling and survive exactly:
# json writes floats with repr (shortest round-trip form), null is None, and
# ensure_ascii=False + utf-8 keeps «فولاد» a single readable string. test_cache.py
# asserts all of that rather than assuming it.
# ---------------------------------------------------------------------------
_TAG = "__bn__"


def _encode(o):
    """Python → JSON-safe structure. Raises TypeError on anything it cannot
    represent faithfully (including non-string dict keys, which JSON would
    stringify behind our back) so a bad value fails loudly here instead of
    coming back subtly wrong later."""
    if o is None or isinstance(o, (str, bool)):
        return o
    if isinstance(o, (int, float)):
        return o
    if isinstance(o, dict):
        out = {}
        for k, v in o.items():
            if not isinstance(k, str):
                raise TypeError(f"cache: non-string dict key {k!r} ({type(k).__name__})")
            out[k] = _encode(v)
        return out
    if isinstance(o, list):
        return [_encode(v) for v in o]
    if isinstance(o, tuple):
        return {_TAG: "tuple", "v": [_encode(v) for v in o]}
    if isinstance(o, set):
        return {_TAG: "set", "v": [_encode(v) for v in sorted(o, key=repr)]}
    if isinstance(o, datetime.datetime):
        return {_TAG: "datetime", "v": o.isoformat()}
    if isinstance(o, datetime.date):
        return {_TAG: "date", "v": o.isoformat()}
    if isinstance(o, decimal.Decimal):
        return {_TAG: "decimal", "v": str(o)}
    raise TypeError(f"cache: cannot serialise {type(o).__name__}")


def _hook(d):
    t = d.get(_TAG)
    if t is None:
        return d
    v = d["v"]
    if t == "tuple":
        return tuple(v)
    if t == "set":
        return set(v)
    if t == "date":
        return datetime.date.fromisoformat(v)
    if t == "datetime":
        return datetime.datetime.fromisoformat(v)
    if t == "decimal":
        return decimal.Decimal(v)
    return d


def dumps(value):
    return json.dumps(_encode(value), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def loads(raw):
    return json.loads(raw.decode("utf-8"), object_hook=_hook)


# ---------------------------------------------------------------------------
# Client — lazy, with a circuit breaker so a dead Redis costs one failed connect
# every RETRY_AFTER seconds instead of one per request.
# ---------------------------------------------------------------------------
_client_obj = None
_client_lock = threading.Lock()
_down_until = 0.0          # > 0 means degraded; the value is when to probe next
_probing = False           # a background probe is in flight
_warned = False
_bump_owed = False         # an invalidation was requested while Redis was down


def _build():
    kw = dict(socket_connect_timeout=CONNECT_TIMEOUT, socket_timeout=SOCKET_TIMEOUT,
              health_check_interval=30, decode_responses=False,
              protocol=PROTOCOL)
    # redis-py retries a timed-out command on its own (three attempts by
    # default in 6.x). Against a hung port that multiplies SOCKET_TIMEOUT by
    # three and is the difference between degrading in 2 seconds and blocking a
    # page load for 19. There is nothing to gain from retrying a cache read
    # anyway — the producer is right there.
    if Retry is not None:
        kw["retry"] = Retry(NoBackoff(), 0)
        kw["retry_on_timeout"] = False
    if REDIS_URL:
        return _redis.Redis.from_url(REDIS_URL, **kw)
    return _redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
                        password=REDIS_PASSWORD, **kw)


def _client():
    """The shared client, or None while degraded.

    Never blocks once the breaker is open: when the retry window expires this
    hands the probe to a background thread and still returns None, so no user
    request ever pays the cost of discovering that Redis is still down."""
    global _client_obj
    if _redis is None:
        return None
    if _down_until:
        if time.time() >= _down_until:
            _start_probe()
        return None
    if _client_obj is None:
        with _client_lock:
            if _client_obj is None:
                try:
                    _client_obj = _build()
                except Exception as e:      # bad URL / bad options
                    _down(e)
                    return None
    return _client_obj


def _start_probe():
    """Re-arm the retry window and ping Redis off the request path."""
    global _probing, _down_until
    with _client_lock:
        if _probing:
            return
        _probing = True
        _down_until = time.time() + RETRY_AFTER   # only one probe per window

    def run():
        global _probing, _down_until, _client_obj
        try:
            r = _client_obj
            if r is None:
                try:
                    r = _client_obj = _build()
                except Exception:
                    return
            r.ping()
        except Exception:
            return                                # still down; window stands
        else:
            _down_until = 0.0
            _up()
        finally:
            _probing = False

    threading.Thread(target=run, name="redis-probe", daemon=True).start()


def _down(err):
    """Enter the degraded state. Warns once per outage, not once per request."""
    global _down_until, _warned
    _down_until = time.time() + RETRY_AFTER
    if not _warned:
        _warned = True
        log.warning("redis unavailable — serving analytics from the in-process "
                    "fallback cache until it returns", extra={"error": str(err)})


def _up():
    global _warned
    if _warned:
        _warned = False
        _local_clear()          # Redis is authoritative again; drop the stand-in
        log.info("redis is back — analytics cache re-enabled")
    _flush_owed_bump()


def _flush_owed_bump():
    """Apply an invalidation that was requested while Redis was unreachable.

    bump_version() can only drop this process's fallback entries when there is
    no Redis to INCR — but every entry written to Redis BEFORE the outage is
    still sitting there under the unchanged version. Without this, the moment
    Redis comes back the app starts serving pre-invalidation numbers again, and
    keeps doing so for the rest of the TTL. Deleting price rows while Redis is
    down and then restarting is exactly that shape: the delete is real, the
    invalidation is lost, and «آخرین تاریخ» reverts to the deleted date.

    Left owed if the INCR itself fails; the next _up() retries it."""
    global _bump_owed
    if not _bump_owed:
        return
    r = _client_obj                 # deliberately not _client(): no breaker games
    if r is None:
        return
    try:
        v = int(r.incr(_VER_KEY))
    except (RedisError, OSError):
        return
    _bump_owed = False
    log.info("applied the cache invalidation owed from the outage",
             extra={"cache_version": v})


def available(force=False):
    """True if a PING succeeds right now.

    While the breaker is open this answers False immediately instead of probing,
    because it is called from /readyz — which is polled every few seconds by the
    container health check, and must not block on a dead Redis. Pass force=True
    for a real probe (startup, verification scripts)."""
    global _down_until
    if _redis is None:
        return False
    if force:
        _down_until = 0.0
    r = _client()
    if r is None:
        return False
    try:
        r.ping()
        _up()
        return True
    except (RedisError, OSError) as e:
        _down(e)
        return False


def describe():
    """One line for the startup log."""
    if _redis is None:
        return "redis-py not installed — analytics cache disabled"
    where = REDIS_URL or f"{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    return f"{where} (ttl={TTL}s, prefix={PREFIX})"


# ---------------------------------------------------------------------------
# Version key — the invalidation mechanism
# ---------------------------------------------------------------------------
def version():
    """Current analytics version. 0 means "no Redis", which callers treat as a
    permanent miss rather than an error."""
    r = _client()
    if r is None:
        return 0
    try:
        v = r.get(_VER_KEY)
        if v is None:
            # SETNX so two workers racing on a cold Redis agree on 1.
            r.set(_VER_KEY, 1, nx=True)
            v = r.get(_VER_KEY) or b"1"
        return int(v)
    except (RedisError, OSError, ValueError) as e:
        _down(e)
        return 0


def bump_version():
    """Invalidate everything, atomically for every worker. Returns the new
    version, or 0 while degraded (where there is no shared version to bump — but
    the in-process fallback entries are dropped, which is the whole cache when
    this process is the only one).

    A degraded bump is REMEMBERED, not discarded: entries written to Redis
    before the outage would otherwise become live again the moment it returns.
    _flush_owed_bump() applies it as soon as there is a client to apply it
    with."""
    global _bump_owed
    _local_clear()
    r = _client()
    if r is None:
        _bump_owed = True
        return 0
    try:
        v = int(r.incr(_VER_KEY))
        _bump_owed = False
        log.info("analytics cache invalidated", extra={"cache_version": v})
        return v
    except (RedisError, OSError) as e:
        _bump_owed = True
        _down(e)
        return 0


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def _key(ver, namespace, parts):
    """bn:v<version>:<namespace>:<digest of the call-site key tuple>.

    The tuples are hashed rather than pasted in because several of them carry
    Persian sector names and user-supplied Jalali dates; a SHA-1 digest keeps
    every key the same bounded length and free of separators that could collide.
    It is content-addressed, so all workers derive the identical key — unlike
    hash(), which is randomised per process."""
    canon = json.dumps(_encode(list(parts)), ensure_ascii=False,
                       separators=(",", ":"))
    return f"{PREFIX}:v{ver}:{namespace}:{hashlib.sha1(canon.encode('utf-8')).hexdigest()[:20]}"


# ---------------------------------------------------------------------------
# In-process fallback — the stand-in while Redis is unreachable
#
# Deliberately the smallest thing that fixes the problem: an OrderedDict used as
# an LRU, holding the SERIALISED bytes (so a caller that mutates a returned row
# cannot corrupt the entry, exactly as with Redis), bounded by total bytes, and
# expiring on a short TTL. No background eviction thread — entries are checked
# on read and the LRU tail is dropped on write.
# ---------------------------------------------------------------------------
_local = OrderedDict()                    # key -> (expires_at, raw bytes)
_local_bytes = 0
_local_lock = threading.Lock()


def _local_get(key):
    with _local_lock:
        hit = _local.get(key)
        if hit is None:
            return None
        expires, raw = hit
        if expires < time.time():
            _local_drop(key)
            return None
        _local.move_to_end(key)           # LRU: keep the freshly-read entry
        return raw


def _local_drop(key):
    """Remove one entry. Caller holds _local_lock."""
    global _local_bytes
    hit = _local.pop(key, None)
    if hit is not None:
        _local_bytes -= len(hit[1])


def _local_set(key, raw, ttl):
    global _local_bytes
    if len(raw) > LOCAL_MAX_BYTES:        # one value bigger than the whole budget
        return
    with _local_lock:
        _local_drop(key)
        _local[key] = (time.time() + ttl, raw)
        _local_bytes += len(raw)
        while _local_bytes > LOCAL_MAX_BYTES and _local:
            _local_drop(next(iter(_local)))     # evict least-recently-used


def _local_clear():
    global _local_bytes
    with _local_lock:
        _local.clear()
        _local_bytes = 0


def local_stats():
    """{'entries': n, 'bytes': n} — what the fallback is holding. Observational,
    used by /readyz and the verification scripts."""
    with _local_lock:
        return {"entries": len(_local), "bytes": _local_bytes}


def _degraded_get_or_set(namespace, key_parts, producer, ttl=None):
    """get_or_set() with no Redis: same contract, process-local storage."""
    key = _key(0, namespace, key_parts)   # v0 = the degraded namespace
    raw = _local_get(key)
    if raw is not None:
        try:
            return loads(raw)
        except Exception:                 # unreadable → recompute over it
            with _local_lock:
                _local_drop(key)
    value = producer()
    try:
        _local_set(key, dumps(value), min(ttl or TTL, LOCAL_TTL))
    except TypeError as e:                # unserialisable → correct but uncached
        log.error("value is not cacheable",
                  extra={"namespace": namespace, "error": str(e)})
    return value


# ---------------------------------------------------------------------------
# The one helper every call site uses
# ---------------------------------------------------------------------------
def get_or_set(namespace, key_parts, producer, ttl=None):
    """Return the cached value for (namespace, key_parts), computing it with
    producer() on a miss.

    `namespace` separates the call sites that used to have a dict each — several
    of them key on the identical (kind, as_of) tuple, which would collide now
    that they share one keyspace.

    Degrades to the in-process fallback cache whenever Redis is unreachable, and
    never lets a cache problem escape: a serialisation failure is logged and the
    value is returned uncached."""
    r = _client()
    if r is None:
        return _degraded_get_or_set(namespace, key_parts, producer, ttl)

    try:
        ver = version()
        if not ver:
            return _degraded_get_or_set(namespace, key_parts, producer, ttl)
        key = _key(ver, namespace, key_parts)
        raw = r.get(key)
    except (RedisError, OSError) as e:
        _down(e)
        return _degraded_get_or_set(namespace, key_parts, producer, ttl)

    if raw is not None:
        try:
            value = loads(raw)
            _count(r, "hit", namespace)
            return value
        except Exception as e:              # corrupt entry → recompute over it
            log.warning("discarding unreadable cache entry",
                        extra={"cache_key": key, "error": str(e)})

    # --- miss: single-flight so N workers do not all run the same scan --------
    lock = key + ":lock"
    holder = False
    try:
        holder = bool(r.set(lock, b"1", nx=True, ex=LOCK_TTL))
    except (RedisError, OSError) as e:
        _down(e)
        return _degraded_get_or_set(namespace, key_parts, producer, ttl)

    if not holder:
        raw = _wait_for(r, key)
        if raw is not None:
            try:
                value = loads(raw)
                _count(r, "hit", namespace)
                return value
            except Exception:
                pass                        # fall through and compute it here
        # The leader died or is slower than LOCK_WAIT — compute rather than hang.

    try:
        _count(r, "miss", namespace)
        value = producer()
        try:
            r.set(key, dumps(value), ex=ttl or TTL)
        except (RedisError, OSError) as e:
            _down(e)
        except TypeError as e:              # unserialisable → correct but uncached
            log.error("value is not cacheable",
                      extra={"namespace": namespace, "error": str(e)})
        return value
    finally:
        if holder:
            try:
                r.delete(lock)
            except (RedisError, OSError):
                pass


def _wait_for(r, key):
    """Poll for the value another worker is computing. Returns the raw bytes, or
    None if it did not appear within LOCK_WAIT."""
    deadline = time.time() + LOCK_WAIT
    while time.time() < deadline:
        time.sleep(_LOCK_POLL)
        try:
            raw = r.get(key)
        except (RedisError, OSError):
            return None
        if raw is not None:
            return raw
    return None


def _count(r, what, namespace):
    if not STATS:
        return
    try:
        r.incr(f"{PREFIX}:stats:{what}:{namespace}")
    except (RedisError, OSError):
        pass


# ---------------------------------------------------------------------------
# Introspection — used by the verification scripts and the /health view
# ---------------------------------------------------------------------------
def keys(namespace=None, ver=None):
    """Live cache keys at the current version, optionally one namespace."""
    r = _client()
    if r is None:
        return []
    if ver is None:
        ver = version()
    pat = f"{PREFIX}:v{ver}:{namespace or '*'}:*"
    try:
        return [k.decode("utf-8") for k in r.scan_iter(match=pat, count=500)
                if not k.endswith(b":lock")]
    except (RedisError, OSError) as e:
        _down(e)
        return []


def stats():
    """{'hit': {...}, 'miss': {...}} per namespace, since the counters were last
    reset. Purely observational."""
    r = _client()
    out = {"hit": {}, "miss": {}}
    if r is None:
        return out
    try:
        for k in r.scan_iter(match=f"{PREFIX}:stats:*", count=200):
            _, _, what, ns = k.decode("utf-8").split(":", 3)
            out.setdefault(what, {})[ns] = int(r.get(k) or 0)
    except (RedisError, OSError) as e:
        _down(e)
    return out


def reset_stats():
    r = _client()
    if r is None:
        return
    try:
        for k in r.scan_iter(match=f"{PREFIX}:stats:*", count=200):
            r.delete(k)
    except (RedisError, OSError):
        pass


# ---------------------------------------------------------------------------
# One analytics rebuild at a time — across processes
#
# db.refresh_analytics() walks twenty materialized views and takes minutes. Three
# different things ask for it (the tail of an update, a manual row delete, and
# the in-process fallback in market.py), they run in different processes, and
# none of them could see that another was already doing it. A REFRESH
# MATERIALIZED VIEW CONCURRENTLY that arrives during another one is not wrong —
# PostgreSQL serialises them — it is simply another several minutes of work to
# produce identical rows, during which the update page has nothing to show.
#
# The worst case was a redelivered task: tasks.refresh_analytics_only ran with
# acks_late, so a worker killed mid-rebuild left the message unacked, kombu
# handed it back on the next worker boot, and the rebuild started again from the
# beginning — every time the app was restarted, blocking the fetch queue for six
# minutes each time. One entry was found doing exactly that.
#
# A plain SET NX with a TTL is the right lock here: whoever gets it rebuilds,
# everyone else skips, and a process killed while holding it blocks nothing for
# longer than the TTL. There is no correctness risk in a lost lock — the worst
# outcome is a duplicate rebuild, which is what this avoids rather than what it
# guards against.
# ---------------------------------------------------------------------------
REFRESH_LOCK_TTL = int(os.environ.get("ANALYTICS_REFRESH_LOCK_TTL", "1800"))


def _refresh_lock_key():
    return f"{PREFIX}:analytics:refreshing"


def claim_refresh(ttl=None, owner="?"):
    """True if the caller may start an analytics rebuild.

    False means another process is already doing it. With no Redis it always
    returns True: there is nothing to coordinate through, and skipping the
    rebuild on a machine with no cache would leave the views stale for ever —
    a much worse failure than doing the work twice.
    """
    r = _client()
    if r is None:
        return True
    try:
        return bool(r.set(_refresh_lock_key(), str(owner).encode("utf-8")[:200],
                          nx=True, ex=int(ttl or REFRESH_LOCK_TTL)))
    except (RedisError, OSError) as e:
        _down(e)
        return True


def release_refresh():
    """Drop the lock. Safe to call when it was never held."""
    r = _client()
    if r is None:
        return
    try:
        r.delete(_refresh_lock_key())
    except (RedisError, OSError):
        pass


def refresh_in_progress():
    """True while some process holds the rebuild lock. Observational only —
    used by /update/status so the page can say what is happening."""
    r = _client()
    if r is None:
        return False
    try:
        return bool(r.exists(_refresh_lock_key()))
    except (RedisError, OSError):
        return False


# ---------------------------------------------------------------------------
# Per-account throttling and host-wide slots
# ---------------------------------------------------------------------------
# Two primitives the web tier needs and could not express before, both of which
# have to be shared across processes to mean anything:
#
#   throttle_*  — a failure counter with exponential backoff, keyed on whatever
#                 the caller chooses. Used by the login form, keyed on the
#                 ACCOUNT rather than the IP: nginx's per-IP limiter is the
#                 wrong tool here, because Iranian mobile carriers put very
#                 large populations behind one NAT address, so a per-IP limit is
#                 simultaneously too strict for real users at market open and
#                 useless against a distributed attempt.
#
#   claim_slot  — N host-wide slots, so a CPU-bound endpoint can be limited
#                 across every worker in every replica. A threading.Semaphore
#                 only ever limited one process: with 4 Gunicorn workers a
#                 "2 slots" guard actually permitted 8 concurrent runs.
#
# Both degrade deliberately when Redis is down, and each says how below.
# ---------------------------------------------------------------------------

def _throttle_keys(bucket, ident):
    """Namespaced keys for one throttled identity.

    The identity is hashed rather than interpolated: it comes from a form field,
    so it must not be able to shape a Redis key, and hashing also keeps the
    usernames people typed out of the keyspace.
    """
    h = hashlib.sha256(str(ident).encode("utf-8")).hexdigest()[:32]
    return (f"{PREFIX}:throttle:{bucket}:{h}:n",
            f"{PREFIX}:throttle:{bucket}:{h}:until")


def throttle_check(bucket, ident):
    """Seconds the caller must wait, or 0 when it may proceed.

    With no Redis this returns 0 — the request is allowed. That is the honest
    trade: refusing every login because the cache is down would turn a cache
    outage into a total outage, and the password check itself still stands.
    """
    r = _client()
    if r is None:
        return 0
    _, until_key = _throttle_keys(bucket, ident)
    try:
        ttl = r.ttl(until_key)
    except (RedisError, OSError) as e:
        _down(e)
        return 0
    # -2 = no such key, -1 = key with no expiry (should not happen; treat as
    # clear rather than as a permanent lockout).
    return int(ttl) if ttl and ttl > 0 else 0


def throttle_fail(bucket, ident, threshold=5, base=2, cap=900, window=3600):
    """Record one failure. Returns the seconds to wait before the next attempt.

    The first `threshold` failures cost nothing, so a person mistyping a
    password twice is not punished. After that the wait doubles per failure —
    2s, 4s, 8s … — capped at `cap`. The counter itself expires after `window`
    of quiet, so an account is never permanently poisoned by old failures.
    """
    r = _client()
    if r is None:
        return 0
    n_key, until_key = _throttle_keys(bucket, ident)
    try:
        pipe = r.pipeline()
        pipe.incr(n_key)
        pipe.expire(n_key, int(window))
        fails = int(pipe.execute()[0])
        over = fails - int(threshold)
        if over <= 0:
            return 0
        delay = min(int(cap), int(base) * (2 ** (over - 1)))
        r.set(until_key, b"1", ex=delay)
        return delay
    except (RedisError, OSError) as e:
        _down(e)
        return 0


def throttle_clear(bucket, ident):
    """Forget an identity's failures. Called on a successful login, so a user
    who eventually remembers their password starts clean."""
    r = _client()
    if r is None:
        return
    try:
        r.delete(*_throttle_keys(bucket, ident))
    except (RedisError, OSError):
        pass


SLOT_TTL = int(os.environ.get("RUN_SLOT_TTL", "180"))


def claim_slot(name, slots, ttl=None, owner="?"):
    """Take one of `slots` host-wide slots for `name`, or return None.

    Returns an opaque token to hand to release_slot(). Each slot is its own
    SET NX EX key, following claim_refresh() above: the TTL is what makes a
    slot self-healing, so a worker killed mid-run frees its slot without any
    cleanup path having to run.

    Returns None when Redis is unavailable — the CALLER then falls back to its
    in-process semaphore. Granting freely here would remove the protection
    exactly when the system is already unhealthy, and refusing everything would
    take a working feature offline over a cache outage; neither is right, and
    only the caller knows its local fallback.
    """
    r = _client()
    if r is None:
        return None
    ex = int(ttl or SLOT_TTL)
    for i in range(int(slots)):
        key = f"{PREFIX}:slot:{name}:{i}"
        try:
            if r.set(key, str(owner).encode("utf-8")[:200], nx=True, ex=ex):
                return key
        except (RedisError, OSError) as e:
            _down(e)
            return None
    return None


def release_slot(token):
    """Give a slot back. Safe with None, so callers can release unconditionally."""
    if not token:
        return
    r = _client()
    if r is None:
        return
    try:
        r.delete(token)
    except (RedisError, OSError):
        pass


# ---------------------------------------------------------------------------
# Per-user versioning (review finding H-2)
# ---------------------------------------------------------------------------
# The global version key above is deliberately coarse: bump_version() is for
# "the market data changed", and every worker recomputing every key is exactly
# what is wanted there. It is the wrong tool for per-user state, where one
# person saving a preference must not invalidate the other 99,999 people's
# bundles.
#
# So each user carries their own counter. It is INCRemented by db.py inside the
# functions that write user-scoped rows, and it forms part of the bundle's
# cache key — so a write makes the old key unreachable rather than needing a
# delete, which is the same trick the global version uses and is equally
# atomic across workers.
#
# With no Redis, user_version() returns 0 for everyone. That is correct rather
# than degraded: get_or_set falls back to the bounded local cache with a short
# TTL, and the bundle producer runs against PostgreSQL as it did before.

def user_version(user_id):
    """This user's state version. 0 when Redis is unavailable or never bumped."""
    r = _client()
    if r is None:
        return 0
    try:
        raw = r.get(f"{PREFIX}:uver:{int(user_id)}")
        return int(raw) if raw else 0
    except (RedisError, OSError, ValueError, TypeError) as e:
        if isinstance(e, (RedisError, OSError)):
            _down(e)
        return 0


def bump_user(user_id):
    """Invalidate one user's cached bundle. Called from every db.py writer that
    touches users / user_prefs / watchlist / alert_events.

    Silent on failure: a cache that cannot be invalidated is a correctness
    problem bounded by the bundle's TTL, and raising here would turn a Redis
    hiccup into a failed «ذخیره» the user can see."""
    r = _client()
    if r is None:
        return
    try:
        r.incr(f"{PREFIX}:uver:{int(user_id)}")
    except (RedisError, OSError):
        pass
