import argparse
from datetime import timezone
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import ApiKey
from app.db.session import SessionLocal
from app.governance.keys import hash_api_key

ROLES = ("operator", "approver", "admin")


def _create(name: str, role: str) -> int:
    if not 1 <= len(name) <= 60:
        print("name must contain 1 to 60 characters", file=sys.stderr)
        return 2
    plain_key = "gw_" + secrets.token_urlsafe(32)
    try:
        with SessionLocal.begin() as db:
            db.add(ApiKey(name=name, role=role, key_hash=hash_api_key(plain_key), active=True))
            db.flush()
    except IntegrityError:
        print(f"API key name already exists: {name}", file=sys.stderr)
        return 1
    print("Copy this API key now; it cannot be shown again:")
    print(plain_key)
    return 0


def _list() -> int:
    with SessionLocal() as db:
        rows = db.scalars(select(ApiKey).order_by(ApiKey.created_at, ApiKey.name)).all()
    print("key_id\tname\trole\tactive\tcreated")
    for row in rows:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        print(f"{row.key_id}\t{row.name}\t{row.role}\t{row.active}\t{created.astimezone(timezone.utc).isoformat()}")
    return 0


def _deactivate(name: str) -> int:
    with SessionLocal.begin() as db:
        row = db.scalar(select(ApiKey).where(ApiKey.name == name).with_for_update())
        if row is None:
            print(f"API key not found: {name}", file=sys.stderr)
            return 1
        row.active = False
    print(f"Deactivated API key: {name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create, list, or deactivate order-gateway API keys")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--name", required=True)
    create.add_argument("--role", required=True, choices=ROLES)
    commands.add_parser("list")
    deactivate = commands.add_parser("deactivate")
    deactivate.add_argument("--name", required=True)
    arguments = parser.parse_args(argv)
    if arguments.command == "create":
        return _create(arguments.name, arguments.role)
    if arguments.command == "list":
        return _list()
    return _deactivate(arguments.name)


if __name__ == "__main__":
    raise SystemExit(main())
