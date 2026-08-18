#!/usr/bin/env bash
#
# backup-loop.sh — the scheduler for the backup container.
#
# WHY NOT CRON, AND WHY NOT CELERY BEAT
#
# cron is not in the postgres image, and installing it would mean a second
# process supervisor inside a container that should run one thing.
#
# Celery Beat already exists in this stack and would have been the tidy answer,
# except that the Celery worker runs the application image — python:slim, with
# no pg_dump — and the pg_dump that takes the backup MUST be the same major
# version as the server. Adding the PostgreSQL apt repository to the app image
# to get pg_dump 17 is a lot of machinery, and it silently breaks the day the
# database is upgraded and the client is not. Running the backup from the same
# image as the database makes that mismatch impossible.
#
# So: a shell loop that sleeps until the next scheduled time. It is small enough
# to read in one sitting, has no dependencies, and survives a restart because
# the next wake-up is computed from the clock rather than from elapsed time.
set -euo pipefail

BACKUP_AT="${BACKUP_AT:-02:30}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
# Day of week to run the restore verification, 1=Monday … 7=Sunday (date +%u).
# 5 = Friday, which is a non-trading day in Iran, so the extra load lands when
# nothing else is happening.
VERIFY_DAY="${BACKUP_VERIFY_WEEKDAY:-5}"
HEARTBEAT="$BACKUP_DIR/.heartbeat"

log() {
    printf '{"ts":"%s","level":"%s","service":"backup","msg":"%s"}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2"
}

mkdir -p "$BACKUP_DIR"
log INFO "backup scheduler started, daily at $BACKUP_AT (verify on weekday $VERIFY_DAY)"

while true; do
    NOW=$(date +%s)
    # Today's scheduled instant; if it has passed, tomorrow's.
    TARGET=$(date -d "today $BACKUP_AT" +%s 2>/dev/null || echo 0)
    if [ "$TARGET" -le "$NOW" ]; then
        TARGET=$(date -d "tomorrow $BACKUP_AT" +%s)
    fi
    SLEEP=$(( TARGET - NOW ))

    log INFO "next backup in ${SLEEP}s (at $(date -d "@$TARGET" '+%Y-%m-%d %H:%M %Z'))"

    # Wake hourly rather than sleeping the whole interval, so the healthcheck
    # has a recent heartbeat to look at and a stuck scheduler is visible.
    while [ "$SLEEP" -gt 0 ]; do
        touch "$HEARTBEAT"
        CHUNK=$(( SLEEP > 3600 ? 3600 : SLEEP ))
        sleep "$CHUNK"
        SLEEP=$(( SLEEP - CHUNK ))
    done
    touch "$HEARTBEAT"

    if /bin/bash /scripts/backup.sh; then
        # Weekly, prove the newest dump actually restores. Failure is logged
        # loudly but does NOT stop the loop — tomorrow's backup still matters.
        if [ "$(date +%u)" = "$VERIFY_DAY" ]; then
            log INFO "weekly restore verification starting"
            if /bin/bash /scripts/restore.sh; then
                log INFO "weekly restore verification PASSED"
            else
                log ERROR "weekly restore verification FAILED — the backups are not trustworthy"
            fi
        fi
    else
        log ERROR "backup failed"
    fi

    # Guard against a same-second re-entry when the run is instantaneous.
    sleep 60
done
