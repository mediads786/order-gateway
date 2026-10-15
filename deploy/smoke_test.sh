#!/usr/bin/env bash
set +x
set -Eeuo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
load_env
python3 "$ROOT/deploy/credentials.py" check
check_ports
# Production verifies public TLS. Local mode is accepted ONLY for DOMAIN=localhost.
case ${1:-} in
    '') export DEPLOY_LOCAL_TLS=0 ;;
    --local-tls) [[ $DOMAIN == localhost ]] || { echo 'Local TLS option requires DOMAIN=localhost.' >&2; exit 1; }; export DEPLOY_LOCAL_TLS=1 ;;
    *) echo 'Usage: bash deploy/smoke_test.sh [--local-tls]' >&2; exit 1 ;;
esac
python3 "$ROOT/deploy/smoke_test.py"
