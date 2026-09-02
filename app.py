"""
بورس‌نگار (BourseNegar) — سامانهٔ یکپارچهٔ تحلیل بازار سرمایه
A single Persian (RTL) Flask platform over the Tehran Stock Exchange data that
replaces the collection of separate Streamlit scripts. It reads the same
PostgreSQL database «Stock» and reuses their analytics (see db.py) and their
updater logic (see market.py).

Run:
    pip install -r requirements.txt
    python app.py            # then open http://127.0.0.1:5002
"""
import io
import os
import secrets
import threading
import time

try:                                  # load .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import (
    Flask, render_template, request, jsonify, send_file, abort, redirect, url_for, flash, g,
)
from markupsafe import Markup, escape
from flask_login import current_user, login_required

import observability
import db
import cache
import prefs
import reports
import market
import filter_engine
import backtest as backtest_engine

# Structured JSON logging before anything else logs. Every module below uses
# logging.getLogger(); this is what turns those records into one-line JSON with
# a request id attached.
log = observability.setup_logging()

# `python app.py` now brings the whole local stack up with it — Redis and the
# Celery worker — because an app that serves every read screen but cannot run a
# data update is not "started". __name__ is "__main__" from this line onwards
# ONLY when this file was run as a script: Gunicorn and the Celery worker import
# it as the `app` module, and there both are declared services
# (docker-compose.yml) that a web worker has no business starting. It runs here,
# above db/cache/jobs startup, because the cache probe further down is the first
# thing that touches Redis. See dev_boot.py — it never raises, so a machine
# without redis-server starts degraded exactly as before.
if __name__ == "__main__":
    import dev_boot
    dev_boot.start_services()

from tv import tv as tv_blueprint
from auth import auth_bp, login_manager, init_oauth
from account import account_bp

# APP_ENV=production tightens the two things that are merely inconvenient in
# development but broken in production: a missing session secret, and trusting
# the reverse proxy's forwarded headers. docker-compose.yml sets it.
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
# Static assets are cache-busted by asset_version() (?v=<mtime>) in the
# templates, so they can be cached hard. _cache_policy() below is what actually
# stamps the header; this keeps Flask's own send_file default consistent with it
# instead of the old 0 (= no-cache) that contradicted it.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000
# Session signing key. A random fallback was survivable when this ran as one
# process: everyone got logged out on restart and that was the end of it. Under
# Gunicorn it is worse than that — each of the N workers would generate its OWN
# random key, so a session cookie signed by worker 1 fails validation on worker
# 2 and users are bounced to the login page at random. In production the
# variable is therefore required, not defaulted.
_secret = os.environ.get("STOCK_SECRET")
if not _secret:
    if IS_PRODUCTION:
        raise RuntimeError(
            "STOCK_SECRET is not set — refusing to start in production.\n"
            "  It signs session cookies, and every Gunicorn worker must use the "
            "SAME value or logins break at random.\n"
            "  Generate one with:  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
            "  Then set it in deploy/.env (it must stay constant across restarts)."
        )
    _secret = secrets.token_hex(32)
    log.warning("STOCK_SECRET unset — using a throwaway random key; sessions "
                "will not survive a restart and this is unsafe with >1 worker")
app.secret_key = _secret
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Send the session cookie only over HTTPS when behind TLS (set COOKIE_SECURE=1).
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "").lower() in ("1", "true", "yes")

# Behind nginx, the request Flask sees arrives over plain HTTP on a private
# network — so without this every url_for(_external=True) would emit http://,
# the Google OAuth redirect URI would not match, and request.is_secure would be
# False even though the user is on TLS. ProxyFix rewrites scheme / host / client
# IP from the X-Forwarded-* headers nginx sets. It is opt-in (TRUST_PROXY=1,
# which docker-compose.yml sets) because trusting those headers when the app is
# directly reachable would let a client forge its own IP and scheme.
if os.environ.get("TRUST_PROXY", "1" if IS_PRODUCTION else "").lower() in ("1", "true", "yes"):
    from werkzeug.middleware.proxy_fix import ProxyFix
    # One hop: exactly one proxy (nginx) sits in front. Counting more would let a
    # client prepend its own X-Forwarded-For entry and be believed.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Authentication: login manager + /login, /register, /logout (+ optional Google).
login_manager.init_app(app)
init_oauth(app)
app.register_blueprint(auth_bp)
# /api/me/prefs and /api/me/screens — the signed-in user's own record. Kept out
# of app.py because those routes assume `current_user` on every line, and out of
# auth.py because that blueprint is reachable unauthenticated by design.
app.register_blueprint(account_bp)
db.init_db()                          # ensure the `users` / `user_prefs` tables exist

app.register_blueprint(tv_blueprint)  # TradingView UDF datafeed (see tv.py)


# The background cache-warming thread that used to live here is gone. The market
# analytics are materialized views now — one shared database artifact instead of
# a private copy warmed into every worker's RAM — so there is nothing to warm.
# ensure_indexes() still runs (the single-ticker detail/chart lookups need it);
# it is a no-op once the indexes exist.
db.ensure_indexes()

# The update_job / update_job_ticker tables are the job control plane since
# order 06 — they replaced update_stop.flag and update_job.meta.json, which only
# worked when one process owned the run. Idempotent; the Celery worker calls it
# too, so whichever starts first creates them.
try:
    import jobs
    jobs.ensure_tables()
except Exception as _e:
    log.error("could not ensure update_job tables", exc_info=True)

# «طراحی فیلتر» — the custom_filters table, for the same reason and on the same
# terms as the two above: a developer laptop that has never seen Alembic must
# still be able to save a designed filter.
try:
    filter_engine.ensure_tables()
except Exception as _e:
    log.error("could not ensure custom_filters table", exc_info=True)

# شاخص‌ها / حقیقی و حقوقی / دیده‌بان / دلار — the five tables market_data.py owns.
# Same terms as the three above: idempotent, called from the worker too, and a
# laptop that has never run Alembic must still be able to open «شاخص‌ها».
try:
    import market_data
    market_data.ensure_tables()
except Exception as _e:
    log.error("could not ensure market-data tables", exc_info=True)

# The analytics cache is in Redis (cache.py) so all Gunicorn workers share one
# copy and one invalidation. Probe it once at startup purely so the operator sees
# which mode this process is in; an unreachable Redis is NOT fatal — reads fall
# back to cache.py's in-process cache and it retries in the background.
#
# The probe runs on a thread because it is diagnostics, not a dependency: a Redis
# host that hangs rather than refuses (an intercepting local proxy, a firewalled
# port) made this line delay startup by seconds for a message nobody waits for.
def _probe_cache():
    if cache.available(force=True):
        log.info("analytics cache ready",
                 extra={"backend": cache.describe(), "cache_version": cache.version()})
    else:
        log.warning("analytics cache DEGRADED — no Redis; serving from the "
                    "in-process fallback cache (correct, per worker, short TTL)",
                    extra={"backend": cache.describe()})


threading.Thread(target=_probe_cache, name="cache-probe", daemon=True).start()

if not db.analytics_ready():
    # Migration-safe: the app starts and serves correctly without the views, it
    # just computes the old (slow) way until they are built. Building them here
    # would block startup for minutes, so it is left to the next data update —
    # or to `python -c "import db; db.ensure_analytics_views()"`.
    log.warning("analytics views absent — falling back to live computation; "
                "build them with `alembic upgrade head`")


# Request ids, the slow-request warning and the response X-Request-ID header.
# This replaced a before/after pair that printed
#     [perf] GET /stocks -> 200 4231ms
# to stdout. Same 200 ms threshold (SLOW_REQUEST_MS), but emitted as a
# warning-level JSON event carrying the request id, so a slow page can be
# correlated with the database and cache lines logged while serving it.
observability.init_app(app)

# Error tracking. A no-op unless SENTRY_DSN is set — see observability.py on
# why a self-hosted Sentry or GlitchTip is the right target from Iran.
if observability.setup_sentry("web"):
    log.info("sentry enabled", extra={"env": APP_ENV})


@app.route("/healthz")
def healthz():
    """LIVENESS — is this worker able to serve at all?

    Deliberately dependency-free: it must not touch PostgreSQL or Redis. A
    liveness probe that fails when the database blinks would have Docker kill
    and restart every worker during a database hiccup, turning a brief outage
    into a restart loop. Use /readyz to ask whether the dependencies are up."""
    return jsonify({"status": "ok", "env": APP_ENV, "pid": os.getpid()}), 200


@app.route("/readyz")
def readyz():
    """READINESS — should this worker be sent traffic?

    PostgreSQL is the only hard dependency; without it no page can render, so
    its failure is a 503. Redis and the materialized views are reported but
    never fatal: the app is explicitly built to degrade to live queries when
    either is missing (orders 02 and 04), and failing readiness for a degraded
    but correct service would take the site down to no purpose."""
    out = {"database": "down", "redis": "down", "analytics_views": False}
    try:
        db._one("SELECT 1")
        out["database"] = "up"
    except Exception as e:
        out["error"] = str(e)[:200]
    try:
        # Non-forcing: while the breaker is open this answers instantly instead
        # of re-probing, so a health check polled every few seconds cannot be
        # what makes the process slow when Redis is down.
        out["redis"] = "up" if cache.available() else "down"
        out["cache_version"] = cache.version()
        if out["redis"] == "down":
            out["fallback_cache"] = cache.local_stats()
    except Exception:
        pass
    try:
        out["analytics_views"] = bool(db.analytics_ready())
    except Exception:
        pass
    healthy = out["database"] == "up"
    out["status"] = "ready" if healthy else "unavailable"
    return jsonify(out), (200 if healthy else 503)


@app.before_request
def _require_login():
    """Gate the whole platform behind a login. Auth pages and static assets stay
    open; API/export calls get a 401 JSON so the front-end can react, page loads
    redirect to the login screen with a `next` back-link."""
    endpoint = request.endpoint
    if endpoint is None or endpoint == "static" or endpoint.startswith("auth."):
        return
    # Container probes must answer before anyone can log in, or the orchestrator
    # would restart a perfectly healthy container forever.
    if endpoint in ("healthz", "readyz"):
        return
    if current_user.is_authenticated:
        # The data-update pages (downloads / deletes) are admin-only.
        if request.path.startswith("/update") and not getattr(current_user, "is_admin", False):
            if request.method == "POST" or request.path.startswith("/update/"):
                return jsonify({"error": "دسترسی مجاز نیست", "forbidden": True}), 403
            abort(403)
        return
    if request.path.startswith("/api/") or request.path.startswith("/export/") \
            or request.path.startswith("/tv/"):
        return jsonify({"error": "برای ادامه باید وارد شوید", "auth": True}), 401
    return redirect(url_for("auth.login", next=request.path))


def _require_admin():
    """Abort with 403 unless the current user is an admin. Used to fence off the
    data-update pages so only an admin can trigger downloads / deletes."""
    if not (current_user.is_authenticated and getattr(current_user, "is_admin", False)):
        abort(403)


def pill(val):
    """The «+۱۲.۳۴٪» / «−۵.۶۷٪» badge, as HTML.

    Byte-for-byte what {% include "_pill.html" %} produces — and the include is
    still there for one-off use — but built in Python because the wide tables
    call it tens of thousands of times per page. /performance renders 782 rows ×
    20 period columns: at ~16k includes, Jinja's per-include context creation was
    the single largest cost of the request (≈0.3 s of a 0.42 s render), which is
    pure overhead for six lines of markup with no logic in them."""
    if val is None:
        return Markup('<span class="muted">—</span>')
    cls, sign = ("up", "+") if val >= 0 else ("down", "−")
    return Markup(f'<span class="pill {cls}">{sign}'
                  f'{escape(db.to_persian(abs(val)))}٪</span>')


def spark(values, w=120, h=34, pad=2):
    """Turn a series of numbers into SVG path data for a sparkline.

    Returns a dict of ready-made `d` attributes — `line` for the stroke and
    `area` for the fill beneath it — or None when there is not enough data to
    draw. Callers render it with a plain <svg> and no JavaScript.

    Why this is done in Python rather than in a charting library: the dashboard
    draws one 250-point index chart and a handful of 60-point strips, all from
    values the request already has in memory. Every JS charting library that
    could draw them is 40-130 KB, needs a second paint after the HTML lands, and
    would have to be taught this app's fourteen themes and its «رنگ صعود و نزول»
    setting. A path string costs nothing, arrives with the markup, and inherits
    the theme through `stroke:var(--up)` like every other coloured thing here.

    The viewBox is normalised to w x h and the SVG is stretched by CSS, so the
    caller picks the aspect ratio and never has to match pixels.
    """
    pts = [float(v) for v in values if v is not None]
    if len(pts) < 2:
        return None
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    inner = h - 2 * pad
    n = len(pts)
    coords = [((w * i) / (n - 1), pad + inner - ((v - lo) / span) * inner)
              for i, v in enumerate(pts)]
    line = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in coords)
    return {
        "line": line,
        # Closed back along the baseline for the gradient fill.
        "area": f"{line} L{w:.1f} {h:.1f} L0 {h:.1f} Z",
        "w": w, "h": h,
        "dot_x": round(coords[-1][0], 1), "dot_y": round(coords[-1][1], 1),
        # Direction over the WHOLE window, which is what colours the line. Not
        # the last bar: a series that rose all period and dipped on the final
        # session is still a rising series, and coloring it red would say the
        # opposite of what the shape shows.
        "up": pts[-1] >= pts[0],
    }


@app.context_processor
def inject_helpers():
    def asset_version(filename):
        try:
            return int(os.path.getmtime(os.path.join(app.static_folder, filename)))
        except OSError:
            return 0
    import market_data
    return {"asset_version": asset_version, "fa": db.to_persian,
            "fy": db.to_persian_plain, "pill": pill, "PERIODS": db.PERIODS,
            "CALC_PERIODS": db.CALC_PERIODS,
            "PERF_PERIODS": db.PERF_PERIODS,
            "ETF_TYPE_COLORS": db.ETF_TYPE_COLORS,
            # «۱۲.۳ همت» / «۱.۲ میلیارد» — rials and counts at a magnitude a
            # reader can hold. Every new page prints money; none of them should
            # print 2309378183184.
            "money": market_data.rial_short,
            "cnt": market_data.count_short}


def _prefs_attrs(p):
    """The `data-*` attributes that carry the settings onto <html>.

    Rendered server-side ONLY for a signed-in user, and that asymmetry is the
    whole design. `data-prefs="server"` tells the pre-paint script in base.html
    that the decision is already made, so it leaves the attributes alone; for
    everyone else the attribute is absent and the script applies localStorage
    before the first frame. Without the marker, a signed-in user's theme would
    be overwritten on every page load by whatever another browser last wrote
    into this one's localStorage.
    """
    flags = {
        "data-prefs": "server",
        "data-theme": p["theme"],
        "data-density": p["density"],
        "data-font": p["font_scale"],
        "data-sbar": p["scrollbar_size"],
        "data-updown": p["updown_scheme"],
        "data-digits": p["digits"],
        "data-zebra": "on" if p["zebra"] else "off",
        "data-stickyhead": "on" if p["sticky_head"] else "off",
        "data-motion": "reduce" if p["reduce_motion"] else "full",
        "data-wide": "on" if p["wide"] else "off",
    }
    return Markup(" ".join(f'{k}="{escape(v)}"' for k, v in flags.items()))


@app.context_processor
def inject_prefs():
    """Every template gets `prefs` (the merged settings), `prefs_json` (the same
    values for the browser) and `prefs_attrs`.

    One cheap indexed lookup by primary key for a signed-in user; an anonymous
    visitor gets prefs.DEFAULTS with no query at all. It has to be free, because
    the theme is on every screen — a round trip here would tax every page in the
    app for a preference that changes once a month.
    """
    if current_user.is_authenticated:
        p = db.get_prefs(current_user.id)
        return {"prefs": p, "prefs_json": prefs.client_payload(p),
                "prefs_attrs": _prefs_attrs(p), "prefs_meta": prefs}
    p = prefs.payload({})
    return {"prefs": p, "prefs_json": prefs.client_payload(p),
            "prefs_attrs": Markup(""), "prefs_meta": prefs}


@app.context_processor
def inject_watchlist():
    """Expose the current user's watched symbols (as a set of "kind:ticker") and
    its count to every template, so stars render pre-filled and the nav badge
    shows a count. Cheap single query; only runs for logged-in users."""
    if not current_user.is_authenticated:
        return {"watched": set(), "watch_count": 0}
    keys = db.watch_keys(current_user.id)
    return {"watched": keys, "watch_count": len(keys)}


@app.context_processor
def inject_alerts():
    """The UNREAD هشدار count, for the nav badge.

    Unread rather than total: a badge means "there is something here you have
    not seen", and a badge showing how many rules you own never changes and so
    teaches people to ignore it. One indexed count per render for a signed-in
    user, on the partial index over (user_id, seen).

    Swallows its own errors on purpose — a database created before this feature
    has no alert_events table, and a missing badge must not take every page down
    with it."""
    if not current_user.is_authenticated:
        return {"alert_unseen": 0}
    try:
        return {"alert_unseen": db.unseen_alert_count(current_user.id)}
    except Exception:
        return {"alert_unseen": 0}


def _find_period_row(ticker, prefer_kind, as_of):
    """Locate a ticker's finer-period gains — searching the current page's kind
    first, then the other — so the compare box accepts an ETF *or* a stock.
    Returns (row, kind) or (None, None)."""
    ticker = (ticker or "").strip()
    if not ticker:
        return None, None
    other = "etf" if prefer_kind == "stock" else "stock"
    for k in (prefer_kind, other):
        rows, _ = db.period_gainer(k, as_of=as_of if k == prefer_kind else None)
        r = (next((x for x in rows if x["ticker"] == ticker), None)
             or next((x for x in rows if ticker in x["ticker"]), None))
        if r:
            return r, k
    return None, None


def _period_panel(kind, as_of, cmp, cmp2, cat):
    """Build the «محاسبهٔ بازدهٔ دوره‌ای و مقایسه» panel (mirrors etf_gainer.py). Two
    optional tickers, each an ETF *or* a stock:
      • one ticker  → compare it against the top performer per period WITHIN the
        selected category (`cat` = ETF type / stock group; blank = whole market).
      • two tickers → head-to-head comparison of the two, period by period
        (etf_gainer's «Compare Two ETFs», here cross-kind: ETF vs stock too).
    Returns a dict of everything the template needs. Any compared ticker that
    lives in this page's own list is pinned on top of the ranked table."""
    rows, _ = db.period_gainer(kind, as_of=as_of)
    # scope the pool (performance table + top-performer comparison) to the chosen
    # ETF type (stocks: industry group) — so «top performer» means top of THAT
    # category, not the whole market.
    catkey = "type" if kind == "etf" else "sector"
    pool = [r for r in rows if r.get(catkey) == cat] if cat else rows

    compare, compare_kind = _find_period_row(cmp, kind, as_of)
    compare2, compare2_kind = _find_period_row(cmp2, kind, as_of)

    # pin whichever compared tickers belong to this page's kind (and pool)
    pins = [c for c in (compare, compare2) if c is not None and c in pool]
    pin_tk = {c["ticker"] for c in pins}
    display = (pins + [r for r in pool if r["ticker"] not in pin_tk]) if pins else pool

    # head-to-head (both tickers) — winner per period
    head2head = []
    if compare and compare2:
        for p in db.CALC_PERIODS:
            k = p["key"]
            g1, g2 = compare.get(k), compare2.get(k)
            diff = (g1 - g2) if (g1 is not None and g2 is not None) else None
            winner = (None if diff is None else
                      compare["ticker"] if diff > 0 else
                      compare2["ticker"] if diff < 0 else "")
            head2head.append({"label": p["label"], "g1": g1, "g2": g2,
                              "diff": diff, "winner": winner})

    # single-ticker → against the top performer of the (category-scoped) pool
    comparison = []
    if compare and not compare2:
        for p in db.CALC_PERIODS:
            key = p["key"]
            yours = compare.get(key)
            best = None                          # (ticker, gain)
            for r in pool:
                v = r.get(key)
                if v is not None and (best is None or v > best[1]):
                    best = (r["ticker"], v)
            diff = (yours - best[1]) if (yours is not None and best) else None
            comparison.append({
                "label": p["label"], "yours": yours,
                "top_ticker": best[0] if best else None,
                "top": best[1] if best else None, "diff": diff,
            })

    return {"calc_rows": display,
            "compare": compare, "compare_kind": compare_kind,
            "compare2": compare2, "compare2_kind": compare2_kind,
            "comparison": comparison, "head2head": head2head,
            # The Vue island (order 08) renders the long list itself and needs
            # to reproduce the pinning the `display` ordering above encodes.
            # calc_rows stays in the context because the comparison tables and
            # the header count still come from Jinja.
            "pinned_tickers": [c["ticker"] for c in pins]}


@app.after_request
def _cache_policy(resp):
    """Two different caching rules, because HTML and /static/ want opposites.

    HTML is per-user and must never be served stale, so it keeps `no-cache,
    must-revalidate` — the browser may hold a copy but has to ask before showing
    it, which is what keeps a data update from looking like "nothing changed".
    `private` says only this user's browser may store it, never a shared cache.

    What HTML no longer sends is `no-store`. That single token disables Chrome's
    back/forward cache, so «برگشت» re-fetched AND re-rendered the whole page —
    1.9 s for /performance, whose 2.2 MB of markup the browser had just finished
    building. Without it, Back restores the live page from memory in ~50 ms.
    The trade-off, accepted deliberately: a page the user has already seen can be
    restored from the browser's own memory after they log out, until the tab is
    closed. Nothing is written anywhere a second user of the machine could read
    from a cold start, and `no-cache` still forces revalidation on every real
    navigation — the logout redirect included.

    /static/ used to get the SAME no-store header, which was costing a full
    re-download of every asset on every navigation (~45 kB per list page,
    ~285 kB per detail page) — no-store forbids even a conditional request, so
    the browser could not so much as ask for a 304. It was also redundant: the
    templates already cache-bust with ?v=<mtime> via asset_version(), so a
    changed file is a changed URL. Serve those with a year-long immutable
    max-age and let the version query string do the invalidating."""
    ctype = resp.headers.get("Content-Type", "")
    if request.path.startswith("/static/"):
        if resp.status_code < 400:
            # immutable: don't even revalidate on reload — the URL changes when
            # the file's mtime does.
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            resp.headers.pop("Pragma", None)
            resp.headers.pop("Expires", None)
    elif ctype.startswith("text/html"):
        resp.headers["Cache-Control"] = "private, no-cache, must-revalidate"
        # Pragma/Expires are the HTTP/1.0 spelling of no-cache. They are dropped
        # rather than kept: some intermediaries read `Pragma: no-cache` as
        # no-store, which would put the bfcache block straight back.
        resp.headers.pop("Pragma", None)
        resp.headers.pop("Expires", None)
    return resp


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    # Professional landing page — NO database work, so the first page always
    # loads instantly. The live market data lives on the dashboard below.
    return render_template("index.html")


@app.route("/dashboard")
def dashboard():
    # Dashboard shell: paints instantly, then fetches /dashboard/data after load.
    return render_template("dashboard.html")


@app.route("/dashboard/data")
def dashboard_data():
    """Server-rendered dashboard blocks (counts + top gainers/losers + top ETFs),
    fetched lazily by the home page so the landing page itself stays DB-free."""
    summary = db.db_summary()
    stocks, as_of = db.market_gainer("stock")
    etfs, _ = db.market_gainer("etf")
    top_gainers = stocks[:8]
    top_losers = sorted(
        [s for s in stocks if s["p20"] is not None], key=lambda r: r["p20"])[:8]
    etf_top = etfs[:8]
    # «نبض بازار» — advancers vs decliners over the user's default period. It
    # reads the same cached gainer rows this function already has, plus one
    # cached query for the last session, so the panel costs nothing the
    # dashboard was not already paying.
    period = _breadth_period(request.args.get("period"))
    # The kind follows «بازار پیش‌فرض» so the setting reaches this panel too, not
    # only /heatmap — a preference that applies to one of the two screens its
    # own description names is a preference that looks broken.
    breadth_kind = _map_kind(request.args.get("kind"))
    breadth = db.market_breadth(breadth_kind, period=period)

    # ── the graphical half ────────────────────────────────────────────────
    # Indices, the dollar, the live board and retail money flow, plus the SVG
    # paths to draw them. All of it comes from market_data, which is imported
    # lazily and may be looking at a database that predates its tables — so the
    # whole block degrades to None rather than 500ing. The panels below are
    # written to disappear when their data is missing, which is what lets this
    # page keep working on a fresh install with nothing downloaded yet.
    graphs = None
    try:
        md = _md()
        heads = [r for r in md.index_rows() if r["key"] in md.HEADLINE_INDICES]
        heads.sort(key=lambda r: md.HEADLINE_INDICES.index(r["key"]))
        # One 250-bar series for the big chart, 60 bars for each strip. 60 is
        # about a quarter, which is long enough to show a trend and short enough
        # that the line is not a solid block at 110px wide.
        focus = heads[0] if heads else None
        graphs = {
            "heads": heads,
            "focus": focus,
            "focus_chart": spark([v for _, v in md.index_series(focus["key"], bars=250)],
                                 w=1000, h=190, pad=6) if focus else None,
            "strips": {r["key"]: spark([v for _, v in md.index_series(r["key"], bars=60)],
                                       w=110, h=30) for r in heads},
            "usd": md.usd_summary(),
            "usd_strip": spark([v for _, v in md.usd_rows(bars=60)], w=110, h=30),
            "board": md.board_totals(),
            "flow": md.flow_totals(kind="stock", days=5),
            "sectors": md.flow_by_sector(kind="stock", days=5, limit=9),
        }
    except Exception:
        # Logged rather than swallowed silently: a missing table is a data
        # problem to fix, not a normal state, and the dashboard hides the panels
        # either way.
        app.logger.warning("dashboard graphs unavailable", exc_info=True)

    return render_template("_dashboard_data.html", summary=summary, as_of=as_of,
                           top_gainers=top_gainers, top_losers=top_losers,
                           etf_top=etf_top, breadth=breadth, period=period,
                           breadth_kind=breadth_kind,
                           period_label=_period_label(period), g=graphs)


@app.route("/stocks")
def stocks_page():
    market_filter = request.args.get("market") or None
    group = request.args.get("group") or None
    subgroups = db.stock_sub_sectors(group)
    subgroup = request.args.get("subgroup") or None
    if subgroup not in subgroups:        # stale subgroup after changing group
        subgroup = None
    as_of = request.args.get("as_of") or None
    rows, as_of = db.market_gainer("stock", as_of=as_of, market=market_filter,
                                   sector=group, sub_sector=subgroup)
    cmp, cmp2 = request.args.get("cmp"), request.args.get("cmp2")
    cat = request.args.get("cat") or None
    panel = _period_panel("stock", as_of, cmp, cmp2, cat)
    return render_template("market.html", kind="stock", title="تحلیل عملکرد سهام",
                           rows=rows, as_of=as_of, filters=db.stock_markets(),
                           active_filter=market_filter, filter_label="بازار",
                           filter2_name="group", filter2_label="گروه",
                           filters2=db.stock_sectors(), active_filter2=group,
                           filter3_name="subgroup", filter3_label="زیرگروه",
                           filters3=subgroups, active_filter3=subgroup,
                           cmp=cmp, cmp2=cmp2, cat=cat, **panel)


@app.route("/etfs")
def etfs_page():
    etf_type = request.args.get("type") or None
    as_of = request.args.get("as_of") or None
    rows, as_of = db.market_gainer("etf", as_of=as_of, etf_type=etf_type)
    cmp, cmp2 = request.args.get("cmp"), request.args.get("cmp2")
    cat = request.args.get("cat") or None
    panel = _period_panel("etf", as_of, cmp, cmp2, cat)
    return render_template("market.html", kind="etf", title="تحلیل عملکرد صندوق‌ها",
                           rows=rows, as_of=as_of, filters=db.etf_types(),
                           active_filter=etf_type, filter_label="نوع صندوق",
                           cmp=cmp, cmp2=cmp2, cat=cat, **panel)


def _performance_data(args):
    """Everything /performance shows, from the query arguments.

    Split out of the view so the page and /api/performance/<kind> compute it the
    SAME way. The island fetches this instead of the server rendering 782 rows ×
    22 columns into HTML, so nothing here — the filters, the 🏆 top-performer of
    each window, the compare table — is re-implemented in TypeScript. The browser
    only lays out what is on screen; every number is still this function's."""
    kind = args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    if kind == "stock":
        groups, group_label, markets = db.stock_sectors(), "گروه", db.stock_markets()
    else:
        groups, group_label, markets = db.etf_types(), "نوع صندوق", []
    group = args.get("group") or None
    if group not in groups:              # stale group after switching kind
        group = None
    # sub-group (زیرگروه) applies to stocks only, cascading within the sector
    subgroups = db.stock_sub_sectors(group) if kind == "stock" else []
    subgroup = (args.get("subgroup") or None) if kind == "stock" else None
    if subgroup not in subgroups:        # stale subgroup after changing group/kind
        subgroup = None
    market_filter = (args.get("market") or None) if kind == "stock" else None
    if market_filter and market_filter not in markets:
        market_filter = None

    # از/تا calendars: «تا» is the base/as-of date every fixed period is measured
    # back from; «از…تا» additionally adds a «بازهٔ دلخواه» (custom-range) column.
    rf = db.norm_jdate(args.get("rfrom"))
    rt = db.norm_jdate(args.get("rto"))
    if rf and rt and rf > rt:            # entered backwards → swap
        rf, rt = rt, rf
    base = rt or db.latest_date(kind)

    rows, as_of = db.perf_multi(
        kind, as_of=base, market=market_filter,
        sector=group if kind == "stock" else None,
        sub_sector=subgroup if kind == "stock" else None,
        etf_type=group if kind == "etf" else None)

    # Column set for the wide table: [custom range] + trailing windows + all-time.
    perf_cols = list(db.PERF_PERIODS) + [{"key": "first", "label": "از ابتدا"}]
    custom_map = {}
    if rf and rt and rf < rt:
        custom_map = db.range_gainer(kind, rf, rt)     # unfiltered: {ticker: {...}}
        for r in rows:
            c = custom_map.get(r["ticker"], {})
            r["custom_gain"], r["custom_ceil"], r["custom_floor"] = (
                c.get("gain"), c.get("ceil"), c.get("floor"))
        perf_cols = [{"key": "custom", "label": "بازهٔ دلخواه"}] + perf_cols

    # 🏆 top performer (highest gain) per window, within the current filter scope.
    tops = []
    for c in perf_cols:
        gk = c["key"] + "_gain"
        best = max((r for r in rows if r.get(gk) is not None),
                   key=lambda r: r[gk], default=None)
        tops.append({"key": c["key"], "label": c["label"],
                     "ticker": best["ticker"] if best else None,
                     "gain": best[gk] if best else None})

    # Compare-a-ticker: pull the entered ticker from the UNFILTERED pool (so it can
    # sit outside the selected group) and stack it against each window's best.
    cmp = (args.get("cmp") or "").strip()
    compare, comparison = None, []
    if cmp:
        all_rows, _ = db.perf_multi(kind, as_of=base)
        compare = (next((r for r in all_rows if r["ticker"] == cmp), None)
                   or next((r for r in all_rows if cmp in r["ticker"]), None))
        if compare:
            if custom_map:
                c = custom_map.get(compare["ticker"], {})
                compare["custom_gain"], compare["custom_ceil"], compare["custom_floor"] = (
                    c.get("gain"), c.get("ceil"), c.get("floor"))
            for c, top in zip(perf_cols, tops):
                yours = compare.get(c["key"] + "_gain")
                comparison.append({
                    "label": c["label"], "yours": yours,
                    "top_ticker": top["ticker"], "top": top["gain"],
                    "diff": (yours - top["gain"])
                            if (yours is not None and top["gain"] is not None) else None})

    return {"kind": kind, "rows": rows, "as_of": as_of, "perf_cols": perf_cols,
            "tops": tops, "rfrom": rf, "rto": rt, "cmp": cmp, "compare": compare,
            "comparison": comparison, "groups": groups, "group_label": group_label,
            "group": group, "subgroups": subgroups, "subgroup": subgroup,
            "markets": markets, "market": market_filter}


@app.route("/performance")
def performance_page():
    """«بازدهٔ بازه» — per-ticker gain + سقف (ceil) + کف (floor) across the fixed
    windows 1M/3M/6M/1Y/2Y/3Y plus «از ابتدا» (all-time), for stocks OR ETFs.
    Ported from the old Streamlit stock_gain analyzer. Also surfaces the top
    performer of each window and lets you pin one ticker to compare against them.
    Mirrors the kind/market/group toggle used by the strategies & filters pages.

    This view renders only the SHELL — the hero, the date form and the island's
    mount point. It deliberately does not call _performance_data(): the island
    fetches that from /api/performance/<kind>, and computing it here as well
    would do the whole ~780-row job twice per navigation for numbers no longer
    printed into the HTML. Only the filter values the shell itself echoes (into
    the date form's hidden inputs and the «پاک‌کردن تاریخ‌ها» link) are read
    here, and the dates are normalised so a hand-typed URL still round-trips."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    rf = db.norm_jdate(request.args.get("rfrom"))
    rt = db.norm_jdate(request.args.get("rto"))
    if rf and rt and rf > rt:            # entered backwards → swap
        rf, rt = rt, rf
    return render_template(
        "performance.html", kind=kind, rfrom=rf, rto=rt,
        market=request.args.get("market") or None,
        group=request.args.get("group") or None,
        subgroup=request.args.get("subgroup") or None,
        cmp=(request.args.get("cmp") or "").strip())


def _scan_scope(args):
    """kind / group / sub-group, validated the way both scan pages validate them.

    Shared by /strategies, /filters and /api/scan/<what>/<kind> so the island and
    the page can never disagree about which stale filter to drop."""
    kind = args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    if kind == "stock":
        groups, group_label = db.stock_sectors(), "گروه"
    else:
        groups, group_label = db.etf_types(), "نوع صندوق"
    group = args.get("group") or None
    if group not in groups:              # e.g. a stale sector after switching kind
        group = None
    subgroups = db.stock_sub_sectors(group) if kind == "stock" else []
    subgroup = (args.get("subgroup") or None) if kind == "stock" else None
    if subgroup not in subgroups:
        subgroup = None
    return {"kind": kind, "groups": groups, "group_label": group_label,
            "group": group, "subgroups": subgroups, "subgroup": subgroup}


#: what a scan row shows: نماد / نام / گروه / قیمت پایانی / RSI (+ the chevron).
_SCAN_FIELDS = ("id", "ticker", "name", "group", "latest", "rsi")


@app.route("/api/scan/<what>/<kind>")
def api_scan(what, kind):
    """The «استراتژی‌ها» and «فیلترها» tables as JSON, for their islands.

    Both pages are the same shape — N named sections, each a list of matching
    symbols — so they share one endpoint and one island. Rendered as HTML they
    were the two heaviest pages left in the app (3,782 and 2,497 rows across 16
    and 19 tables), and almost all of it is markup for sections the reader never
    scrolls to.

    The scan itself is unchanged and still cached: db.strategy_scan /
    db.filter_scan narrow one shared full scan in memory."""
    if what not in ("strategies", "filters") or kind not in ("stock", "etf"):
        abort(404)
    args = request.args.to_dict(flat=True)
    args["kind"] = kind
    scope = _scan_scope(args)

    t0 = time.perf_counter()
    if what == "strategies":
        scan = db.strategy_scan(kind, group=scope["group"], sub_group=scope["subgroup"])
        meta, bucket = db.STRATEGIES, scan["by_strategy"]
        extra = {"picks": scan.get("picks", []),
                 "strat_names": {s["key"]: s["name"] for s in db.STRATEGIES}}
    else:
        scan = db.filter_scan(kind, group=scope["group"], sub_group=scope["subgroup"])
        meta, bucket = db.FILTERS, scan["by_filter"]
        extra = {"categories": db.FILTER_CATEGORIES}
    elapsed = (time.perf_counter() - t0) * 1000.0

    # Normalised, not nested. A symbol matches several strategies, so sending a
    # whole row per section shipped «فولاد مبارکه اصفهان» and its industry group
    # once per hit: 2.0 MB of JSON against the 2.2 MB of HTML it replaces, which
    # would have been a poor trade. Each symbol is sent ONCE and the sections
    # carry ids, which is also how they are rendered — one shared row object.
    symbols = {}

    def ref(r):
        sid = r["id"]
        if sid not in symbols:
            symbols[sid] = {k: r.get(k) for k in _SCAN_FIELDS}
        return sid

    sections = [{k: v for k, v in m.items()} | {"ids": [ref(r) for r in bucket.get(m["key"], [])]}
                for m in meta]
    if "picks" in extra:
        extra["picks"] = [{"id": ref(r), "score": r.get("score"),
                           "signals": r.get("signals", [])} for r in extra["picks"]]
    return jsonify({
        "what": what, "as_of": scan["as_of"], "scanned": scan["scanned"],
        "count": scan["count"], "sections": sections, "symbols": symbols,
        **scope, **extra, "server_ms": round(elapsed, 1),
    })


@app.route("/api/screener/<kind>")
def api_screener(kind):
    """«غربالگر هوشمند» as JSON, for the island on screener.html.

    Filtered server-side like /api/scan: db.score_scan() narrows one cached scan,
    so a group or verdict change is a JSON dump rather than a re-scan."""
    if kind not in ("stock", "etf"):
        abort(404)
    args = request.args.to_dict(flat=True)
    args["kind"] = kind
    scope = _scan_scope(args)
    verdict = args.get("verdict") or None
    if verdict not in {b[1] for b in db.SCORE_BANDS}:
        verdict = None

    t0 = time.perf_counter()
    scan = db.score_scan(kind, group=scope["group"], sub_group=scope["subgroup"],
                         verdict=verdict)
    elapsed = (time.perf_counter() - t0) * 1000.0

    keep = ("id", "ticker", "name", "group", "latest", "score", "verdict",
            "trend", "momentum", "rsi")
    rows = [{k: r.get(k) for k in keep} for r in scan["rows"]]
    watched = sorted(db.watch_keys(current_user.id)) if current_user.is_authenticated else []
    return jsonify({
        "kind": kind, "as_of": scan["as_of"], "scanned": scan["scanned"],
        "count": scan["count"], "rows": rows, "verdict": verdict,
        "bands": [{"min": b[0], "key": b[1], "label": b[2], "tone": b[3]}
                  for b in db.SCORE_BANDS],
        "etf_type_colors": db.ETF_TYPE_COLORS, "watched": watched,
        **scope, "server_ms": round(elapsed, 1),
    })


@app.route("/api/performance/<kind>")
def api_performance(kind):
    """The performance table as JSON, for the island on performance.html.

    Filtered SERVER-side, unlike /api/market/<kind>: this table is ten times as
    wide, so shipping the whole market to filter it in the browser would trade
    the problem for a bigger one. The filtered read is a slice of the same cached
    scan (db.perf_multi caches the unfiltered list and narrows it in memory), so
    a group change costs a JSON dump rather than a database query.

    Returned alongside the rows: the 🏆 per-window winners and the compare table,
    both computed by the same Python the page used, so switching a dropdown
    cannot leave them disagreeing with the rows."""
    if kind not in ("stock", "etf"):
        abort(404)
    args = request.args.to_dict(flat=True)
    args["kind"] = kind
    t0 = time.perf_counter()
    d = _performance_data(args)
    elapsed = (time.perf_counter() - t0) * 1000.0

    watched = sorted(db.watch_keys(current_user.id)) if current_user.is_authenticated else []
    return jsonify({
        # Full precision, deliberately. Rounding the percentages to four decimals
        # would take ~20% off the payload, but checked against the live data it
        # moved a number the user reads: 184.43496801705757 displays as ۱۸۴.۴۳
        # and its 4-decimal rounding (184.435) displays as ۱۸۴.۴۴. nginx gzips
        # this response anyway, which recovers far more than the rounding would.
        "kind": d["kind"], "as_of": d["as_of"], "rows": d["rows"],
        "cols": d["perf_cols"], "tops": d["tops"],
        "compare": ({"ticker": d["compare"]["ticker"], "name": d["compare"]["name"],
                     "latest": d["compare"]["latest"]} if d["compare"] else None),
        "comparison": d["comparison"], "cmp": d["cmp"],
        "groups": d["groups"], "group": d["group"], "group_label": d["group_label"],
        "subgroups": d["subgroups"], "subgroup": d["subgroup"],
        "markets": d["markets"], "market": d["market"],
        "etf_type_colors": db.ETF_TYPE_COLORS,
        "watched": watched,
        "server_ms": round(elapsed, 1),
    })


@app.route("/strategies")
def strategies_page():
    """The shell only — the island fetches /api/scan/strategies/<kind>. Running
    the scan here as well would repeat the whole job for markup that is no longer
    printed; the shell needs nothing but the kind."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    return render_template("strategies.html", kind=kind)


@app.route("/filters")
def filters_page():
    """The shell only — see strategies_page(); the island fetches
    /api/scan/filters/<kind>."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    return render_template("filters.html", kind=kind)


@app.route("/screener")
def screener_page():
    """«غربالگر هوشمند» — every stock/ETF ranked by the composite technical score
    (db.signal_score), so a trader can find the strongest setups right now.
    Filterable by kind / group / sub-group / verdict band.

    The shell only; the island fetches /api/screener/<kind> and renders the rows
    it can see (779 of them was 1.1 MB of HTML and 16,600 DOM nodes)."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    return render_template("screener.html", kind=kind)


# ---------------------------------------------------------------------------
# «طراحی فیلتر» — the visual filter designer (filter_engine.py)
#
# The page is a shell; everything happens in the island. Three of the four
# endpoints take POST with the graph in the body rather than in the query
# string: a real graph is several kilobytes of JSON and would not survive a URL,
# and it is not a bookmarkable view anyway — the bookmark is the SAVED filter.
# ---------------------------------------------------------------------------
#: One market-wide run is ~800 symbols × 400 bars of Python. Two of them at once
#: on a 2-worker box is a page that stops responding, so runs queue instead of
#: piling up. The wait is bounded: a caller that cannot get in within
#: DESIGNER_RUN_WAIT seconds is told to try again rather than held forever.
_designer_run_lock = threading.Semaphore(int(os.environ.get("DESIGNER_RUN_SLOTS", "2")))
DESIGNER_RUN_WAIT = float(os.environ.get("DESIGNER_RUN_WAIT", "25"))


@app.route("/filter-designer")
def filter_designer_page():
    """«طراحی فیلتر» — build a screening rule as a graph and run it on the market.

    The nineteen filters on /filters and the sixteen strategies on /strategies
    are ours; this is where a user writes their own, without us shipping code."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    return render_template("designer.html", kind=kind)


@app.route("/filter-designer/result")
def filter_designer_result_page():
    """The page «اجرا» lands on — the matches, in a table of their own.

    The results used to live in a panel under the canvas, where the table that
    is the entire point of running a filter got the bottom third of the screen.

    The shell knows nothing about the graph. When the URL names a saved filter
    the island reads it from the database (which is what makes that URL a
    bookmark worth keeping); otherwise it reads the draft the canvas left in
    localStorage. Neither belongs in this request: a graph is kilobytes of JSON,
    too big for a query string and too big for a signed session cookie, and a
    server-side handoff token would buy an expiry and an "this result expired"
    screen for a user who left the tab open over lunch."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    try:
        filter_id = int(request.args.get("filter", "") or 0) or None
    except ValueError:
        filter_id = None
    return render_template("designer_result.html", kind=kind, filter_id=filter_id)


@app.route("/api/designer/catalog")
def api_designer_catalog():
    """The node palette, the ready-made examples, and the group lists for the
    scope bar. One request; the island caches it for the life of the page."""
    args = request.args.to_dict(flat=True)
    scope = _scan_scope(args)
    cat = filter_engine.catalog()
    return jsonify({
        **cat,
        "examples": [{"key": e["key"], "name": e["name"], "desc": e["desc"],
                      "graph": e["graph"]} for e in filter_engine.EXAMPLES],
        **scope,
        "authenticated": current_user.is_authenticated,
        "as_of": db.latest_date(scope["kind"]),
    })


def _designer_body():
    """The POSTed JSON, or a 400 the island can display. Kept separate because
    all three POST endpoints need the identical five lines of validation."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return None, (jsonify({"error": "بدنهٔ درخواست JSON نیست."}), 400)
    kind = body.get("kind")
    if kind not in ("stock", "etf"):
        kind = "stock"
    return {"graph": body.get("graph"), "kind": kind,
            "group": body.get("group") or None,
            "subgroup": body.get("subgroup") or None,
            "ticker": (body.get("ticker") or "").strip(),
            "name": body.get("name") or "", "id": body.get("id"),
            "description": body.get("description") or ""}, None


@app.route("/api/designer/run", methods=["POST"])
def api_designer_run():
    """Evaluate a graph across the whole market and return the matches."""
    body, err = _designer_body()
    if err:
        return err
    if not _designer_run_lock.acquire(timeout=DESIGNER_RUN_WAIT):
        return jsonify({"error": "سامانه در حال اجرای فیلترهای دیگری است؛ "
                                 "چند لحظه بعد دوباره تلاش کنید."}), 429
    t0 = time.perf_counter()
    try:
        out = filter_engine.run(body["graph"], kind=body["kind"],
                                group=body["group"], sub_group=body["subgroup"])
    except filter_engine.GraphError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        log.exception("designer run failed")
        return jsonify({"error": "اجرای فیلتر با خطا روبه‌رو شد."}), 500
    finally:
        _designer_run_lock.release()

    # No `watched` / `etf_type_colors` here, unlike /api/screener: the result
    # table has no star and no type badge, and a query per run whose answer is
    # discarded is worse than a missing feature. The rows link through to the
    # security page, where starring already works.
    return jsonify({**out, "kind": body["kind"], "group": body["group"],
                    "subgroup": body["subgroup"],
                    "server_ms": round((time.perf_counter() - t0) * 1000.0, 1)})


@app.route("/api/designer/explain", methods=["POST"])
def api_designer_explain():
    """Every node's last few values for ONE symbol — «چرا این نماد آمد؟».

    Cheap (one symbol, not eight hundred) so it is deliberately outside the run
    semaphore: inspecting a result must stay possible while a scan is queued."""
    body, err = _designer_body()
    if err:
        return err
    if not body["ticker"]:
        return jsonify({"error": "نماد مشخص نشده است."}), 400
    try:
        return jsonify(filter_engine.explain(body["graph"], body["kind"], body["ticker"]))
    except filter_engine.GraphError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        log.exception("designer explain failed")
        return jsonify({"error": "محاسبهٔ جزئیات با خطا روبه‌رو شد."}), 500


#: A backtest reads years of history where a scan reads months, so it gets a
#: lane of its own rather than sharing the run semaphore: one in flight at a
#: time, and a scan must never end up queued behind one. The wait is short
#: because the honest answer to "two people pressed بک‌تست at once" is "try
#: again in a moment", not a request that sits open for a minute and then times
#: out in the proxy.
_backtest_lock = threading.Semaphore(int(os.environ.get("BACKTEST_SLOTS", "1")))
BACKTEST_WAIT = float(os.environ.get("BACKTEST_WAIT", "8"))


@app.route("/filter-backtest")
def filter_backtest_page():
    """«بک‌تست فیلتر» — the same graph, replayed over history.

    A shell like the other two designer pages: the island reads the graph from
    the saved filter named in the URL, or from the draft the canvas left in
    localStorage, for the reasons written out in filter_designer_result_page."""
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    try:
        filter_id = int(request.args.get("filter", "") or 0) or None
    except ValueError:
        filter_id = None
    return render_template("designer_backtest.html", kind=kind, filter_id=filter_id)


@app.route("/api/designer/backtest", methods=["POST"])
def api_designer_backtest():
    """Replay a graph over history and return its report card."""
    body, err = _designer_body()
    if err:
        return err
    raw = request.get_json(silent=True) or {}

    def _num(key, default, lo, hi):
        try:
            v = float(raw.get(key, default))
        except (TypeError, ValueError):
            return default
        return min(hi, max(lo, v))

    # 1500, not the engine's own 3000-bar ceiling. The deepest window the page
    # offers is 1000 sessions (~17 s); a hand-made request for the engine's
    # maximum measured 67 s, which fits inside the 120 s worker timeout but
    # leaves a single caller holding the backtest lane for over a minute. The
    # engine will still go deeper for a script that imports it directly.
    sessions = int(_num("sessions", backtest_engine.DEFAULT_SESSIONS, 20, 1500))
    cost = _num("cost", backtest_engine.DEFAULT_COST, 0.0, 10.0)
    hold = int(_num("hold", 0, 0, 250)) or None
    if not _backtest_lock.acquire(timeout=BACKTEST_WAIT):
        return jsonify({"error": "یک بک‌تست دیگر در حال اجراست؛ چند لحظه بعد "
                                 "دوباره تلاش کنید."}), 429
    t0 = time.perf_counter()
    try:
        out = backtest_engine.backtest(
            body["graph"], kind=body["kind"], sessions=sessions, hold=hold,
            group=body["group"], sub_group=body["subgroup"], cost=cost,
            require_fill=bool(raw.get("fill", True)),
            repeat=bool(raw.get("repeat", False)))
    except (backtest_engine.BacktestError, filter_engine.GraphError) as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        log.exception("designer backtest failed")
        return jsonify({"error": "اجرای بک‌تست با خطا روبه‌رو شد."}), 500
    finally:
        _backtest_lock.release()
    return jsonify({**out, "server_ms": int((time.perf_counter() - t0) * 1000)})


@app.route("/api/designer/filters", methods=["GET", "POST"])
@login_required
def api_designer_filters():
    """The signed-in user's saved graphs. GET lists them (without the graphs —
    the picker shows names), POST inserts or updates one."""
    if request.method == "GET":
        return jsonify({"filters": filter_engine.list_filters(current_user.id)})
    body, err = _designer_body()
    if err:
        return err
    try:
        fid = filter_engine.save_filter(
            current_user.id, body["name"], body["kind"], body["graph"],
            description=body["description"], filter_id=body["id"])
    except filter_engine.GraphError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"id": fid, "filters": filter_engine.list_filters(current_user.id)})


@app.route("/api/designer/filters/<int:filter_id>", methods=["GET", "DELETE"])
@login_required
def api_designer_filter(filter_id):
    if request.method == "DELETE":
        n = filter_engine.delete_filter(current_user.id, filter_id)
        if not n:
            abort(404)
        return jsonify({"ok": True, "filters": filter_engine.list_filters(current_user.id)})
    row = filter_engine.get_filter(current_user.id, filter_id)
    if not row:
        abort(404)
    return jsonify(row)


def _detail_extras(kind, ticker):
    """The «حقیقی و حقوقی» tab's data for one symbol, plus its snapshot numbers.

    Both degrade to None/[] when the datasets have not been fetched, and the
    template shows the tab only when there is something in it — an empty tab
    that says nothing is worse than no tab at all."""
    md = _md()
    return {"flow": md.flow_history(kind, ticker, bars=60),
            "fund": md.fundamentals(ticker),
            "book": md.order_book(ticker),
            "queue": md.queue_history(kind, ticker, bars=60),
            "holders": md.shareholders(ticker),
            "tapes": md.intraday_coverage(ticker)}


@app.route("/stock/<int:stock_id>")
def stock_detail(stock_id):
    s = db.get_stock(stock_id)
    if not s:
        abort(404)
    analysis = db.security_analysis("stock", s["ticker"])
    tech = db.technical_summary("stock", s["ticker"])
    return render_template("security.html", kind="stock", entity=s, analysis=analysis,
                           tech=tech, back_url=url_for("stocks_page"), back_label="سهام",
                           **_detail_extras("stock", s["ticker"]))


@app.route("/etf/<int:etf_id>")
def etf_detail(etf_id):
    e = db.get_etf(etf_id)
    if not e:
        abort(404)
    analysis = db.security_analysis("etf", e["ticker"])
    tech = db.technical_summary("etf", e["ticker"])
    return render_template("security.html", kind="etf", entity=e, analysis=analysis,
                           tech=tech, back_url=url_for("etfs_page"), back_label="صندوق‌ها",
                           **_detail_extras("etf", e["ticker"]))


# The periods نقشهٔ بازار and نبض بازار can be read over. 'd1' (the last
# session) is not one of db.PERIODS — it is computed by db.last_session(), which
# is why it is spelled out here rather than derived from PERIODS.
MAP_PERIODS = [{"key": "d1", "label": "آخرین روز"}] + [
    {"key": p["key"], "label": p["label"]} for p in db.PERIODS
]
MAP_PERIOD_KEYS = tuple(p["key"] for p in MAP_PERIODS)


def _period_label(key):
    """The Persian name of a map/breadth period. Falls back to the key itself so
    a period added to MAP_PERIODS without a label still renders something."""
    for p in MAP_PERIODS:
        if p["key"] == key:
            return p["label"]
    return key


def _breadth_period(value):
    """A period key from a query string, falling back to the user's default.

    An unknown value falls back rather than 400s: these arrive from bookmarks
    and from links shared between users, and a saved link to a period that has
    since been renamed should still show the map."""
    if value in MAP_PERIOD_KEYS:
        return value
    if current_user.is_authenticated:
        return db.get_prefs(current_user.id)["default_period"]
    return prefs.DEFAULTS["default_period"]


def _map_kind(value):
    if value in ("stock", "etf"):
        return value
    if current_user.is_authenticated:
        return db.get_prefs(current_user.id)["default_kind"]
    return prefs.DEFAULTS["default_kind"]


@app.route("/heatmap")
def heatmap_page():
    """«نقشهٔ بازار» — the market as one screen of tiles, grouped by صنعت (or by
    نوع صندوق), each tile sized by traded value and coloured by return.

    The shell only; the map itself is drawn by static/js/heatmap.js from
    /api/heatmap/<kind>. Rendering ~۷۸۰ tiles server-side would put the same
    weight of markup on the page that order 08 spent its whole budget removing
    from the market tables."""
    kind = _map_kind(request.args.get("kind"))
    period = _breadth_period(request.args.get("period"))
    return render_template("heatmap.html", kind=kind, period=period,
                           periods=MAP_PERIODS)


@app.route("/api/heatmap/<kind>")
def api_heatmap(kind):
    if kind not in ("stock", "etf"):
        abort(404)
    period = _breadth_period(request.args.get("period"))
    rows, as_of, groups = db.market_map(kind, period=period)
    return jsonify({
        "kind": kind, "as_of": as_of, "period": period,
        "groups": groups,
        # Only what a tile draws. The full row carries market / sub_sector /
        # volume as well, and ۷۸۰ of those is ۳۰۰ kB of JSON for fields nothing
        # on this screen reads.
        "rows": [{"t": r["ticker"], "n": r["name"], "g": r["group"],
                  "c": r["chg"], "v": r["value"], "p": r["latest"], "id": r["id"]}
                 for r in rows],
    })


@app.route("/api/breadth/<kind>")
def api_breadth(kind):
    if kind not in ("stock", "etf"):
        abort(404)
    return jsonify(db.market_breadth(kind, period=_breadth_period(request.args.get("period"))))


# ===========================================================================
# شاخص‌ها / پول حقیقی و حقوقی / تابلوی زنده
#
# The three pages the four new datasets earn. Server-rendered rather than Vue
# islands, and deliberately: the island pattern on this platform exists for the
# four tables that render 800 symbols × 20+ columns, where the markup itself was
# the bottleneck. None of these is that shape. «شاخص‌ها» is fifty rows, «پول
# حقیقی و حقوقی» answers a ranked question and is LIMITed to what a person can
# read, and «تابلوی زنده» keeps its filters on the server so the default view is
# 300 rows that mean something rather than a 1,500-row dump. A fourth build
# entry for that would cost more than it buys.
# ===========================================================================
def _md():
    """market_data, imported lazily so a database without the new tables cannot
    stop the whole app from importing."""
    import market_data
    return market_data


@app.route("/indices")
def indices_page():
    """«شاخص‌ها» — the ten market indices, the forty sector ones, and the dollar."""
    md = _md()
    focus = (request.args.get("focus") or "cwi").strip()
    market_rows = md.index_rows()
    sector_rows = md.index_rows(sectors=True)
    known = {r["key"] for r in market_rows} | {r["key"] for r in sector_rows}
    if focus not in known:
        focus = market_rows[0]["key"] if market_rows else "cwi"
    headline = [r for r in market_rows if r["key"] in md.HEADLINE_INDICES]
    headline.sort(key=lambda r: md.HEADLINE_INDICES.index(r["key"]))
    return render_template(
        "indices.html",
        market_rows=market_rows, sector_rows=sector_rows, headline=headline,
        periods=md.INDEX_PERIODS, focus=focus,
        focus_label=md.index_label(focus),
        series=md.index_series(focus, bars=240),
        usd=md.usd_summary(), usd_series=md.usd_rows(bars=240),
        sector_of_group=md.SECTOR_OF_GROUP,
        fresh=md.freshness())


@app.route("/api/indices/series/<path:key>")
def api_index_series(key):
    """Chart points for one index — the shape static/js/chart.js already reads."""
    md = _md()
    bars = max(20, min(2000, int(request.args.get("bars") or 240)))
    if key == "usd":
        pts = md.usd_rows(bars=bars)
        label = "دلار آزاد"
    else:
        pts = md.index_series(key, bars=bars)
        label = md.index_label(key)
    return jsonify({"key": key, "label": label,
                    "points": [{"d": d, "v": v} for d, v in pts]})


@app.route("/moneyflow")
def moneyflow_page():
    """«پول حقیقی و حقوقی» — where retail money went, and with what conviction."""
    md = _md()
    kind = request.args.get("kind", "stock")
    if kind not in ("stock", "etf"):
        kind = "stock"
    try:
        days = int(request.args.get("days") or 5)
    except ValueError:
        days = 5
    if days not in [d for d, _ in md.FLOW_PERIODS]:
        days = 5
    order = request.args.get("order") or "net_desc"
    if order not in dict(md.FLOW_SORTS):
        order = "net_desc"
    sector = (request.args.get("sector") or "").strip() or None
    market = (request.args.get("market") or "").strip() or None

    data = md.money_flow(kind=kind, days=days, sector=sector, market=market,
                         order=order, limit=300)
    return render_template(
        "moneyflow.html", kind=kind, days=days, order=order,
        sector=sector, market=market,
        rows=data["rows"], lo=data["lo"], hi=data["hi"],
        periods=md.FLOW_PERIODS, sorts=md.FLOW_SORTS,
        totals=md.flow_totals(kind=kind, days=days),
        by_sector=md.flow_by_sector(kind=kind, days=days),
        sectors=db.stock_sectors() if kind == "stock" else [],
        markets=db.stock_markets() if kind == "stock" else db.etf_types(),
        fresh=md.freshness())


@app.route("/live")
def live_page():
    """«تابلوی زنده» — the whole-market snapshot: queues, per-capita queue
    values, EPS / P-E, ارزش بازار and حجم مبنا, plus the five-level order book."""
    md = _md()
    order = request.args.get("order") or "value_desc"
    if order not in dict(md.BOARD_SORTS):
        order = "value_desc"
    queue = request.args.get("queue") or None
    if queue not in ("buy", "sell", None):
        queue = None
    sector = (request.args.get("sector") or "").strip() or None
    market = (request.args.get("market") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None

    data = md.board(sector=sector, market=market, queue=queue, q=q,
                    order=order, limit=300)
    return render_template(
        "live.html", order=order, queue=queue, sector=sector, market=market, q=q,
        rows=data["rows"], j_date=data["j_date"], captured=data["captured"],
        total=data["total"], shown=data["shown"],
        sorts=md.BOARD_SORTS, sectors=md.board_sectors(),
        markets=md.board_markets(), totals=md.board_totals(),
        queue_floor=md.QUEUE_FLOOR, fresh=md.freshness())


@app.route("/api/orderbook/<path:ticker>")
def api_order_book(ticker):
    """One symbol's five depths, fetched when a board row is expanded rather
    than rendered into all 300 of them: five rows per symbol is 1,500 rows of
    markup nobody has asked to see."""
    md = _md()
    rows = md.order_book(ticker)
    return jsonify({
        "ticker": ticker,
        "captured": rows[0]["captured_at"].isoformat() if rows else None,
        "rows": [{"depth": r["depth"],
                  "sell_no": r["sell_no"], "sell_vol": r["sell_vol"],
                  "sell_price": r["sell_price"], "buy_price": r["buy_price"],
                  "buy_vol": r["buy_vol"], "buy_no": r["buy_no"]} for r in rows],
        "day_ul": rows[0]["day_ul"] if rows else None,
        "day_ll": rows[0]["day_ll"] if rows else None,
    })


@app.route("/settings")
def settings_page():
    """«تنظیمات» — the display settings, saved per account.

    Every control here writes through static/js/account.js to
    PATCH /api/me/prefs and takes effect immediately; nothing is behind a «ذخیره»
    button, because a theme picker you have to confirm is a theme picker you
    cannot preview."""
    return render_template("settings.html", themes=prefs.THEMES,
                           periods=db.PERIODS, P=prefs)


@app.route("/help")
def help_page():
    """«راهنما» — what each screen answers and how to read its columns."""
    # Counted rather than written down: this paragraph said «ده فیلتر آماده»
    # for as long as there were thirteen, because a number in prose does not
    # move when the list it describes does.
    return render_template("help.html", periods=db.PERIODS,
                           perf_periods=db.PERF_PERIODS,
                           example_count=len(filter_engine.EXAMPLES))


@app.route("/about")
def about_page():
    """«درباره» — what this platform is and where its numbers come from."""
    return render_template("about.html", summary=db.db_summary())


@app.route("/watchlist")
def watchlist_page():
    """The user's «دیده‌بان» — starred stocks & ETFs with their period returns,
    reusing the cached market-gainer tables (so it's fast)."""
    keys = db.watch_keys(current_user.id)
    stock_all, as_of_s = db.market_gainer("stock")
    etf_all, as_of_e = db.market_gainer("etf")
    stock_rows = [r for r in stock_all if f"stock:{r['ticker']}" in keys]
    etf_rows = [r for r in etf_all if f"etf:{r['ticker']}" in keys]
    return render_template("watchlist.html", stock_rows=stock_rows, etf_rows=etf_rows,
                           as_of=as_of_s or as_of_e, total=len(keys))


@app.route("/api/watchlist/toggle", methods=["POST"])
def api_watchlist_toggle():
    """Star ⇄ un-star a symbol for the current user. JSON in/out."""
    data = request.get_json(silent=True) or {}
    kind = (data.get("kind") or "").strip()
    ticker = (data.get("ticker") or "").strip()
    entity_id = data.get("entity_id")
    if kind not in ("stock", "etf") or not ticker:
        return jsonify({"ok": False, "error": "نماد نامعتبر است."}), 400
    try:
        eid = int(entity_id) if entity_id not in (None, "") else None
    except (TypeError, ValueError):
        eid = None
    watched = db.toggle_watch(current_user.id, kind, ticker, eid)
    return jsonify({"ok": True, "watched": watched, "count": db.watch_count(current_user.id)})


# ---------------------------------------------------------------------------
# هشدارها — alerts
# ---------------------------------------------------------------------------
@app.route("/alerts")
@login_required
def alerts_page():
    """The rules a user has set, and what they have fired.

    Opening this page marks the feed read: the badge in the nav exists to bring
    someone here, and a badge that survives the visit it asked for is a badge
    people learn to ignore."""
    events = db.alert_events(current_user.id, limit=60)
    db.mark_alerts_seen(current_user.id)
    return render_template("alerts.html",
                           alerts=db.list_alerts(current_user.id),
                           events=events,
                           rules=db.ALERT_RULES,
                           summary=db.db_summary())


@app.route("/alerts/new", methods=["POST"])
@login_required
def alerts_create():
    kind = (request.form.get("kind") or "stock").strip()
    ticker = (request.form.get("ticker") or "").strip()
    rule = (request.form.get("rule") or "").strip()
    repeat_mode = (request.form.get("repeat_mode") or "once").strip()
    raw = (request.form.get("threshold") or "").strip()
    #  Persian digits in a number field are the normal case here, not an edge
    #  one: the whole app renders «۱۲۳» and a user copying a price back into
    #  this form pastes exactly that.
    raw = raw.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫،", "0123456789.,")).replace(",", "")
    try:
        threshold = float(raw)
    except ValueError:
        flash("مقدار آستانه را به عدد وارد کنید.", "error")
        return redirect(url_for("alerts_page"))
    if not ticker:
        flash("نماد را وارد کنید.", "error")
        return redirect(url_for("alerts_page"))
    if rule not in db.ALERT_RULES:
        flash("نوع هشدار نامعتبر است.", "error")
        return redirect(url_for("alerts_page"))

    #  Reject a ticker the price tables have never heard of, here rather than
    #  letting it become a rule that silently never fires. That is the worst
    #  failure mode a notification feature has: nothing errors, nothing arrives,
    #  and the user concludes the alerts are broken. The evaluator joins on the
    #  ticker, so this is exactly the value that has to exist.
    if not db.ticker_exists(kind, ticker):
        flash(f"نمادی با نام «{ticker}» در پایگاه داده یافت نشد.", "error")
        return redirect(url_for("alerts_page"))
    row = db.create_alert(current_user.id, kind, ticker, rule, threshold,
                          note=request.form.get("note", ""),
                          repeat_mode=repeat_mode)
    if row is None:
        flash("این هشدار از قبل ثبت شده است.", "error")
    else:
        flash(f"هشدار برای «{ticker}» ثبت شد.", "ok")
    return redirect(url_for("alerts_page"))


@app.route("/alerts/<int:alert_id>/toggle", methods=["POST"])
@login_required
def alerts_toggle(alert_id):
    want = (request.form.get("active") or "").strip() == "1"
    ok = db.set_alert_active(alert_id, current_user.id, want)
    return jsonify({"ok": ok, "active": want})


@app.route("/alerts/<int:alert_id>/delete", methods=["POST"])
@login_required
def alerts_delete(alert_id):
    ok = db.delete_alert(alert_id, current_user.id)
    if not request.headers.get("X-Requested-With"):
        flash("هشدار حذف شد." if ok else "هشدار یافت نشد.", "ok" if ok else "error")
        return redirect(url_for("alerts_page"))
    return jsonify({"ok": ok})


@app.route("/update")
def update_page():
    md = _md()
    summary = db.db_summary()
    fresh = md.freshness()
    return render_template("update.html", summary=summary,
                           updater_available=market.UPDATER_AVAILABLE,
                           updater_error=market.UPDATER_ERROR,
                           yesterday=market.yesterday_jalali(),
                           stock_next=market.next_day(summary["stock_latest"]),
                           etf_next=market.next_day(summary["etf_latest"]),
                           fresh=fresh,
                           # «از تاریخ» for each dataset that has its own
                           # calendar, so «افزایشی» starts where that dataset
                           # actually stopped rather than where prices did.
                           ri_stock_next=market.next_day(fresh["ri_stock"]["latest"]),
                           ri_etf_next=market.next_day(fresh["ri_etf"]["latest"]),
                           index_next=market.next_day(fresh["index"]["latest"]),
                           usd_next=market.next_day(fresh["usd"]["latest"]),
                           datasets=market.DATASET_CHOICES,
                           dataset_groups=market.DATASET_GROUPS,
                           status=market.job_status())


@app.route("/update/run", methods=["POST"])
def update_run():
    kind = request.form.get("kind", "stock")
    if kind not in market.RUNNABLE_KINDS:
        flash("نوع دادهٔ نامعتبر.", "error")
        return redirect(url_for("update_page"))
    full = request.form.get("mode") == "full"
    start = (request.form.get("start_date") or "").strip()
    end = (request.form.get("end_date") or "").strip()
    # optional single symbol: blank → update every symbol of this kind
    ticker = (request.form.get("ticker") or "").strip()
    tickers = [ticker] if ticker else None

    # The snapshot datasets have no date range to speak of: Get_MarketWatch
    # returns "right now" and Build_Market_StockList returns "the market as it
    # stands". Sending them a range would put two date pickers on the form that
    # change nothing, so the form omits them and this fills in the bounds the
    # job row still wants for its bookkeeping.
    if not market.DATASET_DATED.get(kind, True):
        start = start or market.yesterday_jalali()
        end = end or market.yesterday_jalali()

    # A per-symbol-day dataset across the whole market is thousands of requests
    # and hours of fetching. It is allowed — «همهٔ نمادها» is a real thing to
    # want — but it has to be ASKED for, because the difference between typing a
    # symbol and not typing one is otherwise invisible until the job is running.
    if kind in market.HEAVY_KINDS and not tickers \
            and request.form.get("heavy_all") != "1":
        flash(f"«{market.kind_label(kind)}» برای هر نماد و هر روز یک درخواست "
              "جداگانه می‌فرستد. یک نماد وارد کنید، یا اگر واقعاً همهٔ نمادها را "
              "می‌خواهید گزینهٔ «همهٔ نمادها» را علامت بزنید.", "error")
        return redirect(url_for("update_page"))
    # «رد کردن نمادهای انجام‌شده» — on by default, so re-running a range after a
    # run that died part-way continues instead of re-downloading what worked.
    # Unchecking it forces every symbol to be fetched again, which is what you
    # want when the window ends today and the prices have since moved.
    resume = request.form.get("refetch") != "1"
    if full:
        # dates are ignored by finpy when ignore_date=False; pass sensible bounds
        start = start or "1380-01-01"
        end = end or market.yesterday_jalali()
    elif not start or not end:
        flash("تاریخ شروع و پایان را وارد کنید.", "error")
        return redirect(url_for("update_page"))
    try:
        job_id = market.start_job(
            kind, start, end, full=full, tickers=tickers, resume=resume,
            created_by=getattr(current_user, "username", None))
        carried = market.job_skipped_count(job_id)
        # The unit belongs to the dataset: «همهٔ نمادها» is right for prices and
        # wrong for a fifty-index sweep or a one-shot snapshot.
        unit = market.kind_unit(kind)
        label = market.kind_label(kind)
        scope = (f"نماد «{ticker}»" if ticker
                 else ("کل سابقه" if full else f"همهٔ {unit}‌ها"))
        msg = (f"دریافت {label} — {scope} — در پس‌زمینه آغاز شد (ممکن است طولانی باشد). "
               "پیشرفت در همین صفحه نمایش داده می‌شود.") if full else \
              (f"به‌روزرسانی {label} — {scope} — در پس‌زمینه آغاز شد. "
               "پیشرفت در همین صفحه نمایش داده می‌شود.")
        if carried:
            msg += (f" {db.to_persian(carried)} {unit} در همین بازه قبلاً دریافت شده بود "
                    "و دوباره دانلود نمی‌شود.")
        flash(msg, "ok")
    except Exception as e:
        flash(f"خطا در شروع به‌روزرسانی: {e}", "error")
    return redirect(url_for("update_page"))


@app.route("/update/retry", methods=["POST"])
def update_retry():
    """Re-run the updater for a hand-picked set of symbols — the ones that failed
    last time. Reuses the previous run's kind / date range / mode so the retry
    targets exactly the same window. Returns JSON so the page can start polling
    the live progress panel without a full reload."""
    data = request.get_json(silent=True) or {}
    tickers = data.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",")]
    tickers = [t for t in tickers if t]
    if not tickers:
        return jsonify({"ok": False, "error": "نمادی برای تلاش دوباره انتخاب نشد."}), 400
    last = market.last_job_params()
    if not last:
        return jsonify({"ok": False, "error": "اجرای قبلی‌ای برای تلاش دوباره یافت نشد."}), 400
    try:
        job_id = market.start_job(
            last["kind"], last["start"], last["end"], full=last["full"],
            tickers=tickers, carry_failed=True,
            created_by=getattr(current_user, "username", None))
        # No clear_cache() here: the retry has not fetched anything yet, and
        # tasks.finalize_update() bumps the cache version when it finishes.
        return jsonify({"ok": True, "count": len(tickers),
                        "kind": last["kind"], "job_id": job_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 409


@app.route("/update/redispatch", methods=["POST"])
def update_redispatch():
    """Re-queue whatever is left of the current job.

    NOT /update/resume — that is the un-pause of «مکث», a different thing. This
    is for a job nothing is working on any more.

    The automatic recovery (tasks.reconcile) is itself a Celery task armed on a
    Redis key, so the one situation it cannot rescue is the one where the worker
    or the broker is the thing that died — and that is exactly when the page sits
    at «۵۰۰ از ۷۸۲» with nothing moving. This runs in the WEB process: it starts
    a local worker if there is none, releases the symbols still marked 'running'
    by a worker that is gone, and dispatches the remainder. market.resume_job_tasks()
    has existed since order 06 and until now had no caller.
    """
    try:
        market.ensure_local_worker()
        res = market.resume_job_tasks()
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if not res:
        return jsonify({"ok": False, "error": "کار فعالی برای ادامه یافت نشد."}), 404
    return jsonify({"ok": True, **res})


@app.route("/update/delete", methods=["POST"])
def update_delete():
    """Delete price-history rows for one symbol (or ALL symbols) inside a Jalali
    from/to range. Returns JSON with the number of rows removed.

    Two switches widen what «حذف» reaches, and they are independent:
      · kind="all"      — both tables, سهام and صندوق‌ها together;
      · all_history=1   — «کلیهٔ سوابق», the entire history rather than a range,
                          in which case the from/to fields are ignored.
    With both set and no ticker, this empties stockpricehistory and
    etfpricehistory. The page asks for a second confirmation before sending
    that; there is nothing to undo it with afterwards."""
    data = request.get_json(silent=True) or {}
    kind = data.get("kind", "stock")
    ticker = (data.get("ticker") or "").strip() or None
    start = (data.get("start_date") or "").strip()
    end = (data.get("end_date") or "").strip()
    all_history = bool(data.get("all_history"))
    md = _md()
    if kind not in ("stock", "etf", "all") and kind not in md.DELETABLE:
        return jsonify({"ok": False, "error": "نوع نامعتبر است."}), 400
    if not all_history:
        if not start or not end:
            return jsonify({"ok": False, "error": "تاریخ «از» و «تا» را وارد کنید."}), 400
        if start > end:
            return jsonify({"ok": False, "error": "تاریخ «از» نباید بعد از «تا» باشد."}), 400

    # The non-price datasets. Routed here rather than through
    # db.delete_price_history because they are not price tables and have their
    # own calendars — see market_data.delete_dataset.
    if kind in md.DELETABLE:
        try:
            n = md.delete_dataset(kind, ticker=ticker, start=start, end=end,
                                  all_history=all_history)
            # No forget_completed() and no analytics rebuild: these tables feed
            # neither the resume marks (which are keyed on price windows) nor the
            # materialized views. The cache bump inside delete_dataset is what
            # the freshness numbers need, and it is all they need.
            return jsonify({"ok": True, "deleted": n, "kind": kind,
                            "ticker": ticker, "all": ticker is None,
                            "all_history": all_history})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    kinds = ("stock", "etf") if kind == "all" else (kind,)
    try:
        deleted = 0
        for k in kinds:
            deleted += db.delete_price_history(k, ticker=ticker, start=start,
                                               end=end, all_history=all_history)
            # The rows are gone, so an earlier run's "already fetched this
            # symbol for this window" is no longer true. Left standing, it makes
            # the next incremental run skip exactly the symbols that were just
            # emptied — see jobs.forget_completed().
            market.forget_completed(k, ticker=ticker, start=start, end=end,
                                    all_history=all_history)
        market.refresh_analytics_async("rows deleted")
        return jsonify({"ok": True, "deleted": deleted, "kind": kind,
                        "ticker": ticker, "all": ticker is None,
                        "all_history": all_history})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/update/status")
def update_status():
    """Progress for the /update page, read from the update_job tables.

    It used to also KICK OFF the analytics refresh when it noticed a finished
    run — a poll endpoint triggering minutes of database work, in whichever of
    the four Gunicorn workers happened to answer that poll. It no longer does:
    tasks.finalize_update() is the tail of the Celery chain and refreshes the
    views exactly once, in the worker, where the run actually ended."""
    st = market.job_status()
    st["refreshing"] = market.analytics_refreshing()
    # Whether the local Celery workers are alive. The page uses it to explain a
    # job that is not moving — "queued, and nothing is listening" is a different
    # problem from "queued, and the worker is busy", and the old page showed
    # «در حال دریافت: …» for both. None where it cannot be known (production,
    # where the workers are separate containers with their own supervisor).
    st["workers"] = market.local_worker_states()
    return jsonify(st)


@app.route("/update/stop", methods=["POST"])
def update_stop():
    """Ask the running job to stop. A single UPDATE on update_job, so it reaches
    the Celery worker regardless of which Gunicorn worker served this request —
    the exact case the old flag file could not handle.

    No refresh is kicked off here: the workers finish the symbol in flight and
    the last one out runs tasks.finalize_update(), which refreshes the analytics
    for the partial run just as it would for a complete one."""
    stopped = market.stop_job()
    # `finished` is the difference between «درخواست ثبت شد» and «تمام شد»: a job
    # with nothing in flight is closed inside this request, so the page can stop
    # polling immediately instead of waiting for a worker to confirm it.
    st = market.job_status()
    return jsonify({"stopped": stopped, "finished": not st.get("running"),
                    "status": st.get("status")})


@app.route("/update/pause", methods=["POST"])
def update_pause():
    paused = market.pause_job()
    return jsonify({"paused": paused})


@app.route("/update/resume", methods=["POST"])
def update_resume():
    resumed = market.resume_job()
    return jsonify({"resumed": resumed})


# ---------------------------------------------------------------------------
# JSON API + Excel export
# ---------------------------------------------------------------------------
@app.route("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    return jsonify(db.search(q) if q else [])


@app.route("/api/market/<kind>")
def api_market(kind):
    """Table data for the Vue island on market.html (order 08).

    Deliberately UNFILTERED. The island fetches the whole list once and does all
    filtering and sorting in the browser, which is the entire point of the
    conversion: the market / group / sub-group selects used to be a full page
    reload each. db.market_gainer() with no filters is exactly the shared cached
    scan order 00 introduced and order 02 turned into a materialized view, so
    this endpoint is a Redis read plus a JSON dump — the filtered variants would
    each be a slice of the same cached list anyway.

    The sub-group options are NOT sent as a separate list. They are derivable
    from the rows (every row carries sector and sub_sector), so the client can
    cascade the two dropdowns without the round trip db.stock_sub_sectors() used
    to need."""
    if kind not in ("stock", "etf"):
        abort(404)
    as_of_arg = request.args.get("as_of") or None

    t0 = time.perf_counter()
    rows, as_of = db.market_gainer(kind, as_of=as_of_arg)
    calc_rows, _ = db.period_gainer(kind, as_of=as_of_arg)
    elapsed = (time.perf_counter() - t0) * 1000.0

    watched = sorted(db.watch_keys(current_user.id)) if current_user.is_authenticated else []

    return jsonify({
        "kind": kind,
        "as_of": as_of,
        "rows": rows,
        "calc_rows": calc_rows,
        "periods": db.PERIODS,
        "calc_periods": db.CALC_PERIODS,
        "etf_type_colors": db.ETF_TYPE_COLORS,
        # The star state, so the island renders pre-filled stars exactly as the
        # inject_watchlist context processor does for the Jinja pages.
        "watched": watched,
        "server_ms": round(elapsed, 1),
    })


@app.route("/api/ohlc/<kind>/<int:entity_id>")
def api_ohlc(kind, entity_id):
    """Adjusted OHLCV candle history for the professional chart + history table."""
    if kind == "stock":
        ent = db.get_stock(entity_id)
    elif kind == "etf":
        ent = db.get_etf(entity_id)
    else:
        abort(404)
    if not ent:
        abort(404)
    candles = db.ohlc_history(kind, ent["ticker"])
    return jsonify({"ticker": ent["ticker"], "name": ent["name"], "candles": candles})


@app.route("/export/<kind>.xlsx")
def export_gainer(kind):
    if kind not in ("stock", "etf"):
        abort(404)
    if kind == "stock":
        rows, as_of = db.market_gainer(
            "stock", market=request.args.get("market") or None,
            sector=request.args.get("group") or None,
            sub_sector=request.args.get("subgroup") or None)
        title, fname = "بازدهی سهام", "stocks_gainer"
    else:
        rows, as_of = db.market_gainer("etf", etf_type=request.args.get("type") or None)
        title, fname = "بازدهی صندوق‌ها", "etfs_gainer"
    data = reports.gainer_workbook(rows, title, as_of)
    return send_file(io.BytesIO(data),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"{fname}_{as_of}.xlsx")


if __name__ == "__main__":
    # Development entry point ONLY. In production the app is served by Gunicorn
    # (see gunicorn.conf.py / docker-compose.yml), which imports `app` from this
    # module and never runs this block.
    #
    # debug is now OFF unless FLASK_DEBUG is set explicitly. It used to be
    # hard-coded True, which exposes the Werkzeug interactive debugger — a
    # remote code execution hole on anything reachable from outside localhost.
    debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes")
    host = os.environ.get("DEV_HOST", "127.0.0.1")
    # DEV_PORT stays the project's own knob and still wins. PORT is the fallback
    # because it is what every host that assigns a port injects — container
    # platforms, PaaS runners, and the editor's preview launcher — and without
    # it a second copy of the app (a git worktree, say) collides with whichever
    # instance already holds 5002.
    port = int(os.environ.get("DEV_PORT") or os.environ.get("PORT") or "5002")

    # Werkzeug prints " * Running on http://… " (and "Press CTRL+C to quit")
    # through the `werkzeug` logger at INFO, but observability.setup_logging()
    # pins that logger to WARNING because its per-request lines duplicate our
    # own JSON access log. That silences the clickable URL along with them, so
    # print it here instead of loosening the log level: terminals turn it into a
    # link, and it is how you actually open the app.
    #
    # 0.0.0.0 / :: are bind addresses, not destinations — show a loopback
    # address you can click. WERKZEUG_RUN_MAIN is set in the reloader's child
    # process, so the guard keeps FLASK_DEBUG=1 from printing this twice.
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        print(f" * Running on http://{shown}:{port}  (Ctrl+C to quit)", flush=True)

    # …and open it. On a background thread gated on the port actually accepting,
    # so the tab opens onto a served page rather than a connection error. Set
    # BN_OPEN_BROWSER=0 to keep the terminal-only behaviour.
    dev_boot.open_browser_when_ready(host, port)

    app.run(debug=debug, host=host, port=port)
