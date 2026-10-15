#!/usr/bin/env bash
set +x
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo 'Run with sudo bash deploy/ec2-bootstrap.sh HOSTNAME [INSTALL_DIRECTORY]' >&2; exit 1; }
DOMAIN_ARG=${1:?Usage: sudo bash ec2-bootstrap.sh HOSTNAME [INSTALL_DIRECTORY]}
INSTALL_DIR=${2:-/opt/order-gateway}
[[ $DOMAIN_ARG =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && ${#DOMAIN_ARG} -le 253 ]] || { echo 'Use a DNS hostname only.' >&2; exit 1; }
[[ $INSTALL_DIR =~ ^/[a-zA-Z0-9_/-]+$ && $INSTALL_DIR != / ]] || { echo 'Use an absolute installation path without spaces.' >&2; exit 1; }
# shellcheck source=/dev/null
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Ubuntu 24.04 LTS required.' >&2; exit 1; }
(( $(awk '/MemTotal/ {print $2}' /proc/meminfo) >= 1800000 )) || { echo 'Select an instance with at least 2 GB RAM.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssl ufw python3 python3-bcrypt gzip util-linux
# Docker Engine and Compose plugin from Docker's official apt repository.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
if [[ ! -e /swapfile ]]; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
fi
[[ -f /swapfile && ! -L /swapfile && $(stat -c %s /swapfile) == 2147483648 ]] || { echo 'Existing /swapfile is not a regular 2 GiB file; inspect manually.' >&2; exit 1; }
chmod 600 /swapfile
swapon --show=NAME --noheadings | grep -Fxq /swapfile || swapon /swapfile
grep -Eq '^/swapfile[[:space:]]' /etc/fstab || printf '%s\n' '/swapfile none swap sw 0 0' >>/etc/fstab
# Never reset an existing firewall silently: reject unexpected existing allow rules.
# Security group MUST separately restrict port 22 to your current public IP.
if ufw show added | grep '^ufw ' | grep -Ev '^ufw allow (22(/tcp)?|80(/tcp)?|443(/tcp)?)$' >/dev/null; then
    echo 'Unexpected existing UFW rules; remove/review them before rerunning.' >&2
    exit 1
fi
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
if [[ ! -d $INSTALL_DIR/.git ]]; then
    [[ ! -e $INSTALL_DIR ]] || { echo 'Install directory already exists without .git.' >&2; exit 1; }
    git clone https://github.com/mediads786/order-gateway.git "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
[[ -f deploy/docker-compose.prod.yml ]] || { echo 'Deployment kit absent: publish the reviewed commit or copy the kit here first.' >&2; exit 1; }
chmod 700 "$INSTALL_DIR"
if [[ ! -e .env ]]; then
    # Exclusive creation prevents overwrites. Values are saved only on the target server.
    python3 - "$DOMAIN_ARG" <<'PY'
import bcrypt, os, secrets, sys
v = dict(DOMAIN=sys.argv[1], COMPOSE_PROJECT_NAME='order-gateway', ERP_ADAPTER='mock',
         ERP_BASE_URL='http://mock_erp:9001', LEGACY_AUTH='key', PROPOSER='rule',
         CADDY_BASIC_AUTH_USER='gateway', S3_BACKUP_BUCKET='')
for name in ('POSTGRES_PASSWORD','ADMIN_TOKEN','SHOPIFY_WEBHOOK_SECRET',
             'SHIPMENT_WEBHOOK_SECRET','CADDY_BASIC_AUTH_PASSWORD'):
    v[name] = secrets.token_hex(32)
v['CADDY_BASIC_AUTH_HASH'] = bcrypt.hashpw(v['CADDY_BASIC_AUTH_PASSWORD'].encode(), bcrypt.gensalt(rounds=14)).decode()
for role in ('OPERATOR','APPROVER','ADMIN'):
    v[f'DEPLOY_{role}_API_KEY'] = 'gw_' + secrets.token_urlsafe(32)
fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as f:
    for key,value in v.items():
        f.write(f"{key}='{value}'\n")
print('SAVE THESE NOW in a password manager. Bootstrap will never print them again:')
for key,value in v.items():
    if any(word in key for word in ('PASSWORD','TOKEN','SECRET','API_KEY')) and key != 'CADDY_BASIC_AUTH_HASH':
        print(f'{key}={value}')
print('CADDY_BASIC_AUTH_USER=gateway')
PY
else
    [[ -f .env && ! -L .env ]] || { echo 'Refusing nonregular .env.' >&2; exit 1; }
    chmod 600 .env
    echo 'Existing .env retained; no secrets regenerated or displayed.'
fi
source deploy/common.sh
load_env
[[ $DOMAIN == "$DOMAIN_ARG" ]] || { echo 'DOMAIN differs from existing .env; edit deliberately before rerunning.' >&2; exit 1; }
check_ports
compose build api worker mock_erp
wait_health
# Pass values via stdin; the helper prints only success/failure, never secrets.
python3 -c 'import json,os; print(json.dumps({n:os.environ[n] for n in ("DEPLOY_OPERATOR_API_KEY","DEPLOY_APPROVER_API_KEY","DEPLOY_ADMIN_API_KEY")}))' |
    compose exec -T api python -c "$(cat deploy/init-api-keys.py)"
bash deploy/install-backup-timer.sh
echo "Gateway health: https://$DOMAIN/health"
echo "Admin (basic auth, then ADMIN_TOKEN): https://$DOMAIN/admin"
echo "Shopify orders/create: https://$DOMAIN/webhooks/shopify/orders-create"
echo 'Run sudo bash deploy/smoke_test.sh next. Certificate issuance can take several minutes.'
