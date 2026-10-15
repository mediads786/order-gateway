# Restore PostgreSQL on the same server

These steps replace **all gateway data**, including API key hashes, orders, approvals,
and audit records. Schedule downtime. Do not run while an update or backup is running.
Use a trusted dump from this kit and the application revision that created it.
PostgreSQL 16 is required. Do not use `docker compose down -v`.

1. SSH as `ubuntu`. Enter a root shell for the protected installation:

   ```bash
   sudo -i
   cd /opt/order-gateway
   set +x
   source deploy/common.sh
   load_env
   systemctl stop order-gateway-backup.timer
   exec 9>deploy/.maintenance.lock
   flock -n 9 || exit 1
   ```

2. Copy the desired `.sql.gz` from your secure off-server backup into
   `/opt/order-gateway/deploy/backups/`. Replace `BACKUP_FILENAME.sql.gz` below
   with its basename. Do not choose a `.partial` file.

   ```bash
   dump="$ROOT/deploy/backups/BACKUP_FILENAME.sql.gz"
   test -f "$dump" && gzip -t "$dump"
   ```

   Stop if either command fails. A successful gzip check detects compression
   corruption; it does not establish that a dump is trustworthy or complete.

3. Take a safety backup of the current database, then stop incoming requests and
   all application database clients:

   ```bash
   # Pin the chosen dump outside the retention glob before making the safety dump.
   install -m 600 "$dump" "$ROOT/deploy/backups/.restore-source.sql.gz"
   dump="$ROOT/deploy/backups/.restore-source.sql.gz"
   DEPLOY_LOCK_HELD=1 bash deploy/backup.sh
   compose stop caddy worker api
   compose up -d --wait db
   compose exec -T db dropdb -U order_gateway --if-exists --force order_gateway
   compose exec -T db createdb -U order_gateway -O order_gateway order_gateway
   set -o pipefail
   gzip -dc "$dump" | compose exec -T db psql -X -v ON_ERROR_STOP=1 -U order_gateway -d order_gateway
   ```

   **If restore fails, leave api, worker, and Caddy stopped.** Investigate or
   repeat with the safety dump. A partial restore must not serve traffic.

4. Use the application revision recorded with the backup, then start the API
   (its entrypoint applies migrations) before the worker and proxy. Keep the
   matching protected `.env`; the dump contains hashes, not recoverable API keys.

   ```bash
   compose up -d --build --wait --wait-timeout 180 db api mock_erp worker caddy
   bash deploy/smoke_test.sh
   rm -- "$ROOT/deploy/backups/.restore-source.sql.gz"
   flock -u 9
   systemctl start order-gateway-backup.timer
   exit
   ```

   The smoke test creates one synthetic order. If the backed-up API key hashes
   do not match `.env`, restore the matching `.env` from the password manager;
   do not overwrite existing key identities automatically.

## Recover onto a replacement instance

Keep the gateway unavailable to Shopify until restore is complete. Launch the same
Ubuntu release and architecture, copy the reviewed kit and checkout, and restore
the original `.env` **before** running bootstrap. Set `.env` owner to root and mode
600. Keep `COMPOSE_PROJECT_NAME=order-gateway`. Use the original database password,
signing secret, and API keys. If the hostname changes, deliberately change only
`DOMAIN`. Bootstrap preserves this file and idempotently registers the same keys.
Then follow the steps above to replace the newly initialized database with your dump.
Update DNS/Elastic IP association before the final public HTTPS smoke test.

Backups cover PostgreSQL only. The mock ERP is in-memory and resets whenever its
container restarts: past mock sales orders, stock, and cancellation records cannot
be restored. Gateway records retain their statuses but their mock ERP references
may no longer resolve. This is a demonstration deployment, not a durable ERP.
Caddy certificates can be reissued on a replacement server (subject to CA limits);
preserve its `caddy_data` volume on the existing server. Keep a secure separate
copy of `.env`, this kit, and the Git revision with each exported database backup.
