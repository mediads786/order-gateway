"""Register deployment keys after migrations; read secrets from stdin, never argv."""
import json
import logging
import sys

logging.disable(logging.CRITICAL)

def main():
    from sqlalchemy import select
    from app.db.models import ApiKey
    from app.db.session import SessionLocal
    from app.governance.keys import hash_api_key

    values = json.load(sys.stdin)
    with SessionLocal.begin() as db:
        for role in ("operator", "approver", "admin"):
            plain = values[f"DEPLOY_{role.upper()}_API_KEY"]
            if not plain.startswith("gw_") or len(plain) < 40:
                raise ValueError("Invalid deployment key")
            name = f"ec2-{role}"
            row = db.scalar(select(ApiKey).where(ApiKey.name == name))
            digest = hash_api_key(plain)
            if row is None:
                db.add(ApiKey(name=name, role=role, key_hash=digest, active=True))
            elif row.key_hash != digest or row.role != role or not row.active:
                raise ValueError("Existing deployment key differs or is inactive; resolve deliberately")
    print("PASS: deployment API key hashes registered")

if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("API key registration failed; check database and key consistency (credentials hidden).", file=sys.stderr)
        raise SystemExit(1)
