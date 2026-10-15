"""Shared bootstrap/local credential generation; never print credential values."""
import os
import argparse
import re
import secrets
import sys

import bcrypt


def validate(values):
    user = values['CADDY_BASIC_AUTH_USER']
    password = values['CADDY_BASIC_AUTH_PASSWORD']
    digest = values['CADDY_BASIC_AUTH_HASH']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', user):
        raise ValueError()
    if not password or any(c in password for c in '\r\n\0'):
        raise ValueError()
    if not re.fullmatch(r'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}', digest):
        raise ValueError()
    if not bcrypt.checkpw(password.encode('utf-8'), digest.encode('ascii')):
        raise ValueError()


def generate(domain, project='order-gateway'):
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', domain) or len(domain) > 253:
        raise ValueError()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', project):
        raise ValueError()
    values = dict(DOMAIN=domain, COMPOSE_PROJECT_NAME=project, ERP_ADAPTER='mock',
                  ERP_BASE_URL='http://mock_erp:9001', LEGACY_AUTH='key', PROPOSER='rule',
                  CADDY_BASIC_AUTH_USER='gateway', S3_BACKUP_BUCKET='')
    for name in ('POSTGRES_PASSWORD', 'ADMIN_TOKEN', 'SHOPIFY_WEBHOOK_SECRET',
                 'SHIPMENT_WEBHOOK_SECRET', 'CADDY_BASIC_AUTH_PASSWORD'):
        values[name] = secrets.token_hex(32)
    # Hash exactly these bytes: no shell read, stdin newline, or password trimming.
    values['CADDY_BASIC_AUTH_HASH'] = bcrypt.hashpw(
        values['CADDY_BASIC_AUTH_PASSWORD'].encode('utf-8'), bcrypt.gensalt(rounds=14)).decode('ascii')
    for role in ('OPERATOR', 'APPROVER', 'ADMIN'):
        values[f'DEPLOY_{role}_API_KEY'] = 'gw_' + secrets.token_urlsafe(32)
    validate(values)
    # Single quotes preserve bcrypt dollars in both Bash and Compose .env parsing.
    # LF is explicit even when generation runs on Windows. Never overwrite .env.
    fd = os.open('.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        for name, value in values.items():
            stream.write(f"{name}='{value}'\n")
    print('PASS: credentials generated in protected .env; values hidden')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    generation = commands.add_parser('generate')
    generation.add_argument('domain')
    generation.add_argument('--project', default='order-gateway')
    commands.add_parser('check')
    args = parser.parse_args()
    if args.command == 'generate':
        generate(args.domain, args.project)
    else:
        validate(os.environ)
        print('PASS: Basic Auth username, newline-free password and bcrypt hash agree')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('FAIL: Basic Auth credential generation/validation; values hidden; existing .env is never replaced', file=sys.stderr)
        sys.exit(1)
