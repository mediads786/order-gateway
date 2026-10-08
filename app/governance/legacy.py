import hashlib
import logging
import os
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.db.models import ApiKey
from app.governance.audit import write_event
from app.governance.keys import authenticate_api_key

logger = logging.getLogger(__name__)


async def legacy_guard(
    request: Request, workflow_label: str, allowed_roles: tuple[str, ...],
) -> ApiKey | JSONResponse | None:
    configured_mode = os.getenv("LEGACY_AUTH", "off")
    mode = configured_mode.strip().lower()
    if mode == "off":
        return None
    if mode != "key":
        logger.warning("Unknown LEGACY_AUTH value %r; enforcing key mode", configured_mode)

    api_key = await run_in_threadpool(
        authenticate_api_key, request.headers.get("X-API-Key"),
    )
    if api_key is None:
        logger.warning("Rejected unauthenticated legacy request workflow=%s", workflow_label)
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    if api_key.role not in allowed_roles:
        raw_body = await request.body() if workflow_label == "legacy:create_order" else b""
        await run_in_threadpool(
            write_event,
            request_id=uuid.uuid4(),
            event_type="workflow.denied",
            workflow=workflow_label,
            input_hash=hashlib.sha256(raw_body).hexdigest(),
            key_id=api_key.key_id,
            actor_name=api_key.name,
            role=api_key.role,
            http_status=403,
            detail={"reason": "forbidden"},
        )
        return JSONResponse(status_code=403, content={"error": "forbidden"})
    return api_key
