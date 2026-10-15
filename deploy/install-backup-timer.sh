#!/usr/bin/env bash
set +x
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
[[ $ROOT =~ ^/[a-zA-Z0-9_/-]+$ ]] || { echo 'Installation path must not contain spaces or systemd special characters.' >&2; exit 1; }
cat >/etc/systemd/system/order-gateway-backup.service <<EOF
[Unit]
Description=Order gateway PostgreSQL backup
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
UMask=0077
WorkingDirectory=$ROOT
ExecStart=/bin/bash $ROOT/deploy/backup.sh
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7
EOF
cat >/etc/systemd/system/order-gateway-backup.timer <<'EOF'
[Unit]
Description=Nightly order gateway backup

[Timer]
OnCalendar=*-*-* 02:15:00 UTC
RandomizedDelaySec=300
Persistent=true
Unit=order-gateway-backup.service

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now order-gateway-backup.timer
echo 'Backup timer enabled: nightly at 02:15 UTC plus up to five minutes jitter.'
