import hashlib
import hmac
import os

from fastapi import Request

COOKIE_NAME = "gw_admin"
SESSION_MESSAGE = b"order-gateway-admin-session-v1"


def configured_token() -> str:
    return os.getenv("ADMIN_TOKEN", "")


def admin_enabled() -> bool:
    return len(configured_token()) >= 16


def session_cookie_value(token: str | None = None) -> str:
    key = (configured_token() if token is None else token).encode()
    return hmac.new(key, SESSION_MESSAGE, hashlib.sha256).hexdigest()


def authenticated(request: Request) -> bool:
    token = configured_token()
    cookie = request.cookies.get(COOKIE_NAME, "")
    return len(token) >= 16 and hmac.compare_digest(
        cookie.encode("utf-8"), session_cookie_value(token).encode("ascii"),
    )
