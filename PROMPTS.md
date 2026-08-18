# BourseNegar — Work Orders

Nine prompts in dependency order. Each is self-contained: paste one into a fresh
Claude Code session without re-explaining the project.

Baselines measured 2026-08-14 against the live Stock database.

---

## Order 00 — Fix the navigation bugs

**~1 hour** · no prerequisites

> The single highest ratio of user-visible improvement to effort in the whole plan. No schema change, no new dependency, no infrastructure.

```
Work in C:\Users\Yasmine\Desktop\StockPlatform_Claude — a Flask + PostgreSQL
Persian/RTL stock analytics platform. Fix four performance bugs. Do NOT change the
database schema, add dependencies, or alter any analytics maths.

1. Filter clicks are guaranteed cache misses (db.py, market_gainer)
The cache key is (kind, as_of, market, etf_type, sector, sub_sector), but warm_cache()
precomputes only the unfiltered case. Measured: unfiltered = 0 ms once cached, but
?market=... takes 5,289 ms and ?group=... takes 5,231 ms to return 5 rows.
There are ~288 filter combinations reachable from the UI; exactly 1 is warmed.

The key insight: the SQL inside _gainer() is NOT parameterised by market, sector,
sub_sector or etf_type — those are applied in a Python loop AFTER the full table scan,
so the query is byte-identical for all 288 combinations. One cached unfiltered result
can serve every one of them.

Refactor so the expensive scan is cached once per (kind, as_of), and market_gainer()
filters that cached list in memory. Keep the public signature and the (rows, as_of)
return shape exactly as they are. Ensure clear_cache() still invalidates correctly.
Check whether _perf_matrix / the strategy, filter and score scans have the same
filter-in-the-cache-key problem, and fix them the same way if so.

2. The font blocks rendering on a foreign CDN (templates/base.html)
Vazirmatn is pulled from cdn.jsdelivr.net as a render-blocking stylesheet in <head>.
The users are in Iran, where that CDN is unreliable. Download the woff2 files into
static/fonts/, add a local @font-face block to static/css/style.css, and remove both
the preconnect hint and the CDN <link>. Keep the exact same font family and weights.

3. Static assets are forbidden from caching (app.py, _no_cache)
It sets "Cache-Control: no-store" on /static/ as well as HTML. no-store forbids even a
304, so ~45 kB re-downloads on every list-page navigation and ~285 kB on detail pages.
The templates already cache-bust via asset_version() using file mtime, so this is
redundant. Restrict the no-store rule to HTML responses only, and serve /static/ with
a long max-age plus immutable.

4. Dead references
templates/login_register.html requests css/all.min.css, which does not exist — it 404s
on every login page view. static/js/tv-chart.js is referenced by no template. Remove
both.

Verify before finishing: write a short script that calls db.market_gainer("stock")
unfiltered, then with a market filter, then with a sector filter, and print the
milliseconds for each. All three must be well under 100 ms after the first. Then
confirm /stocks, /etfs and a stock detail page still render correctly in RTL.
```

---

## Order 01 — ANALYZE, then tune PostgreSQL

**~30 minutes** · after 00

> Zero code changes. The planner is currently choosing plans against row counts that are wrong by a factor of 2,700.

```
The PostgreSQL database "Stock" behind my Flask app at
C:\Users\Yasmine\Desktop\StockPlatform_Claude is running entirely on default settings
and has never been analyzed. Connection details are in .env (STOCK_DB_* variables).

1. Fix the statistics. pg_stat_user_tables reports 2,282 live rows for
stockpricehistory; the real count is 6,119,262 — off by 2,700x. Autovacuum has
never successfully analyzed it. Run ANALYZE on stockpricehistory, etfpricehistory,
stocks and etf, then confirm the estimates now match reality. Check whether autovacuum
is disabled or starved for these tables and report what you find.

2. Tune the config. The box has 16 GB RAM and an NVMe SSD; the hot table is
1,263 MB. Current values and my targets:
    shared_buffers                   128MB  ->  4GB
    work_mem                           4MB  ->  64MB
    effective_cache_size               4GB  ->  12GB
    random_page_cost                   4.0  ->  1.1
    max_parallel_workers_per_gather      2  ->  4
Locate postgresql.conf, show me the exact edits before applying them, and tell me
which need a restart versus a reload. Do not restart the server without asking.

3. Measure. Before and after, time this query and report both numbers. It is the
core of the app's market pages and currently runs in about 5,884 ms warm:

  WITH ranked AS (
    SELECT ticker, adj_final::float v, j_date,
           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY j_date DESC) rn
    FROM stockpricehistory
    WHERE adj_final > 0 AND j_date <= :as_of AND j_date >= :cutoff)
  SELECT ticker, MAX(v) FILTER (WHERE rn=1) AS latest
  FROM ranked GROUP BY ticker;

Use EXPLAIN (ANALYZE, BUFFERS) both times. I specifically want to see whether the
"Sort Method: external merge  Disk: 10400kB" spill disappears once work_mem is raised.
```

---

## Order 02 — Materialized views for the market analytics

**~1 day** · after 01

> The biggest single win in the plan: 5,884 ms to 8.9 ms, verified on this database before it was recommended.

```
In C:\Users\Yasmine\Desktop\StockPlatform_Claude, move the market-wide analytics out of
request time and into materialized views.

The problem. Every market-wide page recomputes returns from 6.1M raw price rows on
each request. _gainer() in db.py window-functions a two-year slice of the whole price
table; EXPLAIN shows a Parallel Seq Scan reading 135,779 blocks (~1.06 GB — the entire
table), discarding 1,755,563 rows by filter, then an external merge sort spilling to
disk. It takes 5,884 ms warm and 13,908 ms cold. The prices only change once a day.

Proven fix. I built the same result as a materialized view against this database:
8.9 ms to read, 184 kB on disk, 6.8 s to refresh. That is 661x faster. Do this properly
for all of them.

Build views for the analytics currently cached in db._CACHE: market_gainer,
period_gainer, _perf_prices, _strategy_scan_full, _filter_scan_full, score_scan_full —
for both kind="stock" and kind="etf". Read each function first and reproduce its maths
exactly; the returns, ceiling and floor percentages must match the current output
number for number.

Requirements:
- Give every view a UNIQUE index so REFRESH MATERIALIZED VIEW CONCURRENTLY works and
  readers are never blocked during a refresh.
- Add a db.refresh_analytics() that refreshes them all in dependency order, and call it
  at the end of the data-update flow in market.py, where clear_cache() is called today.
- Rewrite the db.py read functions to SELECT from the views, keeping every public
  signature and return shape identical so app.py and the templates need no changes.
- Then DELETE warm_cache() and its background thread in app.py (_kick_off_warm). Warming
  is now a database artifact shared by every worker, not per-process RAM. Keep
  ensure_indexes().
- Add a migration-safe creation path: the app must start cleanly against a database
  where the views do not exist yet.

Verify: for several tickers and every period, assert the view output equals the old
Python output exactly. Then report the before/after milliseconds for /stocks, /etfs,
/performance, /strategies, /filters and /screener.
```

---

## Order 03 — Schema pass — dates and numerics

**~half a day** · after 02

> Speeds up the view refresh itself, and is the prerequisite for TimescaleDB if live data ever happens.

```
In C:\Users\Yasmine\Desktop\StockPlatform_Claude, fix three schema problems in the
PostgreSQL "Stock" database. Take a pg_dump backup first and confirm it before starting.

1. Dates are strings. stockpricehistory.j_date and etfpricehistory.j_date are
varchar(10) holding Jalali dates, so every range filter is a string comparison across
6.1M rows. A real `date` column ALREADY EXISTS in both tables and is unused by the
analytics. Move all range filtering and ordering in db.py onto the `date` column, and
keep j_date strictly for display. Verify `date` is fully populated and consistent with
j_date before relying on it — report any mismatched or NULL rows rather than guessing.

2. Prices are numeric. Every OHLC column is `numeric` and gets cast with ::float at
query time, six million times per scan. Convert the adj_* columns to double precision.
Check for precision loss on a sample first and show me the comparison.

3. Indexes. ix_sph_ticker_jdate and ix_sph_jdate exist but cannot serve the
market-wide scans. Once filtering moves to `date`, add
  (ticker, date DESC) INCLUDE (adj_final)
on both price tables to give index-only scans for the single-ticker detail and chart
lookups. Then check whether the old j_date indexes are still used at all — drop them if
not, and tell me how much space that reclaims.

Re-run ANALYZE afterwards. Confirm the materialized view refresh time and every page's
output are unchanged or better — the numbers on screen must not move.
```

---

## Order 04 — Redis replaces the in-process cache

**~half a day** · after 02

> The step that makes running more than one worker safe. Nothing above this line is safe to run under Gunicorn with multiple workers.

```
In C:\Users\Yasmine\Desktop\StockPlatform_Claude, replace the in-process analytics
cache with Redis.

Why. db._CACHE is a plain module-level dict — unbounded, no TTL, no eviction, and
private to each process. Under Gunicorn with N workers that becomes N independent
copies in RAM and N simultaneous cold computations. It is the main reason this app
cannot currently serve multiple users safely.

What to build:
- Add Redis (redis-py) with connection settings in .env alongside the existing
  STOCK_DB_* variables, and a sensible localhost default.
- Replace _CACHE, _STRAT_CACHE, _FILTER_CACHE, _SCORE_CACHE and _MKT_CACHE with a
  single Redis-backed helper. Keep the same call sites and cache-key tuples.
- Use a version-key pattern: store a monotonically increasing "analytics version" in
  Redis, include it in every cache key, and have clear_cache() bump it instead of
  deleting keys. That makes invalidation atomic across all workers.
- Pick a serialisation that round-trips the row dicts faithfully, including None,
  floats and Persian strings. Verify with an explicit test.
- Set a TTL as a safety net so a missed invalidation self-heals.
- The app must still start and serve correctly if Redis is unreachable — degrade to
  querying the materialized views directly, and log a warning, rather than crashing.

Verify: run the app under Gunicorn with 4 workers, hit a market page from several
processes at once, and confirm the expensive query executes only once total — not once
per worker.
```

---

## Order 05 — Gunicorn, Nginx, Docker Compose

**~1 day** · after 04

> The actual launch step. This is where debug=True stops being a remote code execution risk.

```
Containerise the Flask app at C:\Users\Yasmine\Desktop\StockPlatform_Claude for
production on a Linux VPS. It currently runs via app.run(debug=True, port=5002) — the
Werkzeug development server, single process, with the interactive debugger exposed.

Build:
- A Dockerfile for the app: slim Python base, non-root user, dependencies installed
  from requirements.txt as a cached layer.
- docker-compose.yml with services: web (Gunicorn), db (PostgreSQL 17), redis, nginx.
  Named volumes for Postgres and Redis so data survives recreation.
- Gunicorn with gthread workers, not sync and not gevent — this workload waits on
  Postgres rather than burning CPU. Pick worker and thread counts for a 4-8 vCPU box and
  explain your reasoning in a comment.
- Nginx in front: TLS termination, gzip and brotli, /static/ served directly with the
  long-cache headers from order 00, sensible request limits, and a WebSocket-ready
  proxy config so a future streaming service does not need this rewritten.
- Move all secrets to environment variables. db.py currently has a hard-coded password
  default — remove it and fail loudly if the variable is missing.
- Bake the Postgres tuning from order 01 into the db service config.
- A healthcheck for each service.

Constraints: the target VPS is in Iran, so do not use any AWS, GCP, Azure, Heroku,
Vercel, Fly or Render service, and do not pull base images from a registry likely to be
blocked — tell me if you need a mirror. Keep everything self-hosted.

Deliver a README section covering first-time setup, how to run migrations, how to
tail logs, and how to restore from a pg_dump backup.
```

---

## Order 06 — Celery and Beat for the TSE fetch

**~1 day** · after 05

> Removes the file-based job control that silently breaks the moment a second worker exists.

```
In C:\Users\Yasmine\Desktop\StockPlatform_Claude, move the market-data update job onto
Celery.

What is wrong now. The updater runs as a bare threading.Thread inside a Flask
request, so it dies with the worker process and has no retries and no history. Its
control plane is files on local disk — update_stop.flag and update_job.meta.json —
which means a stop click landing on worker 2 cannot stop a job running inside worker 1.
Under Gunicorn this is silently broken.

Build:
- Celery with the Redis from order 04 as broker and result backend. Add worker and beat
  services to docker-compose.yml.
- Convert the update flow in market.py / run_update.py into Celery tasks, one per
  ticker batch, with retry and exponential backoff. The last task in the chain must call
  db.refresh_analytics() from order 02 and then bump the Redis cache version.
- Move all job state — running, progress, per-ticker success/failure, stop requests —
  into a PostgreSQL table. Delete update_stop.flag and update_job.meta.json entirely.
- Rewrite the /update routes and update.html polling to read that table. Keep the
  existing admin-only gating and the current UI behaviour.
- Add a Beat schedule for a nightly fetch after the Tehran market closes. Tehran Stock
  Exchange trades Saturday to Wednesday — do not schedule it Thursday or Friday.
- Handle the TSETMC connectivity failures visibly. The last logged run recorded eight
  consecutive "No data returned" errors before being stopped; those must surface as
  failed tasks with retry counts, not silent zeros.

Verify: start a run, kill the worker container mid-flight, and confirm it resumes
without losing or duplicating tickers.
```

---

## Order 07 — Migrations, error tracking, backups

**~half a day** · after 05

> Do these before you have users, not after you lose some.

```
Add the three production essentials to C:\Users\Yasmine\Desktop\StockPlatform_Claude.

1. Alembic. There is no migration tooling at all, so schema changes currently have
no safe path once real users have rows. Introduce Alembic, generate an initial revision
that reflects the CURRENT live schema (stocks, stockpricehistory, etf, etfpricehistory,
users, watchlist) without trying to recreate it, and capture the order 02 materialized
views and order 03 column changes as proper revisions. Wire `alembic upgrade head` into
container startup.

2. Error tracking and logging. The app reports errors with print() and has a
[perf] hook that logs any request over 200 ms to stdout. Add Sentry for exceptions, and
convert logging to structured JSON with a request id so slow requests can be traced.
Keep the existing 200 ms threshold as a warning-level event. Do not log Persian user
data or session cookies.

3. Backups. Add a nightly pg_dump to compressed, timestamped files with a retention
policy, plus a restore script that has actually been tested — restore into a scratch
database and diff the row counts as part of the task. Storage must be self-hosted or a
domestic provider; no foreign object storage.
```

---

## Order 08 — Vue 3 on the five table pages

**~2 weeks** · optional · after 00

> Convert one page first and stop. Only repeat once you are happy with how the first one turned out.

_Prompt — run this once per page, starting with market.html_

```
In C:\Users\Yasmine\Desktop\StockPlatform_Claude, convert ONE page to Vue 3 as a
mounted island. Do not build a single-page app and do not touch the other templates.

Context. This is a Persian RTL Flask app with 17 Jinja templates, vanilla JS, no
package.json, no build step, no framework of any kind today. Five templates are worth
converting because they share one shape — filter controls above a large sortable table:
market.html (serves /stocks and /etfs), performance.html, strategies.html,
filters.html, screener.html. The other twelve stay in Jinja permanently.

Start with market.html only. It renders 782 rows server-side with no pagination, and
every filter change is a full page reload.

Build:
- A JSON endpoint for the table data. db.market_gainer() already returns plain dicts, so
  this should be thin. It must be fast — confirm order 00 and ideally order 02 have
  landed first, or you will be putting a fast table on a slow API.
- Vue 3 + TypeScript + Vite, mounted into a single <div> inside the existing
  market.html. Jinja keeps rendering the shell, nav, search box and auth. Build to
  static/dist/ and load it with the existing asset_version() cache-busting.
- TanStack Table for the grid: client-side sorting and filtering with no round trip,
  and row virtualization so 782 rows do not all live in the DOM.
- The filter controls move client-side. Keep the URL query parameters working so links
  and browser back still behave.

Non-negotiable: RTL layout and Persian number formatting must match the current page
exactly — compare screenshots before and after. The existing db.to_persian helpers
define the formatting; reimplement them faithfully in TypeScript. Keep the Excel export
and the watchlist stars working.

Also: once navigation is fast, check whether the #nav-loader "calculating, please
wait" overlay in base.html is still needed. It was built to mask the slowness that
orders 00 and 02 remove.

Stop after this one page. Show me the result before converting the other four.
```

---
