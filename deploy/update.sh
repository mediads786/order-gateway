#!/usr/bin/env bash
set +x
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_env
exec 9>"$ROOT/deploy/.maintenance.lock"
flock -n 9 || { echo 'Another update or backup is running.' >&2; exit 1; }
[[ -z $(git status --porcelain --untracked-files=normal) ]] || { echo 'Working tree must be clean before update.' >&2; exit 1; }
# Back up the old schema before migrations. Use the lock already held by this process.
DEPLOY_LOCK_HELD=1 bash deploy/backup.sh
git pull --ff-only
check_ports
compose build api worker mock_erp
# Stop worker while the API entrypoint runs Alembic migrations.
compose stop caddy worker api
compose up -d --force-recreate --wait --wait-timeout 180 db api mock_erp worker caddy
bash deploy/smoke_test.sh
echo 'Update complete.'
