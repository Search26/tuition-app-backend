#!/usr/bin/env bash
# Restore a backup into a NEW database, never over production.
# Usage: scripts/restore.sh <dump> [--into <db>] [--bucket-from <backup dir>] [--strict]
#   --into         target database (default tuition_restore); refuses the live one
#   --bucket-from  copy a bucket mirror (e.g. /var/backups/tuition) back into MinIO
#   --strict       exit 1 when a table's row count differs from the live database (CI)
# Switching production over to a restored database is a separate step in docs/deploy.md.
set -euo pipefail
cd "$(dirname "$0")/.."

ENV_FILE=${ENV_FILE:-.env.prod}
[[ $ENV_FILE == /* ]] || ENV_FILE=./$ENV_FILE
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a
dc() { docker compose --env-file "$ENV_FILE" -f compose.prod.yml "$@"; }
psql_at() { dc exec -T postgres psql -U "$POSTGRES_USER" -v ON_ERROR_STOP=1 -At "$@"; }

dump=${1:?usage: scripts/restore.sh <dump> [--into <db>] [--bucket-from <dir>] [--strict]}
shift
into=tuition_restore
bucket_from=
strict=false
while [ $# -gt 0 ]; do
  case $1 in
    --into) into=$2; shift 2 ;;
    --bucket-from) bucket_from=$2; shift 2 ;;
    --strict) strict=true; shift ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done
[ -f "$dump" ] || { echo "no such dump: $dump" >&2; exit 2; }
if [ "$into" = "$POSTGRES_DB" ]; then
  echo "refusing to restore over the live database ($POSTGRES_DB); pick another --into" >&2
  exit 2
fi

echo "Restoring $dump into $into"
dc exec -T postgres dropdb -U "$POSTGRES_USER" --if-exists "$into"
dc exec -T postgres createdb -U "$POSTGRES_USER" "$into"
dc exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$into" --no-owner --exit-on-error < "$dump"
echo "alembic version in $into: $(psql_at -d "$into" -c 'select version_num from alembic_version')"

# Exact row counts per table, restored next to live.
count_sql="select string_agg(format('select %L, count(*) from %I', table_name, table_name), ' union all ' order by table_name)
           from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE'"
counts() { psql_at -d "$1" -F ' ' -c "$(psql_at -d "$1" -c "$count_sql")"; }
mismatch=0
live_counts=$(counts "$POSTGRES_DB")
printf '%-20s %10s %10s\n' table restored live
while read -r table restored; do
  live=$(awk -v t="$table" '$1 == t {print $2}' <<< "$live_counts")
  mark=""
  if [ "$restored" != "${live:-missing}" ]; then mark="  differs"; mismatch=1; fi
  printf '%-20s %10s %10s%s\n' "$table" "$restored" "${live:-missing}" "$mark"
done < <(counts "$into")

if [ -n "$bucket_from" ]; then
  echo "Copying the bucket back from $bucket_from/minio/$MINIO_BUCKET"
  BACKUP_DIR=$bucket_from dc run --rm -T rclone copy "/backup/minio/$MINIO_BUCKET" "minio:$MINIO_BUCKET"
fi

if [ "$strict" = true ] && [ "$mismatch" = 1 ]; then
  echo "row counts differ" >&2
  exit 1
fi
echo "Restore into $into done"
