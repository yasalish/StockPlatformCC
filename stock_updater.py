"""
stock_updater.py — به‌روزرسانی قیمت سهام

Since order 06 this module is a THIN WRAPPER. The fetch-and-store logic it used
to own now lives in tse_fetch.py, shared with etf_updater and with the Celery
tasks, because the two updaters were near-identical copies of each other and had
already drifted once. The old copy also had two properties that made it unusable
under a task queue: a bare INSERT that duplicated rows when re-run, and failures
reported as printed text and a counter rather than raised exceptions.

The command-line entry point below is kept so the module can still be run by
hand; the web application reaches this code through tasks.fetch_batch instead.
"""
import tse_fetch

KIND = "stock"


def fetch_tickers():
    """[(entity_id, ticker)] from the reference table."""
    return tse_fetch.reference_tickers(KIND)


def insert_price_history(entity_id, price_history, ticker=None, full=False):
    """Idempotent write for one symbol. Returns True/False for compatibility
    with the old signature; tse_fetch.store() returns the row count."""
    ticker = ticker or (price_history["Ticker"].iloc[0]
                        if "Ticker" in price_history.columns else None)
    try:
        tse_fetch.store(KIND, entity_id, ticker, price_history, full=full)
        return True
    except tse_fetch.FetchError as e:
        print(f"Error inserting price history for {ticker}: {e}")
        return False


def update_stock_prices(start_date, end_date=None, full=False, only=None):
    """Update every stock (or just `only`) in one process, sequentially.

    This is the legacy path, kept for `python stock_updater.py` and for a machine with no
    Redis. The production path is Celery: market.start_job() creates a job row
    per symbol and tasks.fetch_batch processes them with retries, so a crash
    resumes instead of starting over.

    Returns (success, failure, total), unchanged.
    """
    if end_date is None:
        end_date = start_date
    work = fetch_tickers()
    if only:
        wanted = {str(t).strip() for t in only}
        work = [(i, t) for i, t in work if t in wanted]
    if not work:
        print("No tickers found.")
        return 0, 0, 0

    ok = bad = 0
    for entity_id, ticker in work:
        print(f"Fetching price history for ticker: {ticker}")
        try:
            rows = tse_fetch.fetch_and_store(KIND, entity_id, ticker,
                                             start_date, end_date, full=full)
            print(f"Inserted {rows} rows for {ticker}.")
            ok += 1
        except tse_fetch.FetchError as e:
            # Printed AND counted as a failure — never a silent zero.
            print(f"Failed {ticker}: {e.reason} — {e}")
            bad += 1
    print(f"Update completed. Success: {ok}, Failed: {bad}, Total: {len(work)}")
    return ok, bad, len(work)


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args:
        print("usage: python stock_updater.py <start_jalali> [end_jalali] [full]")
        raise SystemExit(2)
    update_stock_prices(args[0], args[1] if len(args) > 1 else None,
           full=("full" in args))
