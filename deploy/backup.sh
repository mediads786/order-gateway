#!/usr/bin/env bash
set +x
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_env
if [[ ${DEPLOY_LOCK_HELD:-0} != 1 ]]; then
    exec 9>"$ROOT/deploy/.maintenance.lock"
    flock -n 9 || { echo 'Another update or backup is running.' >&2; exit 1; }
fi
install -d -m 700 "$ROOT/deploy/backups"
stamp=$(date -u +%Y%m%dT%H%M%S%NZ)
output="$ROOT/deploy/backups/order-gateway-$stamp.sql.gz"
partial="$output.partial"
trap 'rm -f -- "$partial"' EXIT
compose exec -T db pg_dump -U order_gateway -d order_gateway --no-owner --no-privileges | gzip -c >"$partial"
gzip -t "$partial"
chmod 600 "$partial"
mv -- "$partial" "$output"
# Do not prune local backups unless any requested remote upload succeeds.
if [[ -n ${S3_BACKUP_BUCKET:-} ]]; then
    command -v aws >/dev/null || { echo 'S3 requested but AWS CLI is missing; local backup retained.' >&2; exit 1; }
    aws s3 cp "$output" "s3://$S3_BACKUP_BUCKET/order-gateway/$(basename -- "$output")" \
        --only-show-errors --sse AES256
fi
mapfile -t backups < <(find "$ROOT/deploy/backups" -maxdepth 1 -type f -name 'order-gateway-*.sql.gz' -printf '%f\n' | sort -r)
for ((i=7; i<${#backups[@]}; i++)); do
    rm -- "$ROOT/deploy/backups/${backups[i]}"
done
echo 'PASS: compressed PostgreSQL backup complete; at most seven completed local dumps retained.'
