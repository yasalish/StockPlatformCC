#!/usr/bin/env bash
#
# backup.sh — nightly pg_dump of the «Stock» database.
#
# Writes a compressed, timestamped custom-format dump, verifies it can be read
# back, records a checksum, and prunes old ones.
#
# Storage is a local directory (a Docker named volume or a bind mount on the
# VPS). Nothing is uploaded anywhere: the deployment target is inside Iran and
# foreign object storage is both unreachable and out of scope. To keep a copy
# off-box, rsync BACKUP_DIR to another machine you control or to a domestic
# provider — that is a one-line addition at the end of this script, deliberately
# left out rather than guessed at.
#
#   ./backup.sh                 dump, verify, prune
#   ./backup.sh --no-prune      keep everything
#
# Environment (all have sensible defaults):
#   STOCK_DB_NAME / STOCK_DB_USER / STOCK_DB_HOST / STOCK_DB_PORT
#   STOCK_DB_PASSWORD    required
#   BACKUP_DIR           default /backups
#   BACKUP_KEEP_DAYS     delete dumps older than this  (default 14)
#   BACKUP_KEEP_MIN      never go below this many      (default 3)
#   BACKUP_COMPRESS      pg_dump -Z level              (default 6)
set -euo pipefail

DB_NAME="${STOCK_DB_NAME:-Stock}"
DB_USER="${STOCK_DB_USER:-postgres}"
DB_HOST="${STOCK_DB_HOST:-db}"
DB_PORT="${STOCK_DB_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
KEEP_MIN="${BACKUP_KEEP_MIN:-3}"
COMPRESS="${BACKUP_COMPRESS:-6}"
PRUNE=1
[ "${1:-}" = "--no-prune" ] && PRUNE=0

if [ -z "${STOCK_DB_PASSWORD:-}" ]; then
    echo '{"level":"ERROR","service":"backup","msg":"STOCK_DB_PASSWORD is not set"}' >&2
    exit 2
fi
export PGPASSWORD="$STOCK_DB_PASSWORD"

# JSON to stdout, matching observability.py, so `docker compose logs` is one
# searchable stream rather than two formats.
#
# Values are escaped before being embedded. A path is the obvious way this bites:
# a backslash or a double quote anywhere in BACKUP_DIR produces output that is
# not valid JSON, and whatever is shipping these logs then silently drops the
# line — so a failing backup becomes an absence of evidence rather than an alert.
esc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }

log() {
    local level="$1"; shift
    local msg="$1"; shift
    printf '{"ts":"%s","level":"%s","service":"backup","msg":"%s"%s}\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$(esc "$msg")" "${1:-}"
}

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
TARGET="$BACKUP_DIR/${DB_NAME}-${STAMP}.dump"
# Write to a .part file and rename only on success, so a crash mid-dump cannot
# leave a truncated file that looks like a valid backup to the retention pass.
PARTIAL="$TARGET.part"

log INFO "backup starting" ",\"database\":\"$(esc "$DB_NAME")\",\"target\":\"$(esc "$TARGET")\""
START=$(date +%s)

# -Fc  custom format: compressed, and pg_restore can filter and parallelise it.
# -Z   compression level.
# --no-owner / --no-privileges: restoring into a scratch database owned by
#      someone else must not fail on missing roles.
if ! pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -Fc -Z "$COMPRESS" --no-owner --no-privileges -f "$PARTIAL"; then
    rm -f "$PARTIAL"
    log ERROR "pg_dump failed"
    exit 1
fi

# A dump that cannot be listed cannot be restored. Catching that now, while
# there is still a good backup on disk, is the entire point of doing it here.
if ! pg_restore --list "$PARTIAL" > /dev/null 2>&1; then
    rm -f "$PARTIAL"
    log ERROR "dump is unreadable — discarded"
    exit 1
fi

mv "$PARTIAL" "$TARGET"
SIZE=$(wc -c < "$TARGET" | tr -d ' ')
ELAPSED=$(( $(date +%s) - START ))

if command -v sha256sum > /dev/null 2>&1; then
    (cd "$BACKUP_DIR" && sha256sum "$(basename "$TARGET")" > "$(basename "$TARGET").sha256")
fi

log INFO "backup complete" \
    ",\"file\":\"$(esc "$(basename "$TARGET")")\",\"bytes\":$SIZE,\"seconds\":$ELAPSED"

# --- retention -------------------------------------------------------------
# Age alone is not a safe rule: a database that has been failing to dump for a
# month would have its last good backup deleted on the day it is finally needed.
# KEEP_MIN is the floor — the newest N survive whatever their age.
if [ "$PRUNE" = "1" ]; then
    mapfile -t ALL < <(ls -1t "$BACKUP_DIR"/"${DB_NAME}"-*.dump 2>/dev/null || true)
    TOTAL=${#ALL[@]}
    DELETED=0
    if [ "$TOTAL" -gt "$KEEP_MIN" ]; then
        for f in "${ALL[@]:$KEEP_MIN}"; do
            if [ -n "$(find "$f" -mtime "+$KEEP_DAYS" -print 2>/dev/null)" ]; then
                rm -f "$f" "$f.sha256"
                DELETED=$((DELETED + 1))
            fi
        done
    fi
    log INFO "retention applied" \
        ",\"kept\":$((TOTAL - DELETED)),\"deleted\":$DELETED,\"keep_days\":$KEEP_DAYS,\"keep_min\":$KEEP_MIN"
fi
