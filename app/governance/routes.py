import hashlib
import json
import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.governance.audit import write_event
from app.governance.keys import authenticate_api_key
from app.governance.registry import WORKFLOWS, can_request
from app.services.orders import submit_order

logger = logging.getLogger(__name__)
router = APIRouter()


def _response(status: int, body: dict, request_id: uuid.UUID | None = None) -> JSONResponse:
    headers = {"X-Request-Id": str(request_id)} if request_id else None
    return JSONResponse(status_code=status, content=body, headers=headers)


def _input_hash(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _record_for_actor(
    api_key, request_id: uuid.UUID, workflow: str, input_hash: str,
    event_type: str, status: int | None = None, order_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    write_event(
        request_id=request_id,
        event_type=event_type,
        workflow=workflow,
        input_hash=input_hash,
        key_id=api_key.key_id,
        actor_name=api_key.name,
        role=api_key.role,
        http_status=status,
        order_id=order_id,
        detail=detail,
    )


def _validation_detail(exc: ValidationError) -> dict:
    return {
        "reasons": [
            {"field": ".".join(str(part) for part in error["loc"]), "reason": error["msg"]}
            for error in exc.errors()
        ]
    }


@router.get("/workflows")
def list_workflows(request: Request):
    api_key = authenticate_api_key(request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated workflow registry request")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return [
        {
            "name": workflow.name,
            "description": workflow.description,
            "risk": workflow.risk,
            "request_roles": list(workflow.request_roles),
            "executable": workflow.executable,
            "input_schema": workflow.input_model.model_json_schema(),
        }
        for workflow in WORKFLOWS.values()
    ]


@router.post("/workflows/{name}/requests")
async def request_workflow(request: Request, name: str):
    raw_body = await request.body()
    api_key = authenticate_api_key(request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated workflow request")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    request_id = uuid.uuid4()
    input_hash = _input_hash(raw_body)
    workflow = WORKFLOWS.get(name)
    if workflow is None:
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.denied", 404,
                          detail={"reason": "unknown_workflow"})
        return _response(404, {"error": "unknown_workflow", "request_id": str(request_id)}, request_id)
    if not can_request(api_key.role, name):
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.denied", 403,
                          detail={"reason": "forbidden"})
        return _response(403, {"error": "forbidden", "request_id": str(request_id)}, request_id)

    _record_for_actor(api_key, request_id, name, input_hash, "workflow.requested")
    try:
        envelope = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _reject_request(api_key, request_id, name, input_hash, 422, {"error": "invalid_request"})
    if not isinstance(envelope, dict) or not isinstance(envelope.get("input"), dict):
        return _reject_request(api_key, request_id, name, input_hash, 422, {"error": "invalid_request"})

    input_data = envelope["input"]
    if not workflow.executable:
        try:
            workflow.input_model.model_validate(input_data)
        except ValidationError as exc:
            return _reject_request(
                api_key, request_id, name, input_hash, 422,
                {"error": "invalid_request", **_validation_detail(exc)},
            )
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.not_executable", 501,
                          detail={"reason": "not_executable"})
        return _response(501, {"error": "workflow_not_executable", "request_id": str(request_id)}, request_id)

    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        result = {"detail": "Idempotency-Key header is required"}
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.rejected", 400,
                          detail={"error": "missing_idempotency_key"})
        return _response(400, result, request_id)
    try:
        canonical_body = json.dumps(
            input_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return _reject_request(api_key, request_id, name, input_hash, 422, {"error": "invalid_request"})

    try:
        status, result = await run_in_threadpool(submit_order, canonical_body, idempotency_key)
    except Exception as exc:
        logger.exception("Governed workflow failed request_id=%s workflow=%s", request_id, name)
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.failed", 500,
                          detail={"error": "internal_error", "exception": type(exc).__name__})
        return _response(500, {"error": "internal_error", "request_id": str(request_id)}, request_id)

    order_id = _order_id_from_result(result)
    if 200 <= status < 300:
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.executed", status,
                          order_id=order_id, detail={"status": status})
    elif 400 <= status < 500:
        error_detail = result.get("detail") or result.get("reasons") or "rejected"
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.rejected", status,
                          order_id=order_id, detail={"error": error_detail})
    else:
        _record_for_actor(api_key, request_id, name, input_hash, "workflow.failed", status,
                          order_id=order_id, detail={"error": "internal_error"})
        return _response(500, {"error": "internal_error", "request_id": str(request_id)}, request_id)
    return _response(status, {"request_id": str(request_id), "workflow": name, "result": result}, request_id)


def _order_id_from_result(result: dict) -> uuid.UUID | None:
    value = result.get("order_id") if isinstance(result, dict) else None
    try:
        return uuid.UUID(value) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def _reject_request(api_key, request_id: uuid.UUID, name: str, input_hash: str,
                    status: int, body: dict) -> JSONResponse:
    _record_for_actor(api_key, request_id, name, input_hash, "workflow.rejected", status, detail=body)
    return _response(status, body, request_id)
