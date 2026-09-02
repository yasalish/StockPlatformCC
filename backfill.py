"""
backfill.py — دریافت کامل همهٔ داده‌ها، به ترتیب درست

    python backfill.py                    # همهٔ مجموعه‌داده‌های عملی، افزایشی
    python backfill.py --full             # کل سابقه، نه فقط از آخرین تاریخ
    python backfill.py --list             # فقط بگو چه‌کار می‌خواهی بکنی
    python backfill.py --only index,usd   # فقط این‌ها
    python backfill.py --skip stock,etf   # همه جز این‌ها
    python backfill.py --symbol فولاد --intraday --from 1405-05-01 --to 1405-06-09

WHY THIS EXISTS

The /update page runs ONE job at a time, on purpose — two fetches competing for
TSETMC connections is the main source of the timeouts that look like "no data".
Filling an empty database therefore means starting thirteen jobs in the right
order and waiting for each, which is not a thing to do by hand.

THE ORDER IS NOT ARBITRARY

  1. symbols   FIRST, always. It refreshes the `stocks` reference table, and
               every per-symbol job below iterates over that table — so a
               symbol added here is fetched by everything that follows, and a
               symbol missing here is invisible to all of them.
  2. usd       one request.
  3. index     fifty requests (10 market + 40 sector indices).
  4. watch     one request for the WHOLE market. Run it after the close for the
               end-of-day queue values to be meaningful.
  5. prices    the foundation everything else joins against. Only with
               --with-prices: on an existing install these are already current,
               and re-fetching 810 symbols to confirm that is an hour wasted.
  6. ri        حقیقی/حقوقی, per symbol.
  7. shareholders   per symbol.

WHAT THIS DELIBERATELY WILL NOT DO

The three intraday datasets — سابقهٔ صف, عمق بازار درون‌روز, ریز معاملات — are
ONE REQUEST PER SYMBOL-DAY. Measured on this machine: 1.7 s per symbol-day. For
810 symbols over one year that is

    810 × 240 × 1.7 s  ≈  92 hours of continuous fetching

…which is not a back-fill, it is a fortnight. They are per-symbol tools, so
this script only runs them when you name a symbol AND pass --intraday. That is
not a safety rail you should route around; it is what the numbers say.

RESUMABLE

Every job is itself resumable (jobs.already_done_elsewhere), and this script
adds nothing to that. Kill it with Ctrl+C and the job it was watching keeps
running in the background — the workers are detached. Run it again afterwards
and it picks up from whatever is already stored.
"""
import argparse
import os
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db                                              # noqa: E402
import jobs                                            # noqa: E402
import market                                          # noqa: E402
import market_data                                     # noqa: E402
import tse_fetch                                       # noqa: E402

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
_TTY = sys.stdout.isatty()
#: A bare carriage return, named rather than inlined so the progress line
#: cannot be broken by a tool that rewrites this file.
CR = chr(13)


def fa(x):
    return str(x).translate(FA)


# ---------------------------------------------------------------------------
# The plan
#
# `est` is seconds per work item, taken from this platform's own finished jobs
# rather than guessed: the per-symbol fetches have measured between 2.6 s and
# 20 s depending on how TSETMC is feeling, and 6 s is the middle of that. It is
# used only to print an estimate before starting, so being wrong by a factor of
# two costs a wrong sentence, not a wrong run.
# ---------------------------------------------------------------------------
PLAN = [
    ("symbols",      "فهرست مرجع نمادها", 180, False),
    ("usd",          "قیمت دلار آزاد",      5, False),
    ("index",        "شاخص‌ها",             4, False),
    ("watch",        "دیده‌بان و عمق بازار", 8, False),
    ("stock",        "قیمت سهام",           6, True),
    ("etf",          "قیمت صندوق‌ها",       6, True),
    ("stock_ri",     "حقیقی/حقوقی سهام",    6, False),
    ("etf_ri",       "حقیقی/حقوقی صندوق‌ها", 6, False),
    ("shareholders", "سهامداران عمده",      4, False),
]

#: Only ever run for a named symbol — see the module docstring.
INTRADAY = [
    ("stock_queue",  "سابقهٔ صف"),
    ("stock_ob",     "عمق بازار درون‌روز"),
    ("stock_trades", "ریز معاملات"),
]


def work_size(kind):
    """How many items this job will have, without creating it."""
    try:
        return len(tse_fetch.reference_tickers(kind))
    except Exception:
        return 0


#: Where a per-symbol dataset's coverage is measured. "How many symbols have
#: ANY row" is the question; the newest date cannot answer it.
_COVERAGE = {
    "ri":              ("ri_history", "kind"),
    "queue":           ("queue_history", "kind"),
    "intraday_ob":     ("intraday_orderbook", None),
    "intraday_trades": ("intraday_trades", None),
    "shareholders":    ("shareholders", None),
    "price":           (None, None),      # covered by the price tables already
}


def coverage(kind):
    """(symbols_with_any_row, symbols_in_the_reference_list), or None.

    THIS IS THE NUMBER THAT MATTERS FOR A BACK-FILL, and it is not the one the
    incremental logic uses. `market.dataset_latest()` returns MAX(j_date), so a
    table holding ONE symbol fetched to yesterday reports itself as fully
    up to date — and an incremental run then computes an empty window and
    fetches nothing, for ever, while 809 symbols stay empty.

    That is correct behaviour for the nightly job (it keeps what exists
    current) and exactly wrong for this script, whose whole purpose is to fill
    the table. So the window below is chosen from coverage, not from recency.
    """
    spec = tse_fetch.KINDS.get(kind, {})
    table, kind_col = _COVERAGE.get(spec.get("dataset"), (None, None))
    if not table:
        return None
    total = work_size(kind)
    if not total:
        return None
    try:
        if kind_col:
            row = db._one(f"SELECT COUNT(DISTINCT ticker) n FROM {table} "
                          f"WHERE {kind_col} = %s",
                          (spec.get("for_kind", "stock"),))
        else:
            row = db._one(f"SELECT COUNT(DISTINCT ticker) n FROM {table}")
        return int((row or {}).get("n") or 0), total
    except Exception:
        return 0, total


#: Below this share of the reference list, a dataset is treated as unfilled and
#: the window is widened to cover every symbol rather than the last few days.
#: Not 100 %: symbols that genuinely have no data (halted, newly listed, the
#: junk «----» row in `etf`) never arrive, and demanding perfection would widen
#: the window on every run for ever.
COVERAGE_FLOOR = 0.90


def human(seconds):
    if seconds < 90:
        return f"{fa(int(seconds))} ثانیه"
    if seconds < 5400:
        return f"{fa(round(seconds / 60))} دقیقه"
    return f"{fa(round(seconds / 3600, 1))} ساعت"


#: How far back a widened window reaches. The price tables are the floor of
#: what any other dataset can usefully join against, so there is no point
#: asking TSETMC for حقیقی/حقوقی from before the earliest price bar.
def _earliest_price_jdate(kind):
    tbl = ("stockpricehistory"
           if tse_fetch.KINDS.get(kind, {}).get("for_kind", "stock") == "stock"
           else "etfpricehistory")
    try:
        r = db._one(f"SELECT MIN(j_date) d FROM {tbl}")
        return (r or {}).get("d") or market.FIRST_JALALI
    except Exception:
        return market.FIRST_JALALI


def window_for(kind, full, override_from=None, override_to=None):
    """(start, end, why) for this dataset.

    Three cases, in priority order:

      1. an explicit --from wins, always;
      2. --full, or a dataset whose COVERAGE is below the floor, gets a window
         wide enough to reach every symbol — because "the newest row is from
         yesterday" says nothing about the 809 symbols that have no rows at all
         (see coverage());
      3. otherwise the ordinary incremental window off this dataset's OWN
         table. Not off the price table: حقیقی/حقوقی can legitimately be a month
         behind prices, and starting where prices stopped asks for a window this
         dataset never covered — storing nothing and leaving the real gap open.
    """
    end = override_to or market.yesterday_jalali()
    if override_from:
        return override_from, end, "بازهٔ دستی"
    if full:
        return _earliest_price_jdate(kind), end, "کل سابقه"

    cov = coverage(kind)
    if cov and cov[1] and cov[0] < cov[1] * COVERAGE_FLOOR:
        return (_earliest_price_jdate(kind), end,
                f"پوشش {fa(cov[0])} از {fa(cov[1])} نماد → دریافت کامل")

    latest = market.dataset_latest(kind)
    return market.next_day(latest), end, "افزایشی"


def wait_for_job(job_id, label, poll=5):
    """Block until the job stops RUNNING, printing progress as it goes.

    Ctrl+C here abandons the WATCH, not the job: the workers are detached, so
    the fetch carries on and the next run of this script picks it up."""
    last = None
    started = time.time()
    while True:
        st = market.job_status()
        # `active` is NOT the field to wait on: jobs.snapshot() hard-codes it
        # True whenever there is a job to report at all, so the /update page
        # can keep showing the result panel after a run ends. `running` is the
        # one that tracks queued/running/stopping/finalizing — waiting on
        # `active` waits for ever on a job that finished in nine seconds.
        if st.get("job_id") != job_id or not st.get("running"):
            break
        line = (f"    {fa(st.get('processed', 0))}/{fa(st.get('total', 0))}"
                f"  موفق {fa(st.get('success', 0))}"
                f"  ناموفق {fa(st.get('failed', 0))}"
                f"  ({human(time.time() - started)})")
        if st.get("current"):
            line += f"  ← {st['current']}"
        if line != last:
            # A carriage return only redraws on a terminal. Piped to a file it just appends,
            # turning the progress line into one unreadable stream — so when
            # stdout is not a tty this prints a real line, and only when the
            # COUNT changed rather than on every tick.
            if _TTY:
                print(line.ljust(96), end=CR, flush=True)
            elif last is None or line.split("(")[0] != last.split("(")[0]:
                print(line, flush=True)
            last = line
        # A stalled run is worth saying out loud rather than spinning on.
        if st.get("stalled"):
            print(f"\n    ⚠ این کار متوقف مانده است — صفحهٔ «به‌روزرسانی» را باز کنید "
                  f"و «ادامه از جایی که مانده» را بزنید.")
        time.sleep(poll)
    counts = jobs.summary_counts(job_id)
    print(f"    ✔ {label}: موفق {fa(counts.get('ok', 0))}"
          f" · ناموفق {fa(counts.get('failed', 0))}"
          f" · رد‌شده {fa(counts.get('skipped', 0))}"
          f"  در {human(time.time() - started)}".ljust(96))
    return counts


def wait_until_free(timeout=7200):
    """Wait out whatever job is already running before starting the next one."""
    waited = 0
    while jobs.blocking_job_id() and waited < timeout:
        if waited == 0:
            print("    … منتظر پایان کار در حال اجرا")
        time.sleep(5)
        waited += 5
    return jobs.blocking_job_id() is None


def run_one(kind, label, full=False, tickers=None, start=None, end=None,
            dry=False):
    lo, hi, why = window_for(kind, full, start, end)
    dated = market.DATASET_DATED.get(kind, True)
    scope = f"نماد {tickers[0]}" if tickers else f"{fa(work_size(kind))} مورد"
    span = f"{lo} تا {hi} — {why}" if dated else "عکس لحظه‌ای"
    print(f"\n▸ {label}  ({scope}, {span})")

    if dry:
        print("    (--dry-run — اجرا نشد)")
        return None
    if dated and lo > hi:
        print("    ✔ به‌روز است — چیزی برای دریافت نیست")
        return None
    if not wait_until_free():
        print("    ✖ کار دیگری هنوز در حال اجراست؛ رها شد")
        return None
    try:
        job_id = market.start_job(kind, lo, hi, full=full, tickers=tickers,
                                  source="backfill", created_by="backfill")
    except RuntimeError as exc:
        print(f"    ✖ شروع نشد: {exc}")
        return None
    return wait_for_job(job_id, label)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--full", action="store_true",
                    help="کل سابقه به‌جای دریافت افزایشی")
    ap.add_argument("--with-prices", action="store_true",
                    help="قیمت سهام و صندوق‌ها را هم دوباره بگیر")
    ap.add_argument("--only", default="", help="فقط این نوع‌ها (با کاما)")
    ap.add_argument("--skip", default="", help="این نوع‌ها را رد کن (با کاما)")
    ap.add_argument("--symbol", default="", help="فقط یک نماد")
    ap.add_argument("--intraday", action="store_true",
                    help="داده‌های درون‌روز — فقط با --symbol")
    ap.add_argument("--from", dest="lo", default="", help="از تاریخ شمسی")
    ap.add_argument("--to", dest="hi", default="", help="تا تاریخ شمسی")
    ap.add_argument("--list", action="store_true", help="فقط برنامه را نشان بده")
    ap.add_argument("--dry-run", action="store_true", help="اجرا نکن")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    tickers = [args.symbol.strip()] if args.symbol.strip() else None

    market_data.ensure_tables()
    jobs.ensure_tables()

    plan = [(k, lbl, est) for k, lbl, est, is_price in PLAN
            if (args.with_prices or not is_price)
            and (not only or k in only) and k not in skip]

    if args.intraday:
        if not tickers:
            print("✖ داده‌های درون‌روز فقط برای یک نماد گرفته می‌شود.")
            print("  برای هر نماد و هر روز یک درخواست جداگانه است — کل بازار برای")
            print("  یک سال حدود ۹۲ ساعت دریافت پیوسته می‌شود.")
            print("  مثال:  python backfill.py --symbol فولاد --intraday "
                  "--from 1405-05-01 --to 1405-06-09")
            return 2
        plan += [(k, lbl, 2) for k, lbl in INTRADAY
                 if (not only or k in only) and k not in skip]

    if not plan:
        print("چیزی برای اجرا نیست.")
        return 0

    print("=" * 72)
    print("برنامهٔ دریافت" + (f" — فقط نماد {tickers[0]}" if tickers else ""))
    print("=" * 72)
    total_est = 0
    for kind, label, est in plan:
        n = 1 if tickers else work_size(kind)
        lo, hi, why = window_for(kind, args.full, args.lo, args.hi)
        dated = market.DATASET_DATED.get(kind, True)
        # An inverted window means the dataset is already current, so the job
        # will do nothing — and counting it would inflate the one number
        # this listing exists to give.
        idle = dated and lo > hi
        secs = 0 if idle else n * est
        total_est += secs
        span = ("به‌روز است" if idle
                else (f"{lo}..{hi}" if dated else "عکس لحظه‌ای"))
        cov = coverage(kind)
        cov_txt = ""
        if cov and not tickers:
            cov_txt = f"  [پوشش {fa(cov[0])}/{fa(cov[1])}]"
        est_txt = "—" if idle else human(secs)
        print(f"  {label:<24} {fa(n):>6} مورد  ≈ {est_txt:<12} {span}{cov_txt}")
        if not idle and dated and why.startswith("پوشش"):
            print(f"  {'':<24} └─ {why}")
    print("-" * 72)
    print(f"  برآورد کل: ≈ {human(total_est)}")
    print("  (برآورد بر پایهٔ سرعت واقعی کارهای قبلی همین سامانه است؛ "
          "سرعت TSETMC ثابت نیست.)")
    print("=" * 72)

    if args.list:
        return 0
    if not args.dry_run:
        print("\nهر کار در پس‌زمینه اجرا می‌شود و این اسکریپت منتظرش می‌ماند.")
        print("Ctrl+C فقط تماشا را قطع می‌کند — خودِ دریافت ادامه می‌یابد.\n")

    results = {}
    for kind, label, _est in plan:
        try:
            results[label] = run_one(
                kind, label, full=args.full, tickers=tickers,
                start=args.lo or None, end=args.hi or None, dry=args.dry_run)
        except KeyboardInterrupt:
            print("\n\n⏹ تماشا قطع شد. کار در پس‌زمینه ادامه دارد — "
                  "صفحهٔ «به‌روزرسانی» را ببینید یا این اسکریپت را دوباره اجرا کنید.")
            return 130

    if args.dry_run:
        return 0
    print("\n" + "=" * 72)
    print("پوشش داده پس از اجرا")
    print("=" * 72)
    fresh = market_data.freshness()
    summary = db.db_summary()
    rows = [("قیمت سهام", summary["stock_latest"], summary["stock_rows"]),
            ("قیمت صندوق‌ها", summary["etf_latest"], summary["etf_rows"]),
            ("حقیقی/حقوقی سهام", fresh["ri_stock"]["latest"], None),
            ("حقیقی/حقوقی صندوق‌ها", fresh["ri_etf"]["latest"], None),
            ("شاخص‌ها", fresh["index"]["latest"], fresh["index"]["rows"]),
            ("دلار آزاد", fresh["usd"]["latest"], fresh["usd"]["rows"]),
            ("دیده‌بان بازار", fresh["watch"]["latest"], fresh["watch"]["rows"]),
            ("سهامداران عمده", fresh["shareholders"]["latest"],
             fresh["shareholders"]["rows"]),
            ("سابقهٔ صف", fresh["queue"]["latest"], fresh["queue"]["rows"]),
            ("عمق بازار درون‌روز", fresh["intraday_ob"]["latest"],
             fresh["intraday_ob"]["rows"]),
            ("ریز معاملات", fresh["intraday_trades"]["latest"],
             fresh["intraday_trades"]["rows"])]
    for name, latest, n in rows:
        mark = "  " if latest else "✖ "
        cnt = f"{n:,}".translate(FA) if n else "—"
        print(f"  {mark}{name:<24} {fa(latest or 'هرگز'):<14} {cnt:>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
