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
- **طراحی فیلتر** (`/filter-designer`) — فیلتر شخصی، به‌صورت گرافیکی: جعبه‌های
  دادهٔ قیمت، اندیکاتور، محاسبه، مقایسه، تقاطع و منطق را روی بوم به هم وصل می‌کنید.
- **نتیجهٔ فیلتر** (`/filter-designer/result`) — صفحه‌ای که «اجرا» به آن می‌رود:
  جدول تمام‌عرض نمادهای منطبق، با خروجی CSV، فهرست «فیلترهای دیگر» در سمت راست
  برای اجرای یک‌کلیکی بقیهٔ فیلترها، و دکمهٔ «چرا؟» که مقدار همهٔ جعبه‌ها را برای یک
  نماد روی نمودار همان فیلتر نشان می‌دهد.
- **بک‌تست فیلتر** (`/filter-backtest`) — همان فیلتر، بازپخش‌شده روی تاریخچه: هر
  کندلی که شرط در آن تازه برقرار شده یک سیگنال است، ورود با قیمت باز شدن کندل بعد،
  و بازده ۱ تا ۲۲ کندل بعد در برابر «خرید کل بازار در همان روزها». ورودهای غیرقابل
  انجام (صف خرید، نماد متوقف) کنار گذاشته و شمرده می‌شوند.
- **شاخص‌ها** (`/indices`) — ده شاخص کل بازار (کل، هم‌وزن، قیمت وزنی-ارزشی و
  هم‌وزن، شناور آزاد، بازار اول و دوم، صنعت، ۵۰ فعال‌تر، ۳۰ بزرگ)، چهل شاخص گروه
  صنعت، و نرخ دلار آزاد — با نمودار و «شکاف شاخص کل و هم‌وزن».
- **پول حقیقی و حقوقی** (`/moneyflow`) — خالص ورود/خروج پول حقیقی، سرانهٔ خرید و
  فروش، و قدرت خریدار در بازه‌های ۱ تا ۶۰ روز، به‌همراه «پول به کدام صنعت رفت؟».
- **تابلوی زنده** (`/live`) — عکس کل بازار: صف خرید و فروش و سرانهٔ آن‌ها، حجم
  حقیقی/حقوقی لحظه‌ای، EPS و P/E، ارزش بازار و حجم مبنا، و دفتر سفارش پنج‌سطحی
  که با کلیک روی هر ردیف باز می‌شود.
- **راهنما** (`/help`) و **درباره** (`/about`).
- **جستجو** — جستجوی زندهٔ نماد/نام (سهام + صندوق) در نوار بالا.

نوار ناوبری از دوازده پیوند به هشت رسیده: «سهام، صندوق‌ها، تابلوی زنده، شاخص‌ها،
نقشهٔ بازار» زیر منوی **بازار** و «غربالگر، پول حقیقی و حقوقی، بازدهٔ بازه،
استراتژی‌ها، فیلترها» زیر منوی **تحلیل** جمع شده‌اند. گروه‌بندی بر اساس *پرسش* است،
نه بر اساس منبع داده: «بازار» یعنی «الان بازار چه می‌کند» و «تحلیل» یعنی «کدام نماد
را نگاه کنم».

## اجرا / Run
```bash
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5002
```

That one command is the whole local stack. «به‌روزرسانی داده‌ها» needs four
processes — Redis, two Celery workers and the web app — and `python app.py` now
starts all of them and opens a browser once the port is accepting (see
`dev_boot.py`). Cold start, nothing running: ~2.5 seconds.

```
redis started for this session   redis_port=6379
celery worker starting           role=fetch        queues=updates
celery worker starting           role=maintenance  queues=maintenance
local services ready             redis=started  fetch=started  maintenance=started
 * Running on http://127.0.0.1:5002
analytics cache ready            backend=localhost:6379/0
```

| piece | what it is, and what its absence looks like |
|---|---|
| Redis | Celery's broker and result backend (DBs 1 and 2) plus the shared analytics cache (DB 0). Started from the bundled `.tools\redis\redis-server.exe` or a `redis-server` on `PATH`. If neither exists the app says so once, logs `analytics cache DEGRADED`, and no update can start. |
| Celery worker — `fetch` | `--queues=updates`: the process that actually fetches from TSETMC. Without it a job is created and stays `queued` — no symbol name on the page and nothing fetched. `market.start_job()` restarts it if it died, so a dead worker no longer looks like a broken updater. |
| Celery worker — `maintenance` | `--queues=maintenance`: the analytics rebuild, the finalize step and the reconciler. A SECOND process on purpose — see below. Without it a finished run stays in `finalizing` and the analytics keep serving the previous numbers. |
| browser | opened on a background thread once the port accepts, so the tab lands on a served page rather than a connection error. |

**Why two workers.** `--pool=solo` is the only pool Windows has, and it runs one
task at a time. `db.refresh_analytics()` walks twenty materialized views and
takes minutes (350 s at best, six measured). On one queue that rebuild blocks
everything behind it, and the symptoms point nowhere near the cause: batches sit
unclaimed so the page shows «در حال دریافت: …» with no symbol, «توقف» cannot take
effect because the worker never reads the flag, and `tasks.reconcile` — the
watchdog that recovers from both — is stuck in the same line. Splitting the
queues (`celery_app.py`) keeps the slow, rare work away from the work the page
depends on. Compose does not need a second service: its worker runs
`--pool=prefork --concurrency=4` over both queues, so a rebuild occupies one
child and three keep fetching.

Knobs: `BN_AUTOSTART_REDIS=0`, `BN_AUTOSTART_WORKER=0`, `BN_OPEN_BROWSER=0`,
`REDIS_SERVER_EXE`, `BN_WORKER_POOL`, `CELERY_FETCH_QUEUE`,
`CELERY_MAINTENANCE_QUEUE`.

All of it is limited to the `python app.py` path — under Gunicorn/Compose this
file is *imported*, and there both are declared services with their own
lifecycle. Everything it starts is detached, so Ctrl+C stops the app and leaves
Redis and the worker (and any update running on them) alive.

```powershell
.\start_local.ps1            # the same four, explicitly
.\start_local.ps1 -NoApp     # only the background services
.\start_local.ps1 -Stop      # stop them again
```

`dev_boot.py` and `start_local.ps1` both write `.tools\celery-worker.pid` (fetch)
and `.tools\celery-maintenance.pid` (maintenance), and both read them before
starting a worker — those shared files are the only thing keeping the two paths
from starting a second worker on the same queue. A Celery
control ping cannot do that job: the first one in a process costs ~9 seconds of
broker setup, and a `--pool=solo` worker cannot answer one at all while it is
busy.

The worker it starts uses `--pool=solo`, which is a requirement rather than a
preference: Celery's default prefork pool needs `fork()`, which Windows does not
have. That costs concurrency — one symbol at a time — so a **full** market run
belongs on the Docker Compose stack, where the worker runs prefork with
concurrency 4 (see `docker-compose.yml`).

A stuck job from a run that started while Redis was down can be closed with:

```python
python -c "import jobs; jobs.finish_job(<id>, status='failed', result='broker unavailable')"
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
| /filter-designer | `designer.ts` → `DesignerApp` / `GraphCanvas` | `/api/designer/*` | node-graph editor; not a table at all |
| /filter-designer/result | `designer_result.ts` → `ResultApp` | `/api/designer/run`, `/explain` | the matches, plus the same canvas read-only |
| /filter-backtest | `designer_backtest.ts` → `BacktestApp` / `EquityChart` | `/api/designer/backtest` | the report card; hand-written SVG, no chart library |

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

## طراحی فیلتر / The visual filter designer

`/filters` ships nineteen technical filters and `/strategies` sixteen strategies,
and both are Python we wrote. A user who wants «کندل پوشای صعودی، ولی فقط وقتی
حجم بالای میانگین ۲۰ روزه است» has to ask for a code change. `/filter-designer`
is where they draw it instead.

### Shape

```
templates/designer.html         the editor's shell
templates/designer_result.html  the results page's shell
frontend/src/designer.ts        entry — the editor
frontend/src/designer_result.ts entry — the results page
frontend/src/designer/
    DesignerApp.vue          state, toolbar, undo, save, examples
    GraphCanvas.vue          the pan/zoom board: SVG wires + absolute chips
                             (`readonly` for the results page's copy)
    NodeChip.vue             one box — caption, category, ports, verdict badge
    PalettePanel.vue         the parts, grouped and coloured by category
    InspectorPanel.vue       the selected chip's parameters, and its values
    ResultApp.vue            the results page: scope bar, diagram, table
    ResultPanel.vue          the matches, in the platform's own table chrome
    graph.ts                 pure model: geometry, wire routing, layout, lint
    draft.ts                 the localStorage handoff between the two pages
static/css/designer.css      everything under .dz / .dzr
filter_engine.py            the catalogue, the interpreter, and the scan
verify_designer.py          200+ checks: every block run against the live
                            market at every dropdown value, the frames
                            cross-checked against a hand-built weekly series,
                            and a browser that drives both pages
```

### Two pages, not one

«اجرا» **navigates**. The results first lived in a panel under the canvas, where
the list that is the entire point of running a filter got the bottom third of
the screen while the editor kept the rest. `/filter-designer/result` gives it the
whole page: full-width sortable table, «کپی نمادها», CSV, and a scope bar that
re-runs against another گروه without going back.

The graph reaches that page through the **draft** the editor already writes to
`localStorage` on every edit (`draft.ts`) — not through the URL, which cannot
carry kilobytes of JSON, and not through a server-side handoff token, which
would buy an expiry and a "this result has expired" screen for someone who left
the tab open over lunch. A **saved** filter needs none of that:
`/filter-designer/result?filter=<id>` reads the graph from the database, which is
what makes that URL a bookmark worth keeping, and `«بازگشت به طراحی»` carries the
same id back so the editor reopens *that* filter rather than whatever the draft
holds.

The table is **virtualized**, like `/screener` and `/performance` — a designed
filter is not a handful of matches («قیمت پایانی حداکثر ۵٪ زیر سقف یک‌ساله»
returns 407 today, and `MAX_ROWS` is 3,000), and rendering all of them is the
mistake this platform already fixed once. `<colgroup>` widths are COMPUTED
rather than written down, because «ستون خروجی» means the columns are only known
at run time.

On the right of the results page is **«فیلترهای دیگر»** — every saved filter and
every ready-made example, one click each, re-running in place. Running a screener
is not one question but "what does THIS one say, and that one", and routing that
back through the canvas turned a ten-second comparison into three page loads. The
rail updates the draft as it goes, so «بازگشت به طراحی» opens the filter you were
last looking at rather than the one you arrived with.

The results page carries the graph too, read-only and collapsed. That is not
decoration: it says which filter produced these rows, and it is where «چرا؟»
draws its answer. `GraphCanvas` takes a `readonly` prop for it — chips keep every
visual cue and lose every interactive one, and the whole surface becomes the pan
handle, because with the chips inert a dense graph leaves almost no background
to grab.

### The catalogue

Seventy blocks in ten categories, each with a palette sub-group (`sub`), because
a flat «اندیکاتور» shelf of forty-one names is a wall and what a user is looking
for — "a moving average", "something for volume" — is a shelf, not a search term:

| category | sub-groups | blocks |
|---|---|---|
| داده قیمت | تابلوی قیمت · کندل · سطوح | price fields (incl. درصد تغییر، دامنه، بدنه، سایه‌ها), constant, candle parts, Heikin-Ashi, pivots (classic/fib) |
| اندیکاتور | میانگین‌ها · نوسان‌نما · روند · کانال و نوسان · حجم | 41 — SMA/EMA/WMA/HMA/DEMA/TEMA/SMMA/VWMA · RSI/Stoch/StochRSI/CCI/%R/MFI/AO/ROC/Momentum/TRIX/UO/CMO · MACD/ADX/Ichimoku/PSAR/Supertrend/Aroon/Vortex/LinReg · Bollinger/%B/Keltner/Donchian/ATR/StdDev/Squeeze · OBV/RelVol/VWAP/CMF/A-D/Force |
| الگوی کندلی | الگوها | 24 patterns, evaluated at EVERY bar (not just the last) so «در N کندل اخیر» works |
| محاسبات | محاسبات | arithmetic, unary functions, if/else |
| آماری | پنجرهٔ غلتان · دنباله | rolling max/min/sum/avg/median/stdev/range, change, position-in-range, streak, bars-since |
| مقایسه · تقاطع · منطق | — | comparisons, between, cross up/down, rising/falling, And/Or/Not/at-least-N |
| اطلاعات نماد | اطلاعات نماد | ticker/name/sector/sub-sector/panel/market, text match, ticker list, bar count |
| خروجی | خروجی | the output node, and result columns that carry their own sort direction |

Nothing reads حقیقی/حقوقی flow, order-book depth, free float or market cap:
`stockpricehistory` is OHLC + final + volume + value + trade count and that is
the whole surface. A «قدرت خریدار حقیقی» block would be a chip that always
evaluates to nothing, which is worse than its absence.

`verify_designer.py` wires **every** block into a real graph and runs it against
the live market, because a node type is four things — a catalogue entry, an
`_eval_node` branch, a line in `bars_needed()` and a line in `fields_needed()` —
and two of the three ways to get it wrong fail *silently*: a missing
`fields_needed()` entry is a KeyError on an unloaded column, which `run()`
swallows per symbol, so the filter just matches nothing.

### Silence is the enemy

`run()` catches a per-symbol exception so one bad symbol cannot kill a
market-wide scan — and for a long time it only *logged* that. Which means a
broken block does not raise; it quietly matches nothing, and "this filter is
strict" and "this filter is broken" look identical from the outside.

Two bugs lived in exactly that gap. «تابلو» was offered by the symbol node while
`_meta()` never selected the column, so it returned an empty string for every
stock. And `٪B` — or any indicator — pointed at the `pct` source raised
`TypeError` on every symbol in the market, because «درصد تغییر» had no previous
bar to measure the first candle against and handed back a `None` into
`db._sma_series`, which assumes clean floats.

So `run()` now returns `errors`, the results panel prints it («⚠ N نماد بررسی
نشد») instead of showing a short list, and `verify_designer.py` wires every block
into a real graph with **every dropdown value** — 435 combinations across both
سهام and صندوق‌ها — and asserts the count is zero. That sweep is what found both.

### A price of zero is not a price

347 rows in this database carry `adj_open = adj_high = adj_low = 0` beside a
valid `adj_close` and `adj_final` — the exchange settled the symbol but recorded
no intraday range; «ثاژن» alone has thirty. The panel's WHERE clause only
requires close and final to be positive, so those bars arrive, and the loader
used to substitute `0.0`.

Which is how «شکست کانال دانچیان» reported ثاژن as a breakout: its twenty-bar
highest high came out as **zero**, and 6,300 > 0 is true. A false positive
manufactured by the loader, not by the data — the data honestly said "no high
recorded" and the code answered "the high was zero rials".

`_load_columns()` now repairs such a bar to a FLAT one at its settlement price
(open = high = low = final), which is what it actually was. That keeps every
series a clean list of floats — no `None` for fifteen indicator functions to
guard — and makes the bar's range zero, which is the truth.

### The engine (`filter_engine.py`)

A graph is **data, never code**: it arrives as JSON, is validated against
`NODE_TYPES`, and is walked by a fixed interpreter. There is no `eval`, no
import, and nothing a saved filter can reach except the price panel it is handed.

Everything inside is a **series aligned to the symbol's bars**, oldest→newest,
with `None` for "not computable yet". A comparison turns two number series into
a boolean series; the output node asks whether it was true within the last
`within` bars. Scalars are carried unexpanded and broadcast only when they meet
a series. That single rule is what keeps the interpreter under 200 lines and
makes every node composable with every other one.

Three things keep a market-wide run (≈800 symbols × ≈400 bars) under a second
once warm:

- **`bars_needed()`** walks the graph before the query and asks for only the
  history the deepest lookback needs, bucketed to 150/300/500/800/1300 so similar
  graphs share a panel. `close > open` reads 150 bars, not 800. A time frame
  *multiplies* — a 20-period weekly average is 20 weeks, so ~100 sessions — and
  when a graph wants more than the deepest bucket holds, `run()` says so
  (`clipped`) instead of returning an empty table full of still-warming values.
- **`fields_needed()`** narrows the `SELECT` to the columns the graph actually
  reads, and the panel is cached **column by column** in-process — so adding a
  `high` node to a running graph costs one column's query, not four.
- **Signatures (`_sign`)** give identical sub-graphs one identity, so the six
  `Ichi ۹,۲۶,۵۲` chips a readable Ichimoku layout needs are computed once per
  symbol rather than six times. The reference product's own examples draw that
  way, and without this they were the slowest thing in the catalogue.

Parameters are **clamped, not rejected** — a period that arrives as 10⁹ runs at
the documented ceiling, because an error the user cannot act on is worse. What
*is* rejected is structural: an unknown node type, a cycle, no output node, two
output nodes, or a graph past `MAX_NODES` — each with a Persian message the
canvas shows verbatim.

### «تایم فریم» — weekly and monthly, from the daily table

Every market-data, indicator, candle and rolling-statistic block carries a time
frame. There is no second table: a frame is a **re-indexing** of the daily bars,
and the whole feature is three functions and one rule —

```
_frame_index   daily bar → frame bar number      (from a bucket column)
_to_frame      a daily-aligned series  → one value per frame bar (its last)
_from_frame    a frame-aligned series  → back onto the daily bars (held)
```

`_eval_in_frame()` wraps `_eval_node()` in those three, so **no indicator was
touched to gain a time frame**: its inputs are re-indexed on the way in and its
outputs on the way out, and it never learns that anything happened. The same
wrapper applies «برگشت به عقب», which is why that could be added to forty blocks
at once as well.

Two choices that are not arbitrary:

- **The week starts on Saturday.** The bucket is
  `(date - DATE '2000-01-01') / 7`, and 2000-01-01 was a Saturday. An ISO
  (Monday) week would put Saturday's session in with the *previous*
  Sunday-to-Wednesday and split every real trading week across two buckets.
- **The month is the Jalali month**, off `j_date` — «شهریور» is a month a user
  can reason about, «August» straddles two of them.

Frames are numbered by **arrival**, not by bucket value, so a symbol halted for
three weeks produces no empty frame bars for a moving average to average holes
over. Resampling is **per column and lazy** (`_FrameBars`): a monthly RSI on
«پایانی» resamples one column, not the six the panel happens to hold — which is
the difference between 0.2 s and 10 s on a market-wide run.

The last frame bar is deliberately **partial** (the week so far). A filter that
must see only completed frames says so with «برگشت به عقب ۱».

### The blocks the reference guide asks for

Beyond the indicator set, the catalogue carries the guide's own vocabulary:

| Block | What it answers |
| --- | --- |
| «سقف و کف مجاز» | the day's price band from the previous settlement, the distance to each, and **صف خرید / صف فروش**. «خودکار» reads the band off the symbol's own market (بورس/فرابورس ۵٪, زرد ۳٪, نارنجی ۲٪, قرمز ۱٪, ETF ۱۰٪) |
| «حمایت و مقاومت» | nearest confirmed swing high/low and the distance to each — confirmed *k* bars late, because a peak is not a peak until *k* more bars have printed |
| «فرمول‌نویسی» | one arithmetic expression instead of six boxes |
| «فهرست نمادها» | market / board / sector / ticker list in one box |
| «برچسب سیگنال» | fills a «سیگنال» column with خرید / فروش, so one filter can emit both |
| «هشدار» | turns a saved filter into a watcher (below) |
| «توضیحات» | a note on the canvas that computes nothing |

**«فرمول‌نویسی» is a parser, not an `eval`.** A recursive-descent parser over a
fixed grammar builds five node shapes and nothing else; the evaluator can only
call what is in `_FN`, and the variables it can name are the four wired inputs
plus the same price fields «داده قیمت» offers. Comparisons yield 1/0 so
`if(close>final,1,0)` works, and chained comparisons are a syntax error rather
than a silent `(a<b)<c`. ASTs are cached by source text, and the whole thing is
evaluated **series-wise** with the same combinators every other block uses, so a
formula costs what the equivalent boxes cost. `verify_designer.py` walks the
engine's syntax tree to assert no `eval`/`exec`/`getattr` call exists to fall
back on.

### «بلاک هشدار» — a saved filter that watches for itself

Dropping an «هشدار» block on the canvas and **saving** arms the filter; nothing
else switches it on. `evaluate_filter_alerts()` runs on the same schedule as the
price alerts (`tasks.evaluate_alerts`, i.e. right after an update and on the Beat
tick) and writes one `alert_events` row per **newly** matched symbol.

Which filters are armed is a jsonb containment query
(`graph @> '{"nodes":[{"type":"alert"}]}'`), so arming is something the user does
by dragging a box — there is no second switch elsewhere to forget. The
de-duplication state is the events themselves, keyed `filter:<id>`: with «فقط
یک‌بار» a ticker is reported once, otherwise once per session. A user-supplied
message template is substituted by **replacement, never `str.format`** —
`{0.__class__}` in a format string is a way to walk the object graph. One pass is
capped at `FILTER_ALERT_MAX` (40) filters, because each one is a market-wide
scan.

### What the guide asks for that this data cannot answer

Deliberately **not** built, rather than half-built:

| Guide block | Why not |
| --- | --- |
| «بلاک سفارش» / «اطلاعات حساب» / «پرتفو» | no broker integration exists |
| «بلاک تلگرام» | no bot is configured; «هشدار» delivers to the in-app feed instead |
| «بلاک نمودار» / «خط روند اصلی» (drawing lines on a chart) | a charting feature, not a screening one. The *screening* half — "is price arriving at a level it was rejected from before" — is «حمایت و مقاومت» |
| «بلاک ویژگی اضافی نماد» | user-defined per-symbol metadata needs a place to enter and store it; nothing here has one |

**Three entries left this table.** «بلاک حقیقی و حقوقی», «بلاک عرضه و تقاضا» and
the حجم مبنا / EPS / تعداد سهام half of «بلاک داده عمومی» said "not collected"
here, with a note that each was *one ingestion job away — a data order, not a
designer change*. That order was placed: `ri_history` and `market_snapshot`
now exist (see **دادهٔ فراتر از قیمت** below) and the palette carries a
**«حقیقی و حقوقی»** shelf with two blocks:

- **«حقیقی و حقوقی»** — a per-session *series* on the price calendar, so it
  takes a timeframe and a shift and feeds a moving average or a cross like any
  price: خالص ورود پول حقیقی, قدرت خریدار, سرانهٔ خرید/فروش, and the raw
  values, volumes and head-counts behind them.
- **«داده بنیادی»** — one *scalar* per symbol from the newest board snapshot:
  EPS, P/E, ارزش بازار, حجم مبنا, تعداد سهام, and the buy/sell queue values and
  their per-capita pair. No timeframe, because there is no history to reframe —
  and **refused by the backtester**, because replaying today's P/E over bars
  from two years ago is look-ahead the engine cannot remove.

Both blocks refuse to run when their dataset has never been fetched, naming the
«نوع داده» that fills it. That is deliberate: an empty results table is
indistinguishable from a threshold set too high, and the user's next move would
be to loosen a filter that was never wrong.

Two places where this catalogue deliberately differs from the guide rather than
lacking something:

- **«انتخاب خروجی»**. The guide gives MACD, Bollinger, Stochastic and Aroon a
  dropdown to pick *which* component the block emits. Here every component is
  its own output port, so one MACD chip feeds the line, the signal and the
  histogram at once instead of needing three chips set to three values of a
  dropdown.
- **«برگشت به عقب» vs «برگشت به عقب قیمت»**. The guide lists both — move the
  indicator's line back, or move the price back and then compute. For every
  indicator here those are the same list: each is a translation-invariant
  function of the price series, so `SMA(shift(px, k), n) == shift(SMA(px, n), k)`.
  Two dials that do one thing is worse than one, so there is one.

### «چرا این نماد آمد؟»

A screener that only says yes or no cannot be debugged: the user raises a
threshold, the list empties, and nothing says which of eleven conditions went
false. `/api/designer/explain` returns every node's last bars for one symbol; the
results page opens its diagram, paints ✓/✕ on each chip, tints the satisfied
wires green, and lists the same numbers as a table underneath for anyone who
would rather read them in one column.

The values are read **at the bar the filter fired on**, not at the last bar —
`evaluate()` returns that offset. For any filter with «در N کندل اخیر» above 1
the last candle is usually the one where the condition has gone false again, so
reading the last bar would paint every chip ✕ next to a symbol the filter had
just returned.

### Concurrency

One run is real CPU in the web worker and does not release the GIL, so runs take
a slot (`DESIGNER_RUN_SLOTS`, default 2 per worker) and wait
(`DESIGNER_RUN_WAIT`, default 25 s) rather than piling up; a caller that cannot
get in is told to try again in Persian instead of being held until nginx times
out.

### Storage

`custom_filters` (migrations 0006 and 0007, and `filter_engine.ensure_tables()`
at boot for the laptop path). The graph is one JSONB column **including the node
coordinates**: nothing ever queries inside a graph, the catalogue grows a node
type whenever someone asks for an indicator, and a filter that reopens as a pile
of chips in the corner is a black box rather than a design. Saved filters are
private to their account; «برون‌بری» hands the same JSON to a file so one can be
sent to someone else.

## دادهٔ فراتر از قیمت / The datasets beyond price

Until now every page read one table: daily adjusted OHLCV. `finpy-tse` publishes
four more things worth having, and this section is what was built from them,
what was deliberately not, and why.

### What each finpy function is worth here

Verified against the **installed package**, not the printed guide — two of them
behave differently:

| Function | Data | Verdict |
| --- | --- | --- |
| `Get_Price_History` | daily adjusted OHLCV | already the whole platform (`tse_fetch.py`) |
| `Get_RI_History` | حقیقی/حقوقی: counts, volumes and values of buys and sells | **built** — ⚠️ raises with its own defaults; see below |
| ten `Get_*I_History` + `Get_SectorIndex_History` | 10 market indices + 40 sector indices | **built** — cheap and back-fillable |
| `Get_MarketWatch` | the **whole market in one request**: ~1,540 symbols and a 5-level order book in 3.3 s | **built** — the highest-value call in the library |
| `Get_USD_RIAL` | daily free-market USD/IRR | **built** |
| `Build_Market_StockList` | reference list with sub-sector, panel and codes | **built** — `stocks` had 810 rows against 1,543 live symbols |
| `Get_Queue_History` | queue values at the close, per symbol | **built** — ⚠️ finpy returns nothing at all for it; see below |
| `Get_IntradayOB_History` | the full five-level order-book tape | **built** — ⚠️ same finpy bug |
| `Get_IntradayTrades_History` | every executed trade | **built** — ~35,000 rows per liquid symbol-day |
| `Get_ShareHoldersInfo` | holders above 1% | **built** — ⚠️ raises `FileNotFoundError` on modern pandas; see below |
| `Build_PricePanel`, `Get_60D_PriceHistory` | convenience wrappers over the price fetch | **skipped** — the project's own parallel pipeline already does this, better and resumably |

**Everything in the guide is now ingested except the last row**, and that one is
a wrapper around a fetch this project already owns.

### Four finpy bugs this integration works around

All four are undocumented, all four fail silently or misleadingly, and
`verify_marketdata.py` asserts against the installed package rather than the PDF:

1. **`Get_RI_History` raises with its own defaults.** With `alt=False` — the
   default — every call dies with `AttributeError: Can only use .dt accessor
   with datetimelike values`: TSETMC's primary endpoint now returns a shape
   finpy's date handling cannot parse. Every حقیقی/حقوقی row in this platform
   exists because `tse_fetch.py` passes **`alt=True`**.
2. **`Get_SectorIndex_History` does not match loosely.** The guide says «بانک»,
   «بانکی» and «بانکی‌ها» all resolve; in this build it does an *exact* lookup
   against a hard-coded list of forty strings and, on a miss, **scrapes a Google
   search page** for the web-id. So `market_data.SECTOR_INDICES` mirrors finpy's
   own forty names — feeding it this database's group names («خودرو و ساخت
   قطعات») misses all forty times.

3. **The intraday order book is not "unavailable" — it crashes.** Both
   `Get_IntradayOB_History` and `Get_Queue_History` go through finpy's
   `__Get_Day_LOB__`, which renames TSETMC's order-book columns **by position**:

   ```python
   data.columns = ['Time','Depth','Buy_Vol','Buy_No','Buy_Price', …]   # 8 names
   ```

   TSETMC has since added a `title` column, so nine columns arrive where eight
   are expected and the assignment raises `ValueError: Length mismatch`. Both
   callers wrap the day in a bare `except:` and print

   ```
   WARNING: The following days data is not available on TSE website…
   ```

   …which is why those two datasets looked like missing upstream data. They are
   not: the raw endpoint returns 6,991 rows for a day finpy reports as empty.
   `tse_fetch._day_lob()` reads the same endpoint and selects **by name**, which
   is both the fix and immune to the next column TSETMC adds.

   While reimplementing it: finpy's queue function also assigns `psGelStaMax`
   (the *upper* band) to `Day_LL` and the minimum to `Day_UL` — **swapped** in
   its output. Ours are not, and `verify_marketdata.py` asserts `day_ul > day_ll`
   on every stored session.

4. **`Get_ShareHoldersInfo` cannot run on modern pandas.** It reaches the
   symbol's ISIN with `pd.read_html(r.text)`, and pandas ≥ 2.1 treats a raw HTML
   string there as a *file path* — so every call dies with
   `FileNotFoundError: [Errno 2] No such file or directory: <!doctype html>…`.
   `tse_fetch._read_html_str_shim()` wraps `read_html` so a string argument is
   handed through `StringIO`; everything else in finpy's function is correct and
   is left alone.

### The tables (`market_data.py`)

| Table | Source | Shape |
| --- | --- | --- |
| `ri_history` | `Get_RI_History` | one row per symbol per session, back-fillable |
| `index_history` | the eleven index functions | one row per index per session; market and sector indices share it, keyed by `index_key` |
| `usd_rial` | `Get_USD_RIAL` | one row per day |
| `market_snapshot` | `Get_MarketWatch` | one row per symbol per session, **forward-only** |
| `order_book` | `Get_MarketWatch` | five rows per symbol, **latest only** |
| `queue_history` | the intraday tape | one row per symbol per session, back-fillable |
| `intraday_orderbook` | the intraday tape | the full five-level tape, ~7,000 rows per symbol-day |
| `intraday_trades` | `GetTradeHistory` | every executed trade, ~35,000 rows per liquid symbol-day |
| `shareholders` | `Get_ShareHoldersInfo` | holders above 1%, **latest only** |

Every one is keyed so a re-fetch **replaces** rather than duplicates — the same
property `tse_fetch.store()` gives the price tables, and for the same reason:
under `acks_late` a worker killed after writing and before acknowledging is
handed the same work again, so "twice" is normal, not hypothetical.

`market_snapshot` is keyed on `(ticker, j_date)`, so running the snapshot twice
in a day overwrites that session's row rather than appending. `captured_at` is
stored because it is the only thing that says whether today's row is end-of-day
data: a board captured at 06:32 shows zero volume and every symbol flat, and
that is the truth about that moment, not a bug. The `/live` page prints the
capture time above everything else for exactly that reason.

`order_book` is replaced wholesale, never merged. It is a photograph, and half
of an old one mixed into a new one is not a state the market was ever in — a
symbol missing from the newest response genuinely has no current book, and
showing yesterday's queue as today's is what would mislead someone into a trade.

### The update page grew a dimension

Adding five data types to `/update` needed **no new job machinery**. Everything
about a run — the work list, the claim, the retry, the progress tiles, «توقف»,
«مکث» and resume — is written against `kind` and nothing else (`jobs.py`,
`tasks.py`, `market.py`), so a new dataset is an entry in `tse_fetch.KINDS` plus
a handler, and it inherits all of it. The form now offers eight types in four
groups, each with a sentence saying what it costs, because they range from three
seconds (the board) to several hours (a full حقیقی/حقوقی back-fill) and a form
that presents those identically invites someone to start the wrong one.

Two changes were needed around the edges:

- **`finalize_update` skips the analytics rebuild for non-price runs.** The
  twenty materialized views are built from the price tables and read nothing
  else, so a run that fetched شاخص‌ها or the dollar has left every one of them
  exactly as correct as it was. Rebuilding anyway is six measured minutes of
  identical output, and it would park the page on «در حال بازسازی تحلیل‌ها…»
  after a three-second snapshot. The cache version is still bumped.
- **`nightly_update` derives each dataset's «از تاریخ» from its own table.**
  حقیقی/حقوقی can legitimately be a month behind prices while a back-fill runs,
  and taking the start date from the price table would ask TSETMC for a window
  that dataset has never covered, store nothing, and leave the gap open night
  after night. A dataset that has **never** been fetched is skipped by the
  schedule entirely: `next_day(None)` would hand it `FIRST_JALALI` and quietly
  start five years × 800 symbols at 23:00. That first back-fill is an operator's
  decision, made once on the page where the cost is written next to the button.

**The queue values are collected twice, on purpose.** `market_snapshot` gets
them for the whole market in one ~5-second request but only from the day the
feature is switched on; `queue_history` gets them one symbol-day at a time and
reaches as far back as TSETMC keeps the tape. So the snapshot is the standing
nightly job and the history is the tool you point at a symbol you care about.
Where both cover the same session they agree — same numbers, same instant.

**The three intraday datasets are not on the schedule, and should not be.** One
request per symbol-day means «ریز معاملات» across the market for a month is
~24,000 requests. They are per-symbol tools: `market.HEAVY_KINDS` marks them and
the update form refuses to start one market-wide unless «همهٔ نمادها» is ticked
deliberately. The tick tape is worth the trouble when you want it — its min
price, max price and summed volume reproduce the daily bar's low, high and
volume **exactly**, which is the strongest correctness check in this whole
feature and is asserted by `verify_marketdata.py --live`.

**Everything the update page can write, it can also delete.** Before this, a
wrong-year index sweep could be created from the UI and undone only with psql.
`market_data.DELETABLE` maps each dataset to its table, ticker column and date
column; a dataset with no date column ignores the range and can only be cleared
wholesale, which is the truth about a snapshot. The range stays mandatory unless
`all_history` is passed explicitly — the same guard `db.delete_price_history`
has, and for the same reason.

The beat runs the board at **13:30** — the only one whose timing is substantive.
The photograph worth keeping is the one taken after the 12:30 close, when the
queues have frozen; that is what makes `market_snapshot` a usable end-of-day
queue history, and it is the whole reason `Get_Queue_History` is not fetched
symbol by symbol. Indices and the dollar follow the price runs at 22:30, and the
two long حقیقی/حقوقی sweeps go last.

### پر کردن پایگاه داده / Filling the database

The /update page runs **one job at a time**, deliberately — two fetches
competing for TSETMC connections is the main source of the timeouts that look
like "no data". Filling an empty database therefore means starting thirteen
jobs in the right order and waiting for each, which is what `backfill.py` does:

```bash
python backfill.py --list      # what it would do, with a time estimate
python backfill.py             # run it
python backfill.py --full      # whole history, not just since the last date
python backfill.py --only index,usd
python backfill.py --symbol فولاد --intraday --from 1405-05-01 --to 1405-06-09
```

**The order is not arbitrary.** `symbols` goes first because it refreshes the
`stocks` reference table and every per-symbol job iterates over that table — a
symbol missing there is invisible to all of them. Prices come before the
datasets that join against them. The two long حقیقی/حقوقی sweeps go last.

**It measures coverage, not recency.** `market.dataset_latest()` returns
MAX(j_date), so a table holding ONE symbol fetched to yesterday reports itself
as fully up to date — and an incremental run then computes an empty window and
fetches nothing, for ever, while 809 symbols stay empty. That is right for the
nightly job and exactly wrong for a back-fill, so `backfill.py` counts *symbols
with any row* and widens the window when coverage is below 90 %:

```
  حقیقی/حقوقی سهام        ۸۱۰ مورد  ≈ —          به‌روز است   [پوشش ۸۰۹/۸۱۰]
  سهامداران عمده          ۸۱۰ مورد  ≈ ۵۴ دقیقه   عکس لحظه‌ای  [پوشش ۱/۸۱۰]
```

**It will not run the intraday datasets across the market**, and that is a
measurement rather than caution: at 1.7 s per symbol-day, 810 symbols over one
year is ~92 hours of continuous fetching. They run only for a named `--symbol`.

Ctrl+C abandons the *watch*, not the fetch — the workers are detached, so the
job carries on and the next run picks up where it left off.

### ⚠ Restart the workers after adding a data type

The web process and the Celery workers are **separate long-running processes**.
`python app.py` restarts the web half; the workers are started once and keep
running, so after a change to `tasks.py` / `tse_fetch.py` a worker is still
executing the module it imported days ago.

That is not subtle. Adding «حقیقی/حقوقی صندوق‌ها» and restarting only the web app
produced this, measured:

```
پردازش‌شده ۱ / ۲۹۳    موفق ۰    ناموفق ۱    زمان سپری‌شده ۹۰۹ث
```

Every ticker was dying on `KeyError('etf_ri')` inside the stale worker, and
because `acks_late` redelivers the batch it was claimed again and again — six
attempts per symbol, twelve rows stuck in `'running'`, fifteen minutes, and
**nothing on the page to explain it** (a `KeyError` is not a `FetchError`, so it
never reached `mark_failed`).

Three things now stop that:

1. **`tasks.fetch_batch` refuses an unknown kind**, once, marking every ticker
   in the batch failed with a message that names the actual remedy — «کارگر این
   نوع داده را نمی‌شناسد … کارگرهای Celery را دوباره راه‌اندازی کنید». Not
   retried: no amount of waiting teaches a running process a new module.
2. **`dev_boot` restarts a worker whose process is older than its own source**
   (`_WORKER_SOURCES`), so the ordinary `python app.py` fixes it by itself. A
   job in flight defers the restart — abandoning claimed symbols is worse — and
   `BN_RESTART_STALE_WORKER=0` turns it off.
3. **`tests/test_stale_worker.py`** pins all of the above, including that a
   kind offered on the update form always exists in `tse_fetch.KINDS` — the
   static half of the same failure, which no restart would fix.

To restart them by hand: `.\start_local.ps1 -Stop` then `python app.py`.

### The join that makes the designer blocks correct

`filter_engine._load_columns` pulls the حقیقی/حقوقی columns in with a
`LEFT JOIN ri_history ON (ticker, date)` rather than loading them separately,
and that join is the correctness argument: it aligns every value to the **price**
calendar, so «میانگین ۵ روزهٔ خالص حقیقی» and «RSI روی پایانی» index the same
bars. Two independently-loaded series would drift by one on any session the two
tables disagree about — and they disagree routinely while a back-fill is running.
`LEFT`, not `INNER`: an inner join would silently shorten the price series for
every symbol whose RI back-fill is behind, quietly changing what every *other*
indicator in the same graph computes.

A missing RI row stays **`None`**, not `0.0` — `_NULLABLE_COLS` exempts those
columns from the loader's usual repair. Zero would say «هیچ حقیقی‌ای نخرید»,
which is a claim about the session; the missing row only says the data has not
been fetched. The same distinction runs through every division in
`_flow_series`: a session with no retail buyer has an *undefined* سرانه, and
that matters twice over, because zero would make it match both «قدرت خریدار > ۲»
and «قدرت خریدار < ۰.۵».

Weekly and monthly frames sum the حقیقی/حقوقی columns — including the
head-counts — so a weekly سرانه is the week's rials over the week's
buyer-sessions, which is the published definition. Averaging the daily ratios
instead would weight a dead Wednesday the same as a heavy Saturday.

### Verifying

```bash
python verify_marketdata.py          # schema, arithmetic, fetch layer, routes, nav
python verify_marketdata.py --live   # …and one real fetch of each dataset
```

Part B is the point of that file: it asserts the two finpy facts above against
the installed package, and Part D checks that the fetched rows **join to real
price bars** — a fetch that succeeds but stores dates the price table does not
have is worse than a failure, because every حقیقی/حقوقی block then reads `None`
and every filter using one matches nothing, silently.

## چرا یک به‌روزرسانی گیر می‌کند / Why an update stalls, and what stops it

A symptom worth recognising, because everything on the /update page says the
system is coping: «در حال اجرا…», a symbol count that stops moving, a growing
«N ثانیه از آخرین پیشرفت گذشته», the watchdog reporting «بازیابی خودکار در
جریان است» and «۹۶ نماد دوباره در صف قرار گرفت» — and nothing happens.

That is a **wedged consumer**, and the give-away is in the database: some ticker
is `pending` with `started_at` set and `finished_at` null, nothing is `running`,
and the Redis `updates` list is deep. The watchdog is working perfectly; there is
simply no worker left to hand the messages to.

How it happened once, end to end:

1. `finpy_tse` fetches every symbol with `requests.get(url, headers=headers)` and
   never passes `timeout`. **A `requests` call with no timeout waits for ever.**
2. A TCP connection to TSETMC stalled without a reset — ordinary on a network
   where a filtering middlebox blackholes a connection rather than refusing it.
3. The Windows worker runs `--pool=solo`, which executes the task inline in the
   **consumer thread**, so the worker stopped accepting messages at the same
   instant. 212 batches queued behind one blocked read.
4. `task_time_limit` (1800 s, celery_app.py) did not fire, and could not: Celery
   implements time limits by having the **prefork** pool kill the child running
   the task. Solo has no child. The ceiling was configured and inert — which is
   why nothing in the logs said anything was wrong.

The fix is `tse_fetch._install_http_timeout()`: `requests.Session.request` is
patched once per process to fill in `TSE_HTTP_TIMEOUT` (default `10,45` —
connect, read) whenever the caller supplied none. A stall now raises inside
`fetch()`, becomes a `TransientFetchError`, and is retried and recorded like any
other flaky symbol. `socket.setdefaulttimeout()` would have been the other way to
do it without touching finpy, and is wrong here: it is process-global and would
also apply to psycopg2's connections and to the blocking Redis read Celery's
broker sits in. `tests/test_fetch_http_timeout.py` locks the behaviour down, and
`dev_boot` now logs a warning at worker start naming the solo-pool gap rather
than leaving it to be rediscovered.

**If you meet a stalled job anyway:** restart the fetch worker. Nothing is lost —
`jobs.claim_ticker()` refuses a symbol that already finished and `tse_fetch.store()`
replaces rather than appends, which is what "resumes without losing or
duplicating tickers" reduces to. The queued batches drain as no-ops.

## اسکریپت‌های اصلی / Original scripts
اسکریپت‌های قبلی (`stock_updater.py`، `etf_updater.py`، `stock_gainer.py`،
`etf_gainer.py`، `search.py`, …) دست‌نخورده باقی مانده‌اند؛ ماژول‌های به‌روزرسانی
مستقیماً توسط پلتفرم استفاده می‌شوند و بقیه مرجع منطق تحلیل‌اند.
