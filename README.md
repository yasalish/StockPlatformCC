# بورس‌نگار (BourseNegar)

سامانهٔ یکپارچهٔ تحلیل بازار سرمایه — یک پلتفرم واحد (Flask, فارسی/RTL) که جای
مجموعهٔ اسکریپت‌های جدا‌جدای Streamlit را می‌گیرد و روی همان پایگاه دادهٔ
PostgreSQL «Stock» کار می‌کند.

A single Persian (RTL) Flask platform over the Tehran Stock Exchange, replacing
the previous collection of separate Streamlit scripts. Same PostgreSQL database
(`Stock`), same analytics, one web app — modeled on the `codalnegar` project.

## صفحات / Pages
- **داشبورد** (`/`) — خلاصهٔ بازار + برترین‌ها/ضعیف‌ترین‌های سهام و صندوق‌ها.
- **سهام** (`/stocks`) — جدول بازدهی همهٔ نمادها در دوره‌های ۱ هفته تا ۱۸ ماه،
  قابل مرتب‌سازی و فیلتر بر اساس بازار + خروجی اکسل.
- **جزئیات نماد** (`/stock/<id>`, `/etf/<id>`) — بازدهی دوره‌ای، سقف/کف،
  بازدهی سال‌به‌سال، نمودار قیمت، و بازدهی از ابتدای داده.
- **صندوق‌ها** (`/etfs`) — همان جدول بازدهی برای ETFها با فیلتر نوع صندوق.
- **به‌روزرسانی** (`/update`) — دریافت قیمت‌های جدید از `finpy_tse` و درج در پایگاه داده.
- **نقشهٔ بازار** (`/heatmap`) — کل بازار در یک صفحه: هر گروه یک جعبه و هر نماد یک
  خانه، با اندازهٔ متناسب با ارزش معاملات و رنگ متناسب با بازدهٔ دورهٔ انتخابی.
- **تنظیمات** (`/settings`) — پوسته (شش تا)، ارقام فارسی/لاتین، چگالی و راه‌راه‌بودن
  جدول، ضخامت نوار پیمایش، اندازهٔ قلم، کوررنگی، به‌روزرسانی خودکار و…
- **راهنما** (`/help`) و **درباره** (`/about`).
- **جستجو** — جستجوی زندهٔ نماد/نام (سهام + صندوق) در نوار بالا.

## اجرا / Run
```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5002
```

پایگاه داده از پیش موجود است (جداول `stocks`, `stockpricehistory`, `etf`,
`etfpricehistory`). تنظیمات اتصال در `db.py` (`DB_SETTINGS`) و قابل بازنویسی با
متغیرهای محیطی `STOCK_DB_*` است.

## معماری / Architecture
| فایل | نقش |
|------|-----|
| `app.py` | مسیرهای Flask (صفحات، API جستجو، خروجی اکسل، به‌روزرسانی) |
| `db.py` | اتصال متمرکز به PostgreSQL + همهٔ محاسبات تحلیلی |
| `cache.py` | حافظهٔ نهان مشترک تحلیل‌ها روی Redis (میان همهٔ workerها) |
| `market.py` | ایجاد و کنترل کار به‌روزرسانی (صف Celery + جدول‌های PostgreSQL) |
| `jobs.py` | وضعیت کار در PostgreSQL — جایگزین فایل‌های پرچم |
| `celery_app.py` | پیکربندی Celery و زمان‌بندی Beat (شنبه تا چهارشنبه) |
| `tasks.py` | وظیفه‌های دریافت داده، با تلاش مجدد و از‌سرگیری |
| `tse_fetch.py` | دریافت و درج قیمت یک نماد (بدون تکرار سطر) |
| `reports.py` | خروجی اکسل جدول بازدهی (openpyxl) |
| `prefs.py` | فهرست تنظیمات نمایش و قواعد اعتبارسنجی آن‌ها (ماژول خالص، بدون Flask/DB) |
| `plans.py` | نوع حساب و سهمیه‌ها (ماژول خالص) — پورت اشتراک هنوز پیاده نشده |
| `account.py` | API حساب کاربری: تنظیمات و غربالگرهای ذخیره‌شده |
| `static/js/theme.js` | اعمال پوسته و تنظیمات روی `<html>` و همگام‌سازی با سرور |
| `static/js/tables.js` | نوار پیمایش بالای هر جدول + ابزار (تمام‌صفحه، CSV، چاپ) |
| `static/js/ui.js` | میان‌برها، بازدیدهای اخیر، بازگشت به بالا، ذخیرهٔ نما |
| `static/js/heatmap.js` | ترسیم «نقشهٔ بازار» |
| `templates/` | قالب‌های Jinja (RTL) |
| `tests/` | آزمون‌های خالص (`pytest -q` بدون پایگاه داده) |
| `static/` | CSS + JS (نمودار SVG بدون کتابخانهٔ خارجی) |
| `gunicorn.conf.py` | تنظیمات سرور تولید (gthread) — همراه دلیل هر عدد |
| `Dockerfile` | ایمیج برنامه (Python slim، کاربر غیر‌root) |
| `docker-compose.yml` | شش سرویس: nginx، web، worker، beat، db، redis |
| `deploy/nginx/` | ایمیج و پیکربندی nginx (TLS، gzip+brotli، فایل‌های ایستا) |
| `observability.py` | لاگ JSON ساختاریافته + شناسهٔ درخواست + Sentry |
| `migrations/` | مهاجرت‌های Alembic (شمای پایه، ۰۲، ۰۳، ۰۶، ۰۹) |
| `deploy/scripts/` | پشتیبان‌گیری شبانه و اسکریپت بازیابی آزموده‌شده |
| `frontend/` | جزیره‌های Vue 3 + TypeScript برای بازار، بازدهٔ دوره‌ای، فیلترها/استراتژی‌ها و غربالگر |
| `static/dist/` | خروجی ساخت Vite — با `asset_version()` نسخه‌گذاری می‌شود |
| `deploy/.env.example` | الگوی پیکربندی استقرار (رمزها، میرورها، تنظیم PostgreSQL) |

### منطق تحلیل / Analytics (from the original scripts)
- **بازدهی**: `(adj_final_آخر − adj_final_گذشته) / adj_final_گذشته × ۱۰۰`
- **سقف**: فاصلهٔ قیمت پایانی تا بیشترین قیمت دوره.
- **کف**: فاصلهٔ قیمت پایانی تا کمترین قیمت دوره.
- دوره‌ها بر حسب **روز معاملاتی**: ۵، ۲۰، ۶۰، ۱۲۰، ۲۴۰، ۳۶۰ روز.
- **سال‌به‌سال**: از همین تاریخ در سال‌های گذشته (تقویم جلالی).

## کارایی / Performance
برای سرعت، هنگام نخستین اجرا این ایندکس‌ها روی پایگاه داده ساخته شده‌اند
(اگر وجود نداشته باشند، دوباره بسازید):
```sql
CREATE INDEX IF NOT EXISTS ix_sph_ticker_jdate ON stockpricehistory(ticker, j_date);
CREATE INDEX IF NOT EXISTS ix_eph_ticker_jdate ON etfpricehistory(ticker, j_date);
```

## حافظهٔ نهان / The analytics cache (Redis)

The market-wide analytics are cached in **Redis** (`cache.py`), not in each
process. That is what makes running more than one Gunicorn worker safe: all
workers read one shared copy, and `db.clear_cache()` invalidates every worker at
once by incrementing a version key rather than flushing a local dict.

**Connection** — set `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` / `REDIS_PASSWORD`
(or a single `REDIS_URL`) in `.env`; the default is `localhost:6379/0`. See
`.env.example` for the optional tuning knobs.

**Redis is optional.** If it is unreachable the app still starts and serves
correct pages — it queries the database directly, logs one warning, and picks
Redis back up automatically when it returns. You will see which mode a process is
in on the startup line `[startup] analytics cache: …`.

**Running Redis locally**
```bash
# Linux / production
redis-server --save "" --appendonly no --maxmemory 256mb --maxmemory-policy allkeys-lru

# Windows dev box — a portable build lives in .tools/redis (gitignored)
.tools\redis\redis-server.exe --port 6379 --save "" --appendonly no \
    --maxmemory 256mb --maxmemory-policy allkeys-lru
```
`--maxmemory` + `allkeys-lru` bound the memory; every entry also carries a TTL
(`ANALYTICS_CACHE_TTL`, default 6 h) so a missed invalidation self-heals.

Note: the Windows build is Redis 5.0, which predates the `HELLO` command, so
`cache.py` speaks RESP2 by default. Set `REDIS_PROTOCOL=3` against a Redis 6+
server if you want RESP3.

**Verifying** — `python verify_order04.py` checks the serialisation round-trip,
version-key invalidation, single-flight, cross-process sharing, graceful
degradation, and that every cached analytic equals its uncached computation.

## استقرار / Production deployment (Docker Compose)

The stack is six containers. Only nginx is reachable from outside; everything
else exists solely on the internal compose network.

| service | what it is | published |
|---|---|---|
| `nginx`  | TLS termination, gzip + brotli, `/static/`, rate limits | **80, 443** |
| `web`    | Gunicorn (gthread) + Flask — see `gunicorn.conf.py` | no |
| `worker` | Celery — the TSETMC fetch (order 06) | no |
| `beat`   | Celery Beat — the nightly schedule (order 06) | no |
| `db`     | PostgreSQL 17, tuned per order 01 | no |
| `redis`  | cache (db 0) + Celery broker (db 1) / results (db 2) | no |

### ⚠️ Before the first build: pick a registry mirror

Docker Hub does not serve Iranian IP addresses, so `docker compose build` will
fail on the VPS with a timeout or a 403. Every base image in this stack
(`python`, `postgres`, `redis`, `debian`) is written as `${REGISTRY}image:tag`,
so one variable in `deploy/.env` redirects all of them:

```bash
REGISTRY=docker.arvancloud.ir/     # NOTE the trailing slash
```

Candidates, all domestic — **verify one works before building**, they come and go:

```bash
docker pull docker.arvancloud.ir/debian:bookworm-slim
docker pull registry.docker.ir/debian:bookworm-slim
docker pull docker.iranserver.com/debian:bookworm-slim
docker pull hub.hamdocker.ir/debian:bookworm-slim
```

`DEBIAN_MIRROR` (apt, for the nginx image) and `PIP_INDEX_URL` (PyPI, for the
app image) work the same way. Nothing in this stack touches AWS, GCP, Azure,
Heroku, Vercel, Fly or Render; everything runs on your own machine.

### First-time setup

```bash
# 1. Configuration. deploy/.env is SEPARATE from the .env used by `python app.py`.
cp deploy/.env.example deploy/.env

# 2. Generate the three required secrets and paste them into deploy/.env:
python -c "import secrets; print('STOCK_SECRET=' + secrets.token_hex(32))"
python -c "import secrets; print('STOCK_DB_PASSWORD=' + secrets.token_urlsafe(24))"
python -c "import secrets; print('REDIS_PASSWORD=' + secrets.token_urlsafe(24))"
# Also set REGISTRY (above) and SERVER_NAME=your.domain

# 3. Size PostgreSQL for the box. The defaults are order 01's 16 GB values;
#    on a smaller VPS lower PG_SHARED_BUFFERS / PG_EFFECTIVE_CACHE_SIZE.

# 4. Build and start. --env-file is REQUIRED: without it compose reads the
#    development ./.env and the secrets above are missing, which fails loudly.
docker compose --env-file deploy/.env up -d --build

# 5. Watch it come up. web waits for db and redis to be healthy; nginx for web.
docker compose --env-file deploy/.env ps
curl -k https://localhost/readyz
```

On first start nginx generates a **self-signed** certificate so the stack comes
up on HTTPS immediately. Replace it with a real one:

```bash
docker compose --env-file deploy/.env cp fullchain.pem nginx:/etc/nginx/tls/fullchain.pem
docker compose --env-file deploy/.env cp privkey.pem   nginx:/etc/nginx/tls/privkey.pem
docker compose --env-file deploy/.env exec nginx nginx -s reload
```

Only then uncomment the HSTS line in `deploy/nginx/snippets/security-headers.conf`
— with a self-signed certificate it locks browsers out of the site for a year.

### مهاجرت شِما / Schema migrations (Alembic)

There is real migration tooling now; the idempotent `ensure_*` helpers remain as
a belt-and-braces path for `python app.py` with no Alembic run.

```bash
DC="docker compose --env-file deploy/.env"

$DC run --rm migrate alembic current        # where this database is
$DC run --rm migrate alembic history        # the chain
$DC run --rm migrate alembic upgrade head   # apply (also runs automatically)
```

`alembic upgrade head` is **wired into startup** as a one-shot `migrate`
service; `web`, `worker` and `beat` all wait on
`service_completed_successfully`, so a failed migration stops the deploy instead
of letting the app come up against a schema it does not understand.

| revision | what it does |
|---|---|
| `0001` | **Baseline.** Every object is guarded by an existence check, so it is a pure no-op against the live database and creates the real schema on an empty one. |
| `0002` | Order 03: `adj_*` → `double precision` (one `ALTER TABLE`, so the table is rewritten once, not five times) and the `(ticker, date) INCLUDE (adj_final)` indexes. |
| `0003` | Order 02: the 20 materialized analytics views + their UNIQUE indexes. |
| `0004` | Order 06: `update_job` / `update_job_ticker`. |

Two deliberate choices worth knowing:

- **`0002` runs before `0003`** even though order 02 came first chronologically.
  A materialized view freezes the types of the expressions it selects, so
  building the views before converting the columns would pin them to `numeric`
  until their next full rebuild.
- **The baseline refuses to downgrade.** `alembic downgrade base` would drop the
  price history and every user; it raises instead. To roll a database back,
  restore a dump.

> `0002` rewrites the whole price table under an `ACCESS EXCLUSIVE` lock. On the
> 6.1M-row production table that is minutes of downtime — plan a window for the
> first `upgrade head`. It is skipped entirely once the columns are converted.

### لاگ و ردیابی خطا / Logging and error tracking

Every log line is one JSON object carrying a **request id**, and that id is the
same string from nginx's access log to the Celery task that finishes the work:
nginx generates `$request_id`, passes it as `X-Request-ID`, `observability.py`
attaches it to every record, and Flask returns it to the client.

```bash
$DC logs web | jq 'select(.level=="WARNING")'          # slow + failed requests
$DC logs web | jq 'select(.request_id=="a1b2c3")'      # one request, end to end
$DC logs | jq 'select(.duration_ms > 1000)'            # the worst offenders
$DC logs worker | jq 'select(.level=="ERROR")'
```

A request over **200 ms** (`SLOW_REQUEST_MS`, the threshold from order 00) is a
`WARNING` with its path, status, duration and endpoint. Set `LOG_FORMAT=text`
for a readable local console.

**What is never logged**, by design: cookies and `Authorization` headers; the
query string (which is where the Persian filter values, the search term and the
watchlist ticker live — the *path* is logged, the query is not); request bodies;
usernames and e-mail addresses. Records carry the numeric `user_id` only.

**Sentry** is off unless `SENTRY_DSN` is set, and it applies the same rules —
`send_default_pii=False` plus a `before_send` scrubber. sentry.io is a foreign
SaaS and this deploys inside Iran, so point it at something you host: Sentry's
server is open source and **GlitchTip** speaks the same protocol in a fraction
of the footprint. Both run on this VPS.

### Logs

```bash
DC="docker compose --env-file deploy/.env"

$DC logs -f                     # everything
$DC logs -f web                 # Gunicorn access log + the app's [perf] warnings
$DC logs -f nginx               # access/error, with rt= and urt= timings
$DC logs --since 15m web        # recent only
$DC logs -f db | grep duration  # statements over 200 ms

$DC exec web tail -f /var/lib/boursenegar/update_job.log   # a running data update
```

`rt=` is the total request time and `urt=` the upstream (app) time — the gap
between them is network or client, not the application.

### پشتیبان‌گیری / Backups and restore

A `backup` container takes a nightly `pg_dump`, prunes old ones, and once a week
restores the newest dump into a scratch database and diffs the row counts. It is
built from the **same postgres image as the database**, so `pg_dump` can never
drift to a different major version than the server — the usual way a backup
service quietly stops working after an upgrade.

Storage is `./backups` on the host: self-hosted, nothing uploaded. To keep an
off-box copy, rsync that directory to another machine you control or to a
domestic provider.

```bash
DC="docker compose --env-file deploy/.env"

$DC exec backup /scripts/backup.sh            # dump now
$DC exec backup /scripts/restore.sh           # TEST: restore to scratch + diff
$DC exec backup /scripts/restore.sh --keep    # …and keep the scratch database
$DC logs backup                               # JSON, same format as the app
```

Each dump is `Stock-YYYYmmdd-HHMMSS.dump` (custom format, compressed), written
to a `.part` file and renamed only after `pg_restore --list` proves it readable,
with a `.sha256` sidecar. Retention is `BACKUP_KEEP_DAYS` (14) with a floor of
`BACKUP_KEEP_MIN` (3) — age alone is not safe, because a database that has been
failing to dump for a month would otherwise have its last good backup deleted on
the day it is finally needed.

**Real recovery**, which requires an explicit `--promote`:

```bash
$DC stop web worker beat
$DC exec backup /scripts/restore.sh --file /backups/Stock-20260816-212108.dump       --into Stock --promote
$DC run --rm migrate alembic upgrade head
$DC exec -T web python -c "import db; db.clear_cache()"   # the cache is stale
$DC start web worker beat
```

That last `clear_cache()` matters: Redis has no idea the database underneath it
was replaced, and bumping the version key is what makes every worker drop its
cached rows at once.

### به‌روزرسانی داده / The market-data update (Celery)

The price fetch is a Celery job, not a thread inside a web request. That is what
gives it retries, history and resumability, and it is why a «توقف» click works
no matter which Gunicorn worker receives it.

**How a run is shaped**

```
market.start_job()  →  one update_job row + one update_job_ticker row per symbol
                    →  fetch_batch × N        25 symbols per task, retried
                    →  finalize_update()      refresh_analytics() + cache bump
```

Job state lives in two PostgreSQL tables — `update_job` and `update_job_ticker`
— which replaced `update_stop.flag` and `update_job.meta.json`. Those files only
ever worked when one process owned the run; with the fetch in its own container
they would not have worked at all.

**The nightly schedule.** Beat enqueues a stock fetch at **20:30** and an ETF
fetch at **21:30** Tehran time, **Saturday to Wednesday**. Thursday and Friday
are the Iranian weekend and the exchange is closed, so they are skipped rather
than fetched-and-found-empty. Override with `BEAT_HOUR`, `BEAT_MINUTE` and
`BEAT_TRADING_DAYS` in `deploy/.env`.

> **Never scale `beat` above 1.** Two schedulers means every nightly fetch is
> enqueued twice. `worker` scales freely.

**Starting a run by hand**

```bash
DC="docker compose --env-file deploy/.env"

# From the UI: /update, admin only (unchanged).
# From the shell:
$DC exec -T web python run_update.py stock 1404-01-01 1404-01-10
$DC exec -T web python run_update.py etf   1404-01-01 1404-01-10 full

# Progress, as JSON
$DC exec -T web python -c "import jobs,json;print(json.dumps(jobs.snapshot(),ensure_ascii=False,default=str,indent=2))"
```

**Watching and controlling**

```bash
$DC logs -f worker                       # every fetch, retry and failure
$DC logs -f beat                         # what the schedule enqueued
$DC exec -T worker celery -A celery_app inspect active     # tasks in flight
$DC exec -T worker celery -A celery_app inspect stats

# stop / pause the running job (a row update — reaches every process)
$DC exec -T web python -c "import market;print(market.stop_job())"
$DC exec -T web python -c "import market;print(market.pause_job())"
$DC exec -T web python -c "import market;print(market.resume_job())"
```

**When TSETMC misbehaves.** A symbol that fails is recorded as failed with its
retry count, shown next to the symbol on /update as e.g. `فولاد³`. Eight
consecutive failures are treated as a service outage rather than eight empty
symbols: the batch itself is retried with exponential backoff. Query the detail
with

```sql
SELECT ticker, status, attempts, error
  FROM update_job_ticker
 WHERE job_id = (SELECT max(id) FROM update_job) AND status = 'failed'
 ORDER BY attempts DESC;
```

**If a worker dies mid-run** it resumes by itself — this is verified, not hoped
for. Celery redelivers the in-flight batch (`task_acks_late`), a restarting
worker runs `tasks.reconcile` on boot, and Beat runs it every five minutes; any
symbol still outstanding is re-dispatched. Nothing is duplicated because
`claim_ticker()` refuses to hand out a finished symbol and the write itself
replaces rather than appends. To force it:

```bash
$DC exec -T web python -c "import market;print(market.resume_job_tasks())"
```

### Everyday commands

```bash
DC="docker compose --env-file deploy/.env"

$DC ps                       # health of each service
$DC restart web              # reload the app only
$DC up -d --build web        # rebuild after a code change
$DC exec db psql -U postgres -d Stock
$DC exec -T redis redis-cli -a "$REDIS_PASSWORD" --no-auth-warning info memory
$DC down                     # stop; volumes and data survive
$DC down -v                  # stop AND DELETE ALL DATA — the dangerous one
```

### Verifying the deployment artefacts

```bash
python verify_order04.py            # Redis cache: serialisation, invalidation, degradation
python verify_order05.py            # compose, secrets, gunicorn, Dockerfile, nginx, app
python verify_order06.py            # Celery: config, job tables, and the kill-the-worker test
python verify_order07.py            # Alembic, JSON logging, Sentry scrubbing, backup + RESTORE
python verify_order07.py --full     # …and migrate a restored copy of the real database
python .tools/check_nginx_conf.py   # a real `nginx -t` over deploy/nginx/
```

`verify_order06.py` starts a real Celery worker, kills it mid-run with
`taskkill /F`, starts another, and asserts every symbol ends up written exactly
once. It runs against scratch tables, so it never touches the price data.

## تنظیمات و پوسته / Settings, themes and the table scrollbars (order 09)

### نوار پیمایش بالای جدول‌ها

Every wide table now carries a **second horizontal scrollbar above it**, mirrored
from the real one at the bottom and synced in both directions, sticky under the
site header. The problem it solves: `/performance` is ۷۸۲ rows tall and ۲۰
columns wide, so the only control that moved the table sideways sat two screens
below the part of the table you were reading.

* `static/js/tables.js` builds it. It finds every `.table-scroll` — including
  the ones the Vue islands mount after page load, via a `MutationObserver` — and
  inserts a `.tbl-bar` (mirror + toolbar) directly **before** the scroller.
* The mirror is a real overflow container with a spacer inside, so the browser
  draws a **real scrollbar**: wheel-tilt, drag, shift+wheel and assistive
  technology all keep working. A div with a fake thumb would have to
  reimplement every one of them.
* A table that already fits shows no mirror at all.
* Thickness is `--sbar-h`: ۱۴ / ۲۰ / ۲۸ px, chosen in تنظیمات, default ۲۰
  (the browser default is ~۸).

Two engine facts are baked into the CSS, both learned by measuring:

1. **`scrollbar-color` and `::-webkit-scrollbar` are mutually exclusive in
   Chromium ≥ 121.** Setting the standard property makes Chrome ignore every
   `::-webkit-scrollbar` rule for that element — including the height. So
   `scrollbar-color` is scoped to `@supports not selector(::-webkit-scrollbar)`,
   i.e. Firefox, where it is the only thing that works. Firefox cannot be given
   a pixel thickness at all (`scrollbar-width` takes only `auto | thin | none`),
   so there the bars keep the platform height.
2. **Headless Chrome draws overlay scrollbars**, which take no layout space, so
   the bar cannot be measured in a headless run: `verify_order09.py --headed`
   is what proves the ۲۰px bar is really painted.

Beside the mirror sits a small toolbar: **تمام‌صفحه** for every table, plus
**CSV** and **چاپ** on the non-virtualized ones. The virtualized grids keep only
the full-screen button on purpose — they hold ~۶۰ rows in the DOM at a time, so
exporting from the page would produce a file with sixty of seven hundred rows in
it: a wrong answer that looks like a right one. Those pages keep their
server-side Excel export, which reads the database.

### پوسته‌ها / Themes

Six: **روشن** (the original cream + gold), **سپیا**, **کاغذ سفید**, **تاریک**,
**نیمه‌شب**, **ذغالی**. `:root` in `static/css/style.css` **is** the light theme —
deliberately not `[data-theme="light"]`, so an unknown or missing value renders
as the theme everyone already knows (`prefs.family_of()` answers `light` for the
same reason).

Adding one is three steps:

1. A `[data-theme="<id>"]` block that redefines **every** custom property
   `:root` defines. A block that redefines half of them inherits the rest and
   renders as a broken light theme; `tests/test_prefs.py` and
   `verify_order09.py` both diff the property names and fail if one is missing.
2. An entry in `prefs.THEMES` (id, label, family, swatch) — the picker and the
   ☾/☀ toggle read that list.
3. Nothing else. `--radius` is geometry, not colour.

Adding the themes required an **appearance-preserving refactor first**: the
~۷۰ literal hex values in `style.css` (`#fff` alone appeared ۲۵ times) became
tokens (`--on-brand`, `--hover`, `--input-bg`, `--star`, …), each holding exactly
the value its rule used to carry, so the light theme is unchanged to the pixel.
`verify_order09.py` re-checks that no light-surface literal is left as the value
of an ordinary property.

The theme is applied **before the first paint** by an inline script in
`base.html`'s `<head>`, above the stylesheet: applied from an external file it
lands after first paint and every navigation flashes the wrong theme. For a
signed-in account the server renders the attributes onto `<html>` and marks them
`data-prefs="server"`, which tells that script to leave them alone — otherwise
one browser's localStorage would overwrite the account's theme on every load.

There is also **رنگ صعود و نزول: آبی/نارنجی**, a colour-blind alternative that
overrides only `--up`/`--down`/`--up-bg`/`--down-bg` and therefore composes with
all six themes. In a platform whose entire meaning is carried by green and red,
that is a necessity rather than a nicety.

### تنظیمات / The settings screen

`/settings`, saved per account, applied immediately — no «ذخیره» button, because
a theme picker you have to confirm is a theme picker you cannot preview.

| where | what |
|---|---|
| `prefs.py` | pure module: what a preference **may** be. No Flask, no db, no network — same discipline as `plans.py`. |
| `user_prefs` table | one row per user, updated in place (`db.get_prefs` / `set_prefs` / `reset_prefs`). |
| `account.py` | `GET`/`PATCH /api/me/prefs`, `POST /api/me/prefs/reset`, and the saved-screen endpoints. |
| `static/js/theme.js` | turns the settings into `data-*` attributes on `<html>` and writes changes back. |

Rules worth knowing before changing it:

* **An invalid value is dropped, never raised.** These arrive from a `<select>`;
  a tab left open since last week must not `500` a settings save.
* **`prefs.DEFAULTS` is the schema.** `get_prefs()` merges the stored row *under*
  it, so adding a preference is one key plus one nullable column — no backfill.
  The column defaults in `db.init_db()` **and** in migration `0005` must equal
  `prefs.DEFAULTS` exactly, or a fresh account and a saved-then-reset account
  render differently.
* **Every preference must do something.** Each key in `prefs.DEFAULTS` names, in
  its comment, the code that reads it. A settings screen full of switches that
  change nothing is worse than a short one.

Migration: `migrations/versions/0005_user_prefs_and_screens.py` (`0005` → `0004`)
creates `user_prefs` and `saved_screens`. `db.init_db()` creates the same two
idempotently at boot — exactly as `jobs.ensure_tables()` still does for the
order-06 tables, and for the same two reasons: the local `python app.py` path
where nobody has run Alembic, and a fresh deployment whose first request beats
the migration.

### نقشهٔ بازار و نبض بازار / Market map and breadth

* **`/heatmap`** — the whole market on one screen: a box per گروه (صنعت for
  stocks, نوع for ETFs), a tile per symbol, **sized by traded value** and
  **coloured by return over the chosen period**. It answers the question a
  ranked list actively hides — where the money went — because the top of a list
  sorted by return is always the smallest, most volatile symbols. Group averages
  are value-weighted: a plain mean counts a symbol with ۱۰۰ میلیون تومان of
  turnover the same as one with ۱۰۰ میلیارد.
* **نبض بازار** on the dashboard — advancers vs decliners as a bar drawn from the
  actual counts, plus the value-weighted market return, the median symbol, the
  extremes and the busiest groups. A green headline built on three heavyweight
  symbols and one built on four hundred symbols are different markets, and a
  table sorted by return cannot tell them apart.

Both read the **same cached gainer rows** every other screen reads, plus one
extra cached query (`db.last_session`) for the last session's volume/value and
the one-day change. No new materialized view: `d1` is computed from the two most
recent bars rather than added to `db.PERIODS`, because a new period key changes
the column list of `mv_market_gainer_*` and every market page would fail on a
missing column until the views were rebuilt.

### چیزهای کوچک‌تر / The smaller additions

منوی کاربر در نوار بالا · کلید ☾/☀ · صفحه‌های **راهنما** و **درباره** ·
«ذخیرهٔ این نما» (saved filter presets, stored as the query string verbatim) ·
«بازدیدهای اخیر» (localStorage — a browsing trail, not a preference, so it does
not follow the account onto a shared machine) · back-to-top · keyboard shortcuts
(`/` `t` `f` `g` `m` `s` `?`) · arrow-key navigation in the search box ·
optional auto-refresh · a favicon (the browser's request for `/favicon.ico` had
been a 404 on every page load) · and a theme-aware KLineChart palette, since a
`<canvas>` cannot inherit the page's CSS variables.

### آزمون‌ها / Tests

```bash
pytest -q                              # pure: no database, no Redis, no Flask
python verify_order09.py               # full verification (needs the database + Chrome)
python verify_order09.py --headed      # …and measures the painted scrollbar
python verify_order09.py --no-browser  # static + API checks only
```

## جزیره‌های Vue / The Vue islands (order 08, then the four heavy tables)

`market.html` — the template behind **/stocks** and **/etfs** — was converted
first, by order 08. It is a **mounted island, not a single-page app**: Jinja
still renders the shell, the nav, the search box, the auth box, the flash
messages and the calculator's comparison tables. Vue owns two `<div>`s.

The four remaining table pages have since had the same treatment, for the same
reason — they were shipping between 1 MB and 2.2 MB of markup per navigation:

| page | island | endpoint | approach |
|---|---|---|---|
| /performance | `perf.ts` → `PerfPanel` / `PerfGrid` | `/api/performance/<kind>` | window-virtualized, two-row سقف/کف header |
| /filters, /strategies | `scan.ts` → `ScanPanel` / `ScanSection` | `/api/scan/<what>/<kind>` | sections mount when scrolled to |
| /screener | `screener.ts` → `ScreenerPanel` / `ScreenerGrid` | `/api/screener/<kind>` | window-virtualized ranked table |

`watchlist.html` and the remaining templates still use `BN.initTable`, which is
the right tool for a table of a few dozen rows.

### What changed for the user

| | before | after |
|---|---|---|
| DOM nodes on /stocks | 41,881 | 1,162 (**36×** smaller) |
| DOM nodes on /performance | 37,097 | 803 |
| DOM nodes on /strategies | 38,383 | 844 |
| DOM nodes on /screener | 16,583 | 676 |
| HTML per navigation (those four) | 1.1–2.2 MB | 4–5 kB + a JSON fetch |
| change a filter | full page reload | **no reload**, ~100–200 ms |
| Back button | reload | instant (bfcache) |

Everything else is deliberately identical, and verified so: column widths, row
heights, header labels, every cell's text, the pill up/down classes and the
watchlist star markup all match the server-rendered pages exactly. The numbers
are still Python's — the endpoints return the 🏆 winners, the compare table, the
verdict bands and the scores already computed, so nothing that a reader treats
as authoritative is re-derived in TypeScript.

Verified by `verify_perf_island.py` and `verify_scan_islands.py` (the latter
covers /filters, /strategies and /screener), both of which drive a real browser.

### Working on it

```bash
cd frontend
npm install
npm run build     # → ../static/dist/{market,perf,scan,screener}.js + a shared chunk
npm run dev       # rebuild on save
npm run typecheck
```

`static/dist/` **is committed**: the VPS should not need Node to deploy, and the
Docker image copies it in with the rest of `static/`. Rebuild and commit it
whenever you change `frontend/src/`.

### Two things worth knowing before touching this code

**Persian numbers are a faithful port, not a re-implementation.**
`frontend/src/format.ts` reproduces `db.to_persian` and `_pill.html` character
for character — including that the thousands separator stays an ASCII comma and
the sign is U+2212, not a hyphen. It deliberately does **not** use
`Intl.NumberFormat("fa-IR")`, which would emit «٬» and «٫»: better Persian, but
different from every other page in this app. It also implements Python's
**round-half-to-even**, because `toFixed` rounds half away from zero and four
real values in the dataset (0.125, 2.625, 15.625, 40.625) came out differently.
`verify_order08.py` checks the port against the live Python over ~15,000 values.

**Column widths are stated, not discovered.** `frontend/src/widths.ts` holds the
widths the un-virtualized table produced. They have to be explicit: a virtualized
table has ~27 of 742 rows in the DOM, so the browser's automatic layout would
size the columns from that handful — narrower than the real content, and
changing every time you scroll.

### Is the #nav-loader overlay still needed?

**Yes — keep it.** The order asked to check this once navigation got fast, and it
has for /stocks and /etfs: their filters no longer navigate at all, so the
overlay never appears there. But it lives in `base.html` and still covers the
four table pages that were not converted, which are still full server-side
renders. Measured on this database (materialized views not yet built):

| page | cold | warm |
|---|---|---|
| /performance | 3,663 ms | 1,900 ms |
| /strategies | 7,468 ms | 680 ms |
| /filters | 11,063 ms | 816 ms |
| /screener | 7,911 ms | 697 ms |

Every one of those is above the 200 ms threshold the app itself treats as slow.
Revisit after order 02's views are built AND the remaining four pages are
converted; removing it now would just take the feedback away.

### Verifying

```bash
python verify_order08.py                # everything, including a real browser
python verify_order08.py --no-browser   # skip the render comparison
```

It renders /stocks twice in Chrome — once with the pre-conversion template kept
at `.tools/market.html.pre08`, once with the converted one — and compares them.

## اسکریپت‌های اصلی / Original scripts
اسکریپت‌های قبلی (`stock_updater.py`، `etf_updater.py`، `stock_gainer.py`،
`etf_gainer.py`، `search.py`, …) دست‌نخورده باقی مانده‌اند؛ ماژول‌های به‌روزرسانی
مستقیماً توسط پلتفرم استفاده می‌شوند و بقیه مرجع منطق تحلیل‌اند.
