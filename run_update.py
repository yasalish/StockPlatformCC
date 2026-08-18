"""
run_update.py — راه‌اندازی دستی کار به‌روزرسانی
Command-line entry point for the market-data update.

WHAT CHANGED IN ORDER 06

This file used to be the worker itself: market.start_job() spawned it with
subprocess.Popen, it imported stock_updater/etf_updater and looped over every
ticker in one process, writing progress to a log the web process re-parsed. Its
whole reason for existing was that finpy_tse needs the Windows selector event
loop in the main thread of its own process — a constraint that a Celery worker
satisfies just as well, and with retries, history and resumability on top.

So it is now a thin CLI that ENQUEUES a job and returns. The work happens in the
Celery worker.

    python run_update.py stock 1404-01-01 1404-01-10
    python run_update.py etf   1404-01-01 1404-01-10 full
    python run_update.py stock 1404-01-01 1404-01-10 --tickers syms.txt
    python run_update.py stock 1404-01-01 1404-01-10 --local   # no Celery

--local runs the old sequential path in this process. It exists for a machine
with no Redis and for debugging a single symbol; it has no retries and no resume,
which is precisely why it is not the default any more.
"""
import sys


def main():
    args = sys.argv[1:]
    if len(args) < 3:
        print(__doc__.strip())
        return 2

    kind, start, end = args[0], args[1], args[2]
    if kind not in ("stock", "etf"):
        print(f"RESULT error=bad_kind: {kind!r} (expected stock or etf)")
        return 1

    rest = args[3:]
    full = "full" in rest
    local = "--local" in rest

    only = None
    if "--tickers" in rest:
        try:
            path = rest[rest.index("--tickers") + 1]
            with open(path, encoding="utf-8") as f:
                only = [ln.strip() for ln in f if ln.strip()]
        except (IndexError, OSError) as e:
            print(f"RESULT error=bad_tickers_file: {e}")
            return 1

    if local:
        return _run_locally(kind, start, end, full, only)

    try:
        import market
        job_id = market.start_job(kind, start, end, full=full, tickers=only,
                                  source="cli")
    except Exception as e:
        print(f"RESULT error={type(e).__name__}: {e}")
        return 1

    print(f"RESULT queued job_id={job_id} kind={kind} {start}..{end} "
          f"full={full} tickers={len(only) if only else 'all'}")
    print("Follow it on /update, or:  "
          "python -c \"import jobs,json;print(json.dumps(jobs.snapshot(),"
          "ensure_ascii=False,default=str,indent=2))\"")
    return 0


def _run_locally(kind, start, end, full, only):
    """The pre-Celery path: one process, sequential, no retries, no resume."""
    import asyncio
    # finpy_tse uses asyncio/aiohttp; on Windows it needs the selector policy in
    # the main thread of its own process or the second fetch fails with
    # "Event loop is closed". The Celery worker inherits the same requirement,
    # which is why the worker service runs with a solo/threads pool.
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        if kind == "stock":
            import stock_updater
            ok, bad, total = stock_updater.update_stock_prices(
                start, end, full=full, only=only)
        else:
            import etf_updater
            ok, bad, total = etf_updater.update_etf_prices(
                start, end, full=full, only=only)
        print(f"RESULT ok={ok} fail={bad} total={total}")
        return 0
    except Exception as e:
        print(f"RESULT error={type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
