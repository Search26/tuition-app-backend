#!/usr/bin/env bash
# Nightly backup (deploy/cron/tuition-backup runs it at 03:00):
#   1. pg_dump -Fc of the database into $BACKUP_DIR/db/, keeping the newest $BACKUP_KEEP dumps;
#   2. rclone sync of the MinIO bucket into $BACKUP_DIR/minio/<bucket>/;
#   3. if $BACKUP_REMOTE is set, copy both off the VPS with rclone.
# Prints one JSON line at the end, and exits non-zero on any failure.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env.prod}
[[ $ENV_FILE == /* ]] || ENV_FILE=./$ENV_FILE
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a
dc() { docker compose --env-file "$ENV_FILE" -f compose.prod.yml "$@"; }

dir=${BACKUP_DIR:-/var/backups/tuition}
keep=${BACKUP_KEEP:-14}
started=$(date +%s)
stamp=$(date -u +%Y-%m-%dT%H%M%SZ)
dump="$dir/db/tuition-$stamp.dump"

report() {
  local status=$1
  printf '{"ts":"%s","event":"backup","status":"%s","dump":"%s","db_bytes":%s,"bucket_bytes":%s,"offsite":%s,"seconds":%s}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$status" "$dump" \
    "$(stat -c %s "$dump" 2>/dev/null || echo 0)" \
    "$(du -sb "$dir/minio" 2>/dev/null | cut -f1 || echo 0)" \
    "$([ -n "${BACKUP_REMOTE:-}" ] && echo true || echo false)" \
    "$(( $(date +%s) - started ))"
}
# EXIT, not ERR: set -u and pipeline failures exit without firing ERR.
finish() { local rc=$?; if [ "$rc" -eq 0 ]; then report ok; else report failed; fi; }
trap finish EXIT

mkdir -p "$dir/db" "$dir/minio"
[ -e "${RCLONE_CONFIG_FILE:-./deploy/rclone.conf}" ] || : > "${RCLONE_CONFIG_FILE:-./deploy/rclone.conf}"

# 1. Database. Written to .part first, so a failed dump never looks like a good one.
dc exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$dump.part"
mv "$dump.part" "$dump"
# shellcheck disable=SC2012  # our own file names, no spaces
ls -1t "$dir"/db/tuition-*.dump | tail -n +"$((keep + 1))" | xargs -r rm --

# 2. Bucket: source videos, HLS output, thumbnails, transcode logs.
dc run --rm -T rclone sync "minio:$MINIO_BUCKET" "/backup/minio/$MINIO_BUCKET" --fast-list

# 3. Off the VPS.
if [ -n "${BACKUP_REMOTE:-}" ]; then
  dc run --rm -T rclone copy /backup/db "$BACKUP_REMOTE/db"
  dc run --rm -T rclone sync "/backup/minio/$MINIO_BUCKET" "$BACKUP_REMOTE/minio/$MINIO_BUCKET"
fi

