import hashlib

from app.db.models import ApiKey
from app.db.session import SessionLocal


def hash_api_key(plain_key: str) -> str:
    return hashlib.sha256(plain_key.encode("utf-8")).hexdigest()


def authenticate_api_key(plain_key: str | None) -> ApiKey | None:
    if not plain_key:
        return None
    key_hash = hash_api_key(plain_key)
    with SessionLocal() as db:
        row = db.query(ApiKey).filter(ApiKey.key_hash == key_hash, ApiKey.active.is_(True)).one_or_none()
        if row is None:
            return None
        db.expunge(row)
        return row
