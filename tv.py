"""
tv.py — دیتافید TradingView Advanced Charts برای داده‌های بورس تهران
A UDF-compatible datafeed (the protocol TradingView's `UDFCompatibleDatafeed`
speaks) served from the same PostgreSQL database the rest of the platform uses.

Endpoints (mounted under /tv):
    GET /tv/config      → datafeed capabilities
    GET /tv/symbols     → resolve one symbol  (?symbol=TSE:خودرو)
    GET /tv/search      → symbol search       (?query=&type=&exchange=&limit=)
    GET /tv/history     → OHLCV bars           (?symbol=&resolution=&from=&to=&countback=)
    GET /tv/time        → server time (unix seconds)

Symbols are encoded EXCHANGE:TICKER, where the exchange picks the table:
    TSE      → stocks   (stockpricehistory)
    TSEETF   → ETFs / صندوق‌ها (etfpricehistory)

Only daily data exists in the DB, so weekly/monthly bars are resampled here.
"""
import time

from flask import Blueprint, request, jsonify

import db

tv = Blueprint("tv", __name__, url_prefix="/tv")

# We advertise (and serve) these resolutions. Everything is built from daily.
SUPPORTED_RESOLUTIONS = ["1D", "1W", "1M"]

EXCH_STOCK = "TSE"
EXCH_ETF = "TSEETF"


def _split_symbol(symbol):
    """'TSE:خودرو' → ('stock', 'خودرو'); bare 'خودرو' assumes a stock."""
    symbol = (symbol or "").strip()
    if ":" in symbol:
        exch, ticker = symbol.split(":", 1)
    else:
        exch, ticker = EXCH_STOCK, symbol
    kind = "etf" if exch.upper() == EXCH_ETF else "stock"
    return kind, ticker.strip()


def _exch_for_kind(kind):
    return EXCH_ETF if kind == "etf" else EXCH_STOCK


# ---------------------------------------------------------------------------
# /config — what this datafeed can do
# ---------------------------------------------------------------------------
@tv.route("/config")
def config():
    return jsonify({
        "supported_resolutions": SUPPORTED_RESOLUTIONS,
        "supports_search": True,
        "supports_group_request": False,
        "supports_marks": False,
        "supports_timescale_marks": False,
        "supports_time": True,
        "exchanges": [
            {"value": "", "name": "همه بازارها", "desc": "All"},
            {"value": EXCH_STOCK, "name": "بورس تهران", "desc": "Tehran Stock Exchange"},
            {"value": EXCH_ETF, "name": "صندوق‌ها", "desc": "ETFs"},
        ],
        "symbols_types": [
            {"name": "همه", "value": ""},
            {"name": "سهام", "value": "stock"},
            {"name": "صندوق", "value": "fund"},
        ],
    })


@tv.route("/time")
def server_time():
    return str(int(time.time())), 200, {"Content-Type": "text/plain"}


# ---------------------------------------------------------------------------
# /search — used by the symbol-search box in the chart
# ---------------------------------------------------------------------------
@tv.route("/search")
def search():
    query = (request.args.get("query") or "").strip()
    sym_type = (request.args.get("type") or "").strip()
    exchange = (request.args.get("exchange") or "").strip().upper()
    try:
        limit = int(request.args.get("limit") or 30)
    except ValueError:
        limit = 30

    rows = db.search(query, limit=limit) if query else []
    out = []
    for r in rows:
        kind = r.get("kind")
        tv_type = "fund" if kind == "etf" else "stock"
        exch = _exch_for_kind(kind)
        if sym_type and sym_type != tv_type:
            continue
        if exchange and exchange != exch:
            continue
        out.append({
            "symbol": r["ticker"],
            "full_name": f"{exch}:{r['ticker']}",
            "description": r.get("name") or r["ticker"],
            "exchange": exch,
            "ticker": f"{exch}:{r['ticker']}",
            "type": tv_type,
        })
    return jsonify(out[:limit])


# ---------------------------------------------------------------------------
# /symbols — resolve one symbol to a full SymbolInfo record
# ---------------------------------------------------------------------------
@tv.route("/symbols")
def symbols():
    symbol = request.args.get("symbol") or ""
    kind, ticker = _split_symbol(symbol)
    exch = _exch_for_kind(kind)
    name = db.name_for_ticker(kind, ticker)
    return jsonify({
        "name": ticker,
        "ticker": f"{exch}:{ticker}",
        "full_name": f"{exch}:{ticker}",
        "description": name,
        "type": "fund" if kind == "etf" else "stock",
        "session": "24x7",
        "exchange": exch,
        "listed_exchange": exch,
        "timezone": "Asia/Tehran",
        "minmov": 1,
        "pricescale": 100,          # adjusted prices carry up to 2 decimals
        "has_intraday": False,
        "has_daily": True,
        "has_weekly_and_monthly": True,
        "supported_resolutions": SUPPORTED_RESOLUTIONS,
        "volume_precision": 0,
        "data_status": "endofday",
        "visible_plots_set": "ohlcv",
        "currency_code": "IRR",
    })


# ---------------------------------------------------------------------------
# /history — the actual OHLCV bars
# ---------------------------------------------------------------------------
def _resample(daily, unit):
    """Aggregate ascending daily bars into weekly ('W') or monthly ('M') bars."""
    import datetime as _dt
    buckets = {}
    order = []
    for b in daily:
        d = _dt.datetime.utcfromtimestamp(b["t"])
        if unit == "W":
            iso = d.isocalendar()          # (year, week, weekday)
            key = (iso[0], iso[1])
        else:  # 'M'
            key = (d.year, d.month)
        if key not in buckets:
            buckets[key] = {"t": b["t"], "o": b["o"], "h": b["h"],
                            "l": b["l"], "c": b["c"], "v": b["v"]}
            order.append(key)
        else:
            agg = buckets[key]
            agg["h"] = max(agg["h"], b["h"])
            agg["l"] = min(agg["l"], b["l"])
            agg["c"] = b["c"]              # last close wins
            agg["v"] += b["v"]
    return [buckets[k] for k in order]


@tv.route("/history")
def history():
    symbol = request.args.get("symbol") or ""
    resolution = (request.args.get("resolution") or "1D").upper()
    kind, ticker = _split_symbol(symbol)

    daily = db.tv_bars(kind, ticker)
    if not daily:
        return jsonify({"s": "no_data"})

    if resolution in ("1W", "W", "W1"):
        bars = _resample(daily, "W")
    elif resolution in ("1M", "M", "M1"):
        bars = _resample(daily, "M")
    else:
        bars = daily

    # Time window / countback (TradingView sends one or the other).
    def _int(name):
        try:
            return int(float(request.args.get(name)))
        except (TypeError, ValueError):
            return None

    frm, to, countback = _int("from"), _int("to"), _int("countback")

    if countback is not None and to is not None:
        window = [b for b in bars if b["t"] <= to][-countback:]
    else:
        lo = frm if frm is not None else float("-inf")
        hi = to if to is not None else float("inf")
        window = [b for b in bars if lo <= b["t"] <= hi]

    if not window:
        # Tell TradingView where the previous data is, so it stops paging.
        earlier = [b["t"] for b in bars if to is None or b["t"] <= to]
        resp = {"s": "no_data"}
        if earlier:
            resp["nextTime"] = earlier[-1]
        return jsonify(resp)

    return jsonify({
        "s": "ok",
        "t": [b["t"] for b in window],
        "o": [b["o"] for b in window],
        "h": [b["h"] for b in window],
        "l": [b["l"] for b in window],
        "c": [b["c"] for b in window],
        "v": [b["v"] for b in window],
    })
