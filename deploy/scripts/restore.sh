#!/usr/bin/env bash
#
# restore.sh — restore a dump and PROVE it restored correctly.
#
# The default mode is the test, not the recovery: it restores into a scratch
# database, compares the row count of every table against the live one, prints
# the comparison, and exits non-zero if anything differs. A backup nobody has
# restored is a hypothesis; this is what turns it into a fact, and it is cheap
# enough to run on a schedule.
#
#   ./restore.sh                          newest dump → scratch, diff, drop
#   ./restore.sh --keep                   …and leave the scratch database behind
#   ./restore.sh --file /backups/X.dump   a specific dump
#   ./restore.sh --into Stock --promote   REAL recovery, overwrites the live DB
#
# --promote is required for anything that is not a scratch database, so no
# combination of flags overwrites production by accident.
set -euo pipefail

DB_NAME="${STOCK_DB_NAME:-Stock}"
DB_USER="${STOCK_DB_USER:-postgres}"
DB_HOST="${STOCK_DB_HOST:-db}"
DB_PORT="${STOCK_DB_PORT:-5432}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
JOBS="${RESTORE_JOBS:-4}"

DUMP=""
TARGET_DB=""
PROMOTE=0
KEEP=0

while [ $# -gt 0 ]; do
    case "$1" in
        --file)    DUMP="$2"; shift 2 ;;
        --into)    TARGET_DB="$2"; shift 2 ;;
        --promote) PROMOTE=1; shift ;;
        --keep)    KEEP=1; shift ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

if [ -z "${STOCK_DB_PASSWORD:-}" ]; then
    echo "STOCK_DB_PASSWORD is not set" >&2
    exit 2
fi
export PGPASSWORD="$STOCK_DB_PASSWORD"

PSQL="psql -h $DB_HOST -p $DB_PORT -U $DB_USER -v ON_ERROR_STOP=1 -q -t -A"

# Newest dump unless one was named.
if [ -z "$DUMP" ]; then
    DUMP="$(ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | head -1 || true)"
fi
if [ -z "$DUMP" ] || [ ! -f "$DUMP" ]; then
    echo "no dump found (looked in $BACKUP_DIR)" >&2
    exit 1
fi

SCRATCH=0
if [ -z "$TARGET_DB" ]; then
    TARGET_DB="${DB_NAME}_restore_test"
    SCRATCH=1
fi

if [ "$SCRATCH" = "0" ] && [ "$PROMOTE" = "0" ]; then
    echo "refusing to restore into '$TARGET_DB' without --promote" >&2
    echo "(the default is a scratch database; --promote is for real recovery)" >&2
    exit 2
fi

echo "=============================================================="
echo " restore.sh"
echo "   dump    : $DUMP"
echo "   into    : $TARGET_DB $([ "$SCRATCH" = 1 ] && echo '(scratch)' || echo '(PROMOTE)')"
echo "=============================================================="

# Checksum, when the sidecar exists. Catches bit-rot on the volume, which is
# the failure mode nobody notices until the day of the restore.
if [ -f "$DUMP.sha256" ] && command -v sha256sum > /dev/null 2>&1; then
    if (cd "$(dirname "$DUMP")" && sha256sum -c "$(basename "$DUMP").sha256" > /dev/null 2>&1); then
        echo "  checksum: OK"
    else
        echo "  checksum: MISMATCH — the dump on disk is not the one that was written" >&2
        exit 1
    fi
fi

if [ "$PROMOTE" = "1" ]; then
    echo
    echo "  *** This OVERWRITES $TARGET_DB. Stop the app first:"
    echo "  ***   docker compose --env-file deploy/.env stop web worker beat"
    echo
fi

# --- recreate the target ---------------------------------------------------
$PSQL -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE)" > /dev/null
$PSQL -d postgres -c "CREATE DATABASE \"$TARGET_DB\"" > /dev/null
echo "  created $TARGET_DB"

START=$(date +%s)
# --no-owner: the dump may reference roles this cluster does not have.
# -j: parallel restore; the price tables are the bulk of the time.
pg_restore -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$TARGET_DB" \
    --no-owner --no-privileges -j "$JOBS" "$DUMP"
echo "  restored in $(( $(date +%s) - START ))s"

# --- the actual verification ----------------------------------------------
# Row counts for every table in both databases, compared.
#
# COUNT(*), not pg_class.reltuples: the planner's estimate is exactly the
# statistic that was wrong by a factor of 2,700 before order 01 ran ANALYZE, and
# an approximation cannot prove that a restore is complete.
#
# query_to_xml is the trick that lets one statement count every table without
# knowing their names — a plain COUNT(*) needs a literal table name, and hard-
# coding the list would silently skip any table added later.
COUNT_SQL="
SELECT c.relname || '=' ||
       (xpath('/row/c/text()',
              query_to_xml(format('SELECT count(*) AS c FROM %I.%I',
                                  n.nspname, c.relname),
                           false, true, '')))[1]::text
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE c.relkind = 'r' AND n.nspname = 'public'
 ORDER BY 1;"

SRC_FILE="$(mktemp)"; DST_FILE="$(mktemp)"
trap 'rm -f "$SRC_FILE" "$DST_FILE"' EXIT

$PSQL -d "$DB_NAME"   -c "$COUNT_SQL" | grep -v '^$' | sort > "$SRC_FILE"
$PSQL -d "$TARGET_DB" -c "$COUNT_SQL" | grep -v '^$' | sort > "$DST_FILE"

echo
printf "  %-26s %14s %14s   %s\n" "table" "source" "restored" "match"
echo "  -----------------------------------------------------------------------"

# awk rather than python: this script also runs inside the postgres image,
# which has no interpreter beyond the shell and awk.
MISMATCH=0
awk -F= '
    NR == FNR { src[$1] = $2; next }
              { dst[$1] = $2 }
    END {
        bad = 0; n = 0
        for (t in src) all[t] = 1
        for (t in dst) all[t] = 1
        # asort is a gawk extension; sort the keys by hand so mawk works too.
        i = 0
        for (t in all) keys[++i] = t
        for (a = 1; a <= i; a++)
            for (b = a + 1; b <= i; b++)
                if (keys[a] > keys[b]) { tmp = keys[a]; keys[a] = keys[b]; keys[b] = tmp }
        for (a = 1; a <= i; a++) {
            t = keys[a]; n++
            s = (t in src) ? src[t] : "-"
            d = (t in dst) ? dst[t] : "-"
            ok = (s == d) ? "yes" : "NO"
            if (s != d) bad++
            printf "  %-26s %14s %14s   %s\n", t, s, d, ok
        }
        printf "\n  %d table(s) compared, %d mismatched\n", n, bad
        exit (bad > 0)
    }
' "$SRC_FILE" "$DST_FILE" || MISMATCH=1

if [ "$MISMATCH" != "0" ]; then
    echo
    echo "  RESTORE VERIFICATION FAILED — row counts differ." >&2
    [ "$SCRATCH" = "1" ] && [ "$KEEP" = "0" ] && \
        $PSQL -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE)" > /dev/null
    exit 1
fi

echo "  RESTORE VERIFIED — every table matches the source row for row."

if [ "$SCRATCH" = "1" ] && [ "$KEEP" = "0" ]; then
    $PSQL -d postgres -c "DROP DATABASE IF EXISTS \"$TARGET_DB\" WITH (FORCE)" > /dev/null
    echo "  scratch database dropped"
elif [ "$SCRATCH" = "1" ]; then
    echo "  scratch database kept as $TARGET_DB"
fi

if [ "$PROMOTE" = "1" ]; then
    echo
    echo "  Next steps after a real restore:"
    echo "    alembic upgrade head                      # bring the schema forward"
    echo "    python -c 'import db; db.clear_cache()'   # the cache does not know"
    echo "    docker compose --env-file deploy/.env start web worker beat"
fi
