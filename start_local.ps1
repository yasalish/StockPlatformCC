# start_local.ps1 — راه‌اندازی محلی: Redis + کارگر Celery + برنامه
# ---------------------------------------------------------------------------
# On a laptop, «به‌روزرسانی داده‌ها» needs three processes, not one, and this is
# the script that starts them. docker-compose.yml already declares all six
# services for production; there was nothing equivalent for Windows, so the two
# background services simply were not running — and the symptom is misleading:
# the update page shows «در حال محاسبه… صبر کنید» forever, because the job row is
# created and then nothing ever claims it.
#
#   1. redis-server — Celery's broker AND result backend (databases 1 and 2),
#      and the shared analytics cache (database 0). Without it the app still
#      serves pages (cache.py falls back to an in-process cache and says so in
#      the log), but NO update can ever start.
#   2. celery worker (fetch)       — the process that actually fetches from
#      TSETMC, on --queues=updates. Without it the job is queued and stays
#      queued.
#   3. celery worker (maintenance)  — --queues=maintenance: the analytics
#      rebuild, the finalize step and the reconciler. It is a SECOND process on
#      purpose. --pool=solo runs one task at a time, and the rebuild takes
#      minutes; sharing one worker with the fetches meant a rebuild blocked the
#      whole update — no symbol names on the page, «توقف» unable to take effect,
#      and the reconciler that recovers from both stuck behind it too.
#   4. app.py — the web app itself.
#
# Step 1 is now belt-and-braces for the common case: since redis_boot.py,
# `python app.py` starts Redis on its own, so running this script only for the
# worker (-NoApp) will normally find Redis already up and say so. The step stays
# because -NoApp is also how the worker is started BEFORE the app, and because
# it is what -Stop is symmetric with.
#
# --pool=solo is not a preference, it is a requirement: Celery's default
# prefork pool needs fork(), which Windows does not have, and a worker started
# without it accepts tasks and then fails them. It costs concurrency — one
# symbol at a time instead of four — which on this connection means a full
# market run takes hours. docker-compose.yml uses prefork with concurrency 4,
# and that is the right way to run a full rebuild. The one-task-at-a-time limit
# is also why the maintenance work runs in its own process here.
#
# Usage:
#   .\start_local.ps1              # Redis + both workers + app
#   .\start_local.ps1 -NoApp       # only the background services
#   .\start_local.ps1 -Stop        # stop the workers and Redis this script began
param(
    [switch]$NoApp,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$redisExe = Join-Path $root ".tools\redis\redis-server.exe"
$redisCli = Join-Path $root ".tools\redis\redis-cli.exe"
$logDir = Join-Path $root ".tools"

function Test-Redis {
    # Ask Redis itself rather than looking for a process name: another Redis
    # (a service, WSL, Docker Desktop) answering on 6379 is just as good, and
    # starting a second one on the same port would fail confusingly.
    try { (& $redisCli ping 2>$null) -eq "PONG" } catch { $false }
}

if ($Stop) {
    Get-Process redis-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*celery*worker*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    # Leaving a pid file behind would be harmless only until the id is reused;
    # dev_boot would then think a worker is running and never start one.
    Remove-Item (Join-Path $logDir "celery-worker.pid") -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $logDir "celery-maintenance.pid") -ErrorAction SilentlyContinue
    Write-Host "Redis و کارگر متوقف شدند." -ForegroundColor Yellow
    exit 0
}

# ---- 1. Redis -------------------------------------------------------------
if (Test-Redis) {
    Write-Host "Redis از قبل در حال اجراست." -ForegroundColor DarkGray
} else {
    if (-not (Test-Path $redisExe)) {
        throw "redis-server.exe یافت نشد: $redisExe"
    }
    Start-Process -FilePath $redisExe `
        -ArgumentList (Join-Path $root ".tools\redis\redis.windows.conf"),
                      "--logfile", (Join-Path $logDir "redis-server.log") `
        -WindowStyle Hidden
    Start-Sleep -Seconds 2
    if (Test-Redis) {
        Write-Host "Redis بالا آمد (پورت ۶۳۷۹)." -ForegroundColor Green
    } else {
        throw "Redis بالا نیامد — به .tools\redis-server.log نگاه کنید."
    }
}

# ---- 2. Celery workers ----------------------------------------------------
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
# TSETMC is a domestic host and this machine's HTTP_PROXY points at a local
# tunnel client for reaching sites abroad. Sending the fetch through it made
# every symbol fail with a ProxyError/timeout; direct, the same request answers
# in under four seconds. tse_fetch.py sets this too — it is repeated here so the
# bypass is visible in the process that does the fetching.
$env:NO_PROXY = "tsetmc.com,.tsetmc.com,old.tsetmc.com,cdn.tsetmc.com,www.tsetmc.com,127.0.0.1,localhost"
$env:no_proxy = $env:NO_PROXY

function Start-Worker {
    param([string]$Role, [string]$Queue, [string]$LogBase)

    # Match on the QUEUE, not on "celery worker": the two workers differ only in
    # that argument, and matching the command name would find the fetch worker
    # and conclude that the maintenance one was already running.
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*--queues=$Queue*" }
    if ($existing) {
        Write-Host "کارگر $Role از قبل در حال اجراست (PID $($existing.ProcessId))." -ForegroundColor DarkGray
        return
    }
    # -n keeps the node names distinct. Two workers called celery@HOSTNAME share
    # one control mailbox, so `celery inspect active_queues` sees only one of
    # them and a broadcast can be answered by the wrong process.
    $proc = Start-Process -FilePath "python" `
        -ArgumentList "-m", "celery", "-A", "celery_app", "worker",
                      "--loglevel=info", "--queues=$Queue", "--pool=solo",
                      "-n", "$Role@%h" `
        -WorkingDirectory $root `
        -RedirectStandardOutput (Join-Path $logDir "$LogBase.log") `
        -RedirectStandardError (Join-Path $logDir "$LogBase.err.log") `
        -WindowStyle Hidden -PassThru
    # dev_boot.py reads these files to decide whether `python app.py` needs to
    # start a worker. Without them the two paths cannot see each other's workers
    # and you end up with two on the same queue.
    Set-Content -Path (Join-Path $logDir "$LogBase.pid") -Value $proc.Id -Encoding ascii
    Write-Host "کارگر $Role راه افتاد — گزارش در .tools\$LogBase.log" -ForegroundColor Green
}

Start-Worker -Role "fetch"       -Queue "updates"     -LogBase "celery-worker"
Start-Worker -Role "maintenance" -Queue "maintenance" -LogBase "celery-maintenance"
Start-Sleep -Seconds 6

# ---- 3. the app -----------------------------------------------------------
if (-not $NoApp) {
    Write-Host "برنامه روی http://127.0.0.1:5002 — با Ctrl+C متوقف می‌شود." -ForegroundColor Cyan
    Push-Location $root
    try { python app.py } finally { Pop-Location }
}
