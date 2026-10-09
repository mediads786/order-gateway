import hashlib
import json
import logging
import os
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.governance.audit import write_event
from app.governance.keys import authenticate_api_key
from app.governance.registry import WORKFLOWS, can_request
from app.services.orders import HoldPolicy, submit_order

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


async def _record(
    api_key, request_id: uuid.UUID, workflow: str, input_hash: str,
    event_type: str, status: int | None = None, order_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    await run_in_threadpool(
        _record_for_actor, api_key, request_id, workflow, input_hash,
        event_type, status, order_id, detail,
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
            "approval": workflow.approval,
            "decision_roles": list(workflow.decision_roles),
            "input_schema": workflow.input_model.model_json_schema(),
        }
        for workflow in WORKFLOWS.values()
    ]


@router.post("/workflows/{name}/requests")
async def request_workflow(request: Request, name: str):
    raw_body = await request.body()
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated workflow request")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    request_id = uuid.uuid4()
    input_hash = _input_hash(raw_body)
    workflow = WORKFLOWS.get(name)
    if workflow is None:
        await _record(api_key, request_id, name, input_hash, "workflow.denied", 404,
                      detail={"reason": "unknown_workflow"})
        return _response(404, {"error": "unknown_workflow", "request_id": str(request_id)}, request_id)
    if not can_request(api_key.role, name):
        await _record(api_key, request_id, name, input_hash, "workflow.denied", 403,
                      detail={"reason": "forbidden"})
        return _response(403, {"error": "forbidden", "request_id": str(request_id)}, request_id)

    try:
        envelope = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        input_data = None
    else:
        input_data = envelope.get("input") if isinstance(envelope, dict) else None
    if not isinstance(input_data, dict):
        input_data = None
    status, body = await run_governed_request(
        api_key, name, input_data, request.headers.get("Idempotency-Key"), request_id, input_hash,
    )
    return _response(status, body, request_id)


async def run_governed_request(
    api_key, name: str, input_data: dict | None, idempotency_key: str | None,
    request_id: uuid.UUID, raw_input_hash: str,
) -> tuple[int, dict]:
    input_hash = raw_input_hash
    workflow = WORKFLOWS[name]
    await _record(api_key, request_id, name, input_hash, "workflow.requested")
    if not isinstance(input_data, dict):
        body = {"error": "invalid_request", "request_id": str(request_id)}
        await _record(api_key, request_id, name, input_hash, "workflow.rejected", 422, detail=body)
        return 422, body
    if name == "cancel_order":
        try:
            validated_cancel = workflow.input_model.model_validate(input_data)
        except ValidationError as exc:
            return await _reject_request_body(
                api_key, request_id, name, input_hash, 422,
                {"error": "invalid_request", "request_id": str(request_id), **_validation_detail(exc)},
            )
        from app.governance.approvals import check_cancellation_eligibility, create_cancellation_approval
        order_id = validated_cancel.order_id
        status, error, _ = await run_in_threadpool(check_cancellation_eligibility, order_id)
        if error:
            body = {"error": error, "request_id": str(request_id)}
            await _record(api_key, request_id, name, input_hash, "workflow.rejected", status,
                          order_id=order_id if error != "order_not_found" else None, detail={"error": error})
            return status, body
        duplicate_cancel = False
        try:
            approval_id = await run_in_threadpool(
                create_cancellation_approval, request_id, order_id, validated_cancel.reason,
                api_key.key_id, api_key.name,
            )
        except IntegrityError as exc:
            diag = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if diag != "uq_approvals_open_cancel" and "uq_approvals_open_cancel" not in str(exc.orig):
                raise
            duplicate_cancel = True
        except ValueError as exc:
            if str(exc) != "cancel_already_pending":
                raise
            duplicate_cancel = True
        if duplicate_cancel:
            body = {"error": "cancel_already_pending", "request_id": str(request_id)}
            await _record(api_key, request_id, name, input_hash, "workflow.rejected", 409,
                          order_id=order_id, detail={"error": "cancel_already_pending"})
            return 409, body
        await _record(api_key, request_id, name, input_hash, "workflow.approval_requested", 202,
                      order_id=order_id, detail={"approval_id": str(approval_id)})
        return 202, {"request_id": str(request_id), "workflow": name,
                     "approval_id": str(approval_id), "status": "PENDING_APPROVAL"}
    if not workflow.executable:
        try:
            workflow.input_model.model_validate(input_data)
        except ValidationError as exc:
            return await _reject_request_body(
                api_key, request_id, name, input_hash, 422,
                {"error": "invalid_request", "request_id": str(request_id), **_validation_detail(exc)},
            )
        await _record(api_key, request_id, name, input_hash, "workflow.not_executable", 501,
                      detail={"reason": "not_executable"})
        return 501, {"error": "workflow_not_executable", "request_id": str(request_id)}

    try:
        canonical_body = json.dumps(
            input_data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return await _reject_request_body(api_key, request_id, name, input_hash, 422, {"error": "invalid_request", "request_id": str(request_id)})

    hold = None
    if name == "create_order":
        threshold_text = os.getenv("APPROVAL_THRESHOLD", "1000.00").strip() or "1000.00"
        try:
            threshold = Decimal(threshold_text)
            if not threshold.is_finite() or threshold < 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            logger.error("Invalid APPROVAL_THRESHOLD request_id=%s", request_id)
            await _record(api_key, request_id, name, input_hash, "workflow.failed", 503,
                          detail={"error": "approval_threshold_invalid"})
            return 503, {"error": "approval_threshold_invalid", "request_id": str(request_id)}
        hold = HoldPolicy(threshold, request_id, api_key.key_id, api_key.name)

    if name == "adjust_stock":
        from app.adapters import get_adapter
        from app.adapters.factory import AdapterConfigurationError

        try:
            validated = workflow.input_model.model_validate(input_data)
        except ValidationError as exc:
            return await _reject_request_body(
                api_key, request_id, name, input_hash, 422,
                {"error": "invalid_request", "request_id": str(request_id), **_validation_detail(exc)},
            )
        try:
            active_adapter = get_adapter()
        except AdapterConfigurationError:
            logger.error("Stock adjustment adapter is not configured request_id=%s", request_id)
            await _record(api_key, request_id, name, input_hash, "workflow.failed", 503,
                          detail={"error": "adapter_not_configured"})
            return 503, {"error": "adapter_not_configured", "request_id": str(request_id)}
        if not callable(getattr(active_adapter, "adjust_stock", None)):
            await _record(api_key, request_id, name, input_hash, "workflow.not_executable", 501,
                          detail={"reason": "adapter_not_supported"})
            return 501, {"error": "adapter_not_supported", "request_id": str(request_id)}
        from app.governance.approvals import create_adjustment_approval

        try:
            approval_id = await run_in_threadpool(
                create_adjustment_approval, request_id, validated.model_dump(mode="json"), api_key.key_id, api_key.name,
            )
        except Exception as exc:
            logger.exception("Could not create adjustment approval request_id=%s", request_id)
            await _record(api_key, request_id, name, input_hash, "workflow.failed", 500,
                          detail={"error": type(exc).__name__})
            return 500, {"error": "internal_error", "request_id": str(request_id)}
        await _record(api_key, request_id, name, input_hash, "workflow.approval_requested", 202,
                      detail={"approval_id": str(approval_id)})
        body = {"request_id": str(request_id), "workflow": name,
                "approval_id": str(approval_id), "status": "PENDING_APPROVAL"}
        return 202, body

    if not idempotency_key:
        result = {"detail": "Idempotency-Key header is required", "request_id": str(request_id)}
        await _record(api_key, request_id, name, input_hash, "workflow.rejected", 400,
                      detail={"error": "missing_idempotency_key"})
        return 400, result

    try:
        status, result = await run_in_threadpool(submit_order, canonical_body, idempotency_key, hold=hold)
    except Exception as exc:
        logger.exception("Governed workflow failed request_id=%s workflow=%s", request_id, name)
        await _record(api_key, request_id, name, input_hash, "workflow.failed", 500,
                      detail={"error": "internal_error", "exception": type(exc).__name__})
        return 500, {"error": "internal_error", "request_id": str(request_id)}

    order_id = _order_id_from_result(result)
    if status == 202:
        await _record(api_key, request_id, name, input_hash, "workflow.approval_requested", 202,
                      order_id=order_id,
                      detail={"approval_id": result.get("approval_id"), "total": result.get("total"),
                              "threshold": str(hold.threshold) if hold else None})
        body = {"request_id": str(request_id), "workflow": name,
                "approval_id": result.get("approval_id"), "status": "PENDING_APPROVAL", "result": result}
        return 202, body
    if 200 <= status < 300:
        await _record(api_key, request_id, name, input_hash, "workflow.executed", status,
                      order_id=order_id, detail={"status": status})
    elif 400 <= status < 500:
        error_detail = result.get("detail") or result.get("reasons") or "rejected"
        await _record(api_key, request_id, name, input_hash, "workflow.rejected", status,
                      order_id=order_id, detail={"error": error_detail})
    else:
        await _record(api_key, request_id, name, input_hash, "workflow.failed", status,
                      order_id=order_id, detail={"error": "internal_error"})
        return 500, {"error": "internal_error", "request_id": str(request_id)}
    return status, {"request_id": str(request_id), "workflow": name, "result": result}


def _order_id_from_result(result: dict) -> uuid.UUID | None:
    value = result.get("order_id") if isinstance(result, dict) else None
    try:
        return uuid.UUID(value) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


async def _reject_request_body(api_key, request_id: uuid.UUID, name: str, input_hash: str,
                               status: int, body: dict) -> tuple[int, dict]:
    await _record(api_key, request_id, name, input_hash, "workflow.rejected", status, detail=body)
    return status, body
