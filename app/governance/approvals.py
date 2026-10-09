import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.adapters import get_adapter
from app.db.models import Approval, ApiKey, AuditEvent, Job, Order
from app.db.session import SessionLocal
from app.governance.audit import write_event
from app.governance.keys import authenticate_api_key
from app.governance.registry import can_decide
from app.services.orders import create_queued_job
from app.workers.worker import classify_failure

logger = logging.getLogger(__name__)
router = APIRouter()
PAGE_SIZE = 25
APPROVAL_STATES = ("PENDING", "APPROVED", "REJECTED", "EXECUTED", "EXECUTION_FAILED")


class LocalCancellationUpdateError(RuntimeError):
    pass


class DecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=200)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _request_hash(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def _validation_errors(exc: ValidationError) -> list[dict]:
    return [
        {"field": ".".join(str(part) for part in error["loc"]), "reason": error["msg"]}
        for error in exc.errors()
    ]


def _event(
    *, request_id: uuid.UUID, api_key: ApiKey, workflow: str, input_hash: str,
    event_type: str, status: int | None, approval_id: uuid.UUID,
    order_id: uuid.UUID | None = None, detail: dict | None = None,
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
        detail={"approval_id": str(approval_id), **(detail or {})},
    )


def _approval_summary(db, approval: Approval) -> dict:
    if approval.workflow == "create_order" and approval.order_id:
        order = db.get(Order, approval.order_id)
        return {"total": str(order.total), "currency": order.currency} if order else {}
    values = approval.input or {}
    if approval.workflow == "cancel_order":
        return {"order_id": values.get("order_id"), "reason": values.get("reason")}
    return {"sku": values.get("sku"), "qty_delta": values.get("qty_delta")}


def _cancellation_eligibility(db, order_id: uuid.UUID) -> tuple[str | None, Order | None, Job | None]:
    # Match the worker's job-then-order lock order to avoid a claim/cancel deadlock.
    job = db.scalar(select(Job).where(Job.order_id == order_id).with_for_update())
    order = db.scalar(select(Order).where(Order.order_id == order_id).with_for_update())
    if order is None:
        return "order_not_found", None, None
    if order.status in ("CANCELLED", "REJECTED"):
        return "order_not_cancellable", order, job
    if order.status == "PENDING_APPROVAL":
        return "order_pending_approval", order, job
    if order.status in ("APPROVED", "PROCESSING"):
        return "order_in_progress", order, job
    if order.status == "CONFIRMED" and order.erp_order_id:
        return None, order, job
    if (not order.erp_order_id and job is not None and job.attempts == 0
            and job.status == "QUEUED" and order.status in ("RECEIVED", "QUEUED")):
        return None, order, job
    if not order.erp_order_id and job is not None and job.attempts > 0:
        return "erp_state_unknown", order, job
    return "order_not_cancellable", order, job


def check_cancellation_eligibility(order_id: uuid.UUID) -> tuple[int, str | None, str | None]:
    with SessionLocal.begin() as db:
        error, order, job = _cancellation_eligibility(db, order_id)
        mode = "erp" if order and order.erp_order_id else "local"
    if error == "order_not_found":
        return 404, error, None
    if error:
        return 409, error, None
    return 200, None, mode


def create_cancellation_approval(
    request_id: uuid.UUID, order_id: uuid.UUID, reason: str, key_id: uuid.UUID, key_name: str,
) -> uuid.UUID:
    approval_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        duplicate = db.scalar(select(Approval.approval_id).where(
            Approval.workflow == "cancel_order",
            Approval.status.in_(("PENDING", "APPROVED")),
            Approval.input["order_id"].as_string() == str(order_id),
        ))
        if duplicate:
            raise ValueError("cancel_already_pending")
        db.add(Approval(
            approval_id=approval_id, request_id=request_id, workflow="cancel_order", status="PENDING",
            order_id=None, input={"order_id": str(order_id), "reason": reason},
            requested_by_key_id=key_id, requested_by_name=key_name,
        ))
    return approval_id


def _approval_dict(db, approval: Approval) -> dict:
    return {
        "approval_id": str(approval.approval_id),
        "workflow": approval.workflow,
        "status": approval.status,
        "requested_by_name": approval.requested_by_name,
        "created_at": _utc_iso(approval.created_at),
        "decided_by_name": approval.decided_by_name,
        "decided_at": _utc_iso(approval.decided_at),
        "order_id": str(approval.order_id) if approval.order_id else None,
        "summary": _approval_summary(db, approval),
    }


def create_adjustment_approval(
    request_id: uuid.UUID, input_data: dict, key_id: uuid.UUID, key_name: str,
) -> uuid.UUID:
    approval_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(Approval(
            approval_id=approval_id,
            request_id=request_id,
            workflow="adjust_stock",
            status="PENDING",
            input=input_data,
            requested_by_key_id=key_id,
            requested_by_name=key_name,
        ))
    return approval_id


def _approval_snapshot(approval_id: uuid.UUID) -> Approval | None:
    with SessionLocal() as db:
        row = db.get(Approval, approval_id)
        if row is not None:
            db.expunge(row)
        return row


def _visible(approval: Approval, api_key: ApiKey) -> bool:
    return api_key.role in ("approver", "admin") or approval.requested_by_key_id == api_key.key_id


def _list_approvals(api_key: ApiKey, status: str, page: int) -> list[dict]:
    with SessionLocal() as db:
        query = select(Approval)
        if api_key.role == "operator":
            query = query.where(Approval.requested_by_key_id == api_key.key_id)
        if status in APPROVAL_STATES:
            query = query.where(Approval.status == status)
        rows = db.scalars(
            query.order_by(Approval.created_at.desc(), Approval.approval_id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
        return [_approval_dict(db, row) for row in rows]


@router.get("/approvals")
async def list_approvals(request: Request, status: str = "", page: int = 1):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated approvals list request")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await run_in_threadpool(_list_approvals, api_key, status, max(1, page))


@router.get("/approvals/{approval_id}")
async def get_approval(request: Request, approval_id: str):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated approval detail request")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    try:
        parsed_id = uuid.UUID(approval_id)
    except (ValueError, AttributeError):
        logger.info("Unknown approval_id=%s", approval_id)
        return JSONResponse(status_code=404, content={"error": "not_found"})
    approval = await run_in_threadpool(_approval_snapshot, parsed_id)
    if approval is None or not _visible(approval, api_key):
        logger.info("Approval unavailable approval_id=%s actor=%s", parsed_id, api_key.name)
        return JSONResponse(status_code=404, content={"error": "not_found"})
    result = await run_in_threadpool(_approval_dict_for_id, parsed_id)
    return result


def _approval_dict_for_id(approval_id: uuid.UUID) -> dict | None:
    with SessionLocal() as db:
        approval = db.get(Approval, approval_id)
        return _approval_dict(db, approval) if approval else None


def _deny(
    request_id: uuid.UUID, api_key: ApiKey, approval: Approval, input_hash: str,
    status: int, error: str, extra: dict | None = None,
) -> tuple[int, dict]:
    detail = {"reason": error, **(extra or {})}
    _event(request_id=request_id, api_key=api_key, workflow=approval.workflow,
           input_hash=input_hash, event_type="workflow.denied", status=status,
           approval_id=approval.approval_id, order_id=approval.order_id, detail=detail)
    return status, {"error": error, "request_id": str(request_id), **(extra or {})}


def _conditional_status(db, approval_id: uuid.UUID, expected: str, new_status: str, values: dict) -> None:
    result = db.execute(
        update(Approval).where(Approval.approval_id == approval_id, Approval.status == expected)
        .values(status=new_status, **values).execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise RuntimeError(f"Approval status transition failed approval_id={approval_id} expected={expected}")


def _approve_order(
    approval_id: uuid.UUID, api_key: ApiKey, reason: str | None,
) -> tuple[uuid.UUID | None, str | None]:
    with SessionLocal.begin() as db:
        approval = db.scalar(select(Approval).where(Approval.approval_id == approval_id).with_for_update())
        if approval is None or approval.status != "PENDING":
            return None, approval.status if approval else None
        _conditional_status(db, approval_id, "PENDING", "APPROVED", {
            "decided_by_key_id": api_key.key_id,
            "decided_by_name": api_key.name,
            "decided_at": datetime.now(timezone.utc),
            "decision_reason": reason,
        })
        order = db.scalar(select(Order).where(Order.order_id == approval.order_id).with_for_update())
        if order is None or order.status != "PENDING_APPROVAL":
            raise RuntimeError(f"Pending approval order missing or changed order_id={approval.order_id}")
        order.status = "APPROVED"
        db.add(AuditEvent(order_id=order.order_id, event_type="order.approved",
                          details={"approval_id": str(approval_id)}))
        if db.scalar(select(Job).where(Job.order_id == order.order_id)) is not None:
            raise RuntimeError(f"Approval order already has a job order_id={order.order_id}")
        order.status = "QUEUED"
        create_queued_job(db, order)
        _conditional_status(db, approval_id, "APPROVED", "EXECUTED", {
            "result": {"order_id": str(order.order_id), "status": "QUEUED"},
        })
        return order.order_id, None


def _reject_approval(
    approval_id: uuid.UUID, api_key: ApiKey, reason: str,
) -> tuple[str, uuid.UUID | None, str | None]:
    with SessionLocal.begin() as db:
        approval = db.scalar(select(Approval).where(Approval.approval_id == approval_id).with_for_update())
        if approval is None or approval.status != "PENDING":
            return "already_decided", approval.order_id if approval else None, approval.status if approval else None
        _conditional_status(db, approval_id, "PENDING", "REJECTED", {
            "decided_by_key_id": api_key.key_id,
            "decided_by_name": api_key.name,
            "decided_at": datetime.now(timezone.utc),
            "decision_reason": reason,
        })
        order_id = approval.order_id
        if approval.workflow == "create_order" and order_id is not None:
            order = db.scalar(select(Order).where(Order.order_id == order_id).with_for_update())
            if order is None or order.status != "PENDING_APPROVAL":
                raise RuntimeError(f"Pending approval order missing or changed order_id={order_id}")
            order.status = "CANCELLED"
            db.add(AuditEvent(order_id=order_id, event_type="order.cancelled",
                              details={"rejected_by": api_key.name}))
        return "rejected", order_id, "REJECTED"


def _approve_adjustment(
    approval_id: uuid.UUID, api_key: ApiKey, reason: str | None,
) -> tuple[dict | None, str | None]:
    with SessionLocal.begin() as db:
        approval = db.scalar(select(Approval).where(Approval.approval_id == approval_id).with_for_update())
        if approval is None or approval.status != "PENDING":
            return None, approval.status if approval else None
        input_data = approval.input or {}
        _conditional_status(db, approval_id, "PENDING", "APPROVED", {
            "decided_by_key_id": api_key.key_id,
            "decided_by_name": api_key.name,
            "decided_at": datetime.now(timezone.utc),
            "decision_reason": reason,
        })
    return input_data, None


def _finish_adjustment(approval_id: uuid.UUID, status: str, result: dict) -> None:
    with SessionLocal.begin() as db:
        _conditional_status(db, approval_id, "APPROVED", status, {"result": result})


def _execute_stock_adjustment(approval_id: uuid.UUID, input_data: dict) -> dict:
    adapter = get_adapter()
    reference = f"GW-ADJ:{approval_id}:{input_data['sku']}"
    result = adapter.adjust_stock(reference, input_data["sku"], input_data["qty_delta"], input_data["reason"])
    if not isinstance(result, dict):
        raise ValueError("ERP adapter returned an invalid stock adjustment result")
    return result


def _approve_cancellation(
    approval_id: uuid.UUID, api_key: ApiKey, reason: str | None,
) -> tuple[dict | None, str | None]:
    with SessionLocal.begin() as db:
        approval = db.scalar(select(Approval).where(Approval.approval_id == approval_id).with_for_update())
        if approval is None or approval.status != "PENDING":
            return None, approval.status if approval else None
        input_data = approval.input or {}
        _conditional_status(db, approval_id, "PENDING", "APPROVED", {
            "decided_by_key_id": api_key.key_id,
            "decided_by_name": api_key.name,
            "decided_at": datetime.now(timezone.utc),
            "decision_reason": reason,
        })
    return input_data, None


def _execute_cancellation(
    approval_id: uuid.UUID, input_data: dict, cancelled_by: str,
) -> tuple[str | None, dict | None]:
    order_id = uuid.UUID(input_data["order_id"])
    with SessionLocal.begin() as db:
        error, order, job = _cancellation_eligibility(db, order_id)
        if error:
            return error, None
        if order.erp_order_id:
            erp_order_id = order.erp_order_id
            mode = "erp"
        else:
            if job is None or job.status != "QUEUED":
                return "order_in_progress", None
            job.status = "CANCELLED"
            job.locked_at = None
            job.updated_at = datetime.now(timezone.utc)
            order.status = "CANCELLED"
            db.add(AuditEvent(order_id=order_id, event_type="order.cancelled", details={
                "approval_id": str(approval_id), "cancelled_by": cancelled_by, "mode": "local",
                "reason": input_data["reason"],
            }))
            result = {"order_id": str(order_id), "status": "CANCELLED", "mode": "local"}
            _conditional_status(db, approval_id, "APPROVED", "EXECUTED", {"result": result})
            return None, result

    reference = f"GW-CANCEL:{approval_id}"
    adapter = get_adapter()
    adapter_result = adapter.cancel_order(reference, erp_order_id, input_data["reason"])
    if (not isinstance(adapter_result, dict) or not isinstance(adapter_result.get("cancel_id"), str)
            or type(adapter_result.get("applied")) is not bool):
        raise ValueError("ERP adapter returned an invalid cancellation result")
    try:
        with SessionLocal.begin() as db:
            order = db.scalar(select(Order).where(Order.order_id == order_id).with_for_update())
            if order is None:
                raise RuntimeError(f"Order disappeared after ERP cancellation order_id={order_id}")
            order.status = "CANCELLED"
            db.add(AuditEvent(order_id=order_id, event_type="order.cancelled", details={
                "approval_id": str(approval_id), "cancelled_by": cancelled_by, "mode": mode,
                "reason": input_data["reason"],
            }))
            result = {"order_id": str(order_id), "status": "CANCELLED", "mode": mode}
            _conditional_status(db, approval_id, "APPROVED", "EXECUTED", {"result": result})
    except Exception as exc:
        raise LocalCancellationUpdateError(f"Local update failed after ERP cancellation order_id={order_id}") from exc
    return None, result


def _fail_cancellation(approval_id: uuid.UUID, error: str) -> None:
    with SessionLocal.begin() as db:
        _conditional_status(db, approval_id, "APPROVED", "EXECUTION_FAILED", {"result": {"error": error}})


def _decision_response(request_id: uuid.UUID, status: int, body: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=body, headers={"X-Request-Id": str(request_id)})


@router.post("/approvals/{approval_id}/decision")
async def decide(request: Request, approval_id: str):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated approval decision")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    request_id = uuid.uuid4()
    raw_body = await request.body()
    input_hash = _request_hash(raw_body)
    try:
        parsed_id = uuid.UUID(approval_id)
    except (ValueError, AttributeError):
        logger.info("Unknown approval_id=%s", approval_id)
        return _decision_response(request_id, 404, {"error": "not_found", "request_id": str(request_id)})
    approval = await run_in_threadpool(_approval_snapshot, parsed_id)
    if approval is None:
        logger.info("Unknown approval_id=%s", parsed_id)
        return _decision_response(request_id, 404, {"error": "not_found", "request_id": str(request_id)})
    if not can_decide(api_key.role, approval.workflow):
        status, body = await run_in_threadpool(
            _deny, request_id, api_key, approval, input_hash, 403, "forbidden",
        )
        return _decision_response(request_id, status, body)
    if api_key.key_id == approval.requested_by_key_id:
        status, body = await run_in_threadpool(
            _deny, request_id, api_key, approval, input_hash, 403, "self_approval_not_allowed",
        )
        return _decision_response(request_id, status, body)
    try:
        payload = DecisionInput.model_validate_json(raw_body)
        if payload.decision == "reject" and (payload.reason is None or not payload.reason.strip()):
            raise ValueError("rejection reason is required")
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        detail = {"reasons": _validation_errors(exc)} if isinstance(exc, ValidationError) else {"reason": str(exc)}
        status, body = await run_in_threadpool(
            _deny, request_id, api_key, approval, input_hash, 422, "invalid_request",
            detail,
        )
        return _decision_response(request_id, status, body)

    if payload.decision == "reject":
        outcome, order_id, final_status = await run_in_threadpool(
            _reject_approval, parsed_id, api_key, payload.reason,
        )
        if outcome == "already_decided":
            status, body = await run_in_threadpool(
                _deny, request_id, api_key, approval, input_hash, 409, "already_decided",
                {"status": final_status},
            )
        else:
            await run_in_threadpool(
                _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
                input_hash=input_hash, event_type="workflow.approval_rejected", status=200,
                approval_id=parsed_id, order_id=order_id,
                detail={"decider": api_key.name, "reason": payload.reason},
            )
            status, body = 200, {"request_id": str(request_id), "approval_id": str(parsed_id), "status": "REJECTED"}
        return _decision_response(request_id, status, body)

    if approval.workflow == "create_order":
        order_id, current_status = await run_in_threadpool(_approve_order, parsed_id, api_key, payload.reason)
        if order_id is None:
            status, body = await run_in_threadpool(
                _deny, request_id, api_key, approval, input_hash, 409, "already_decided",
                {"status": current_status},
            )
            return _decision_response(request_id, status, body)
        await _record_approved(request_id, api_key, approval, input_hash, payload.reason)
        await run_in_threadpool(
            _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
            input_hash=input_hash, event_type="workflow.executed", status=200,
            approval_id=parsed_id, order_id=order_id,
        )
        return _decision_response(request_id, 200, {
            "request_id": str(request_id), "approval_id": str(parsed_id), "status": "EXECUTED",
            "order_id": str(order_id),
        })

    if approval.workflow == "cancel_order":
        input_data, current_status = await run_in_threadpool(
            _approve_cancellation, parsed_id, api_key, payload.reason,
        )
        if input_data is None:
            status, body = await run_in_threadpool(
                _deny, request_id, api_key, approval, input_hash, 409, "already_decided", {"status": current_status},
            )
            return _decision_response(request_id, status, body)
        await _record_approved(request_id, api_key, approval, input_hash, payload.reason)
        try:
            error, result = await run_in_threadpool(_execute_cancellation, parsed_id, input_data, api_key.name)
        except LocalCancellationUpdateError as exc:
            order_id = uuid.UUID(input_data["order_id"])
            logger.exception("Local update failed after ERP cancellation approval_id=%s order_id=%s", parsed_id, order_id)
            await run_in_threadpool(
                _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
                input_hash=input_hash, event_type="workflow.failed", status=500,
                approval_id=parsed_id, order_id=order_id, detail={"error": "local_update_failed"},
            )
            return _decision_response(request_id, 500, {
                "request_id": str(request_id), "approval_id": str(parsed_id),
                "status": "APPROVED", "error": "local_update_failed",
            })
        except Exception as exc:
            retryable, reason = classify_failure(error=exc)
            error_code = reason[:200]
            order_id = input_data.get("order_id")
            logger.exception("Cancellation execution failed approval_id=%s order_id=%s", parsed_id, order_id)
            await run_in_threadpool(_fail_cancellation, parsed_id, error_code)
            status = 502 if retryable else 422
            await run_in_threadpool(
                _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
                input_hash=input_hash, event_type="workflow.failed", status=status,
                approval_id=parsed_id, order_id=uuid.UUID(order_id), detail={"error": error_code},
            )
            return _decision_response(request_id, status, {
                "request_id": str(request_id), "approval_id": str(parsed_id),
                "status": "EXECUTION_FAILED", "error": error_code,
            })
        if error:
            await run_in_threadpool(_fail_cancellation, parsed_id, error)
            order_id = uuid.UUID(input_data["order_id"])
            await run_in_threadpool(
                _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
                input_hash=input_hash, event_type="workflow.failed", status=409,
                approval_id=parsed_id, order_id=order_id, detail={"error": error},
            )
            return _decision_response(request_id, 409, {
                "request_id": str(request_id), "approval_id": str(parsed_id),
                "status": "EXECUTION_FAILED", "error": error,
            })
        order_id = uuid.UUID(result["order_id"])
        await run_in_threadpool(
            _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
            input_hash=input_hash, event_type="workflow.executed", status=200,
            approval_id=parsed_id, order_id=order_id, detail={"result": result},
        )
        return _decision_response(request_id, 200, {
            "request_id": str(request_id), "approval_id": str(parsed_id),
            "status": "EXECUTED", "result": result,
        })

    input_data, current_status = await run_in_threadpool(
        _approve_adjustment, parsed_id, api_key, payload.reason,
    )
    if input_data is None:
        status, body = await run_in_threadpool(
            _deny, request_id, api_key, approval, input_hash, 409, "already_decided", {"status": current_status},
        )
        return _decision_response(request_id, status, body)
    await _record_approved(request_id, api_key, approval, input_hash, payload.reason)
    try:
        result = await run_in_threadpool(_execute_stock_adjustment, parsed_id, input_data)
    except Exception as exc:
        retryable, reason = classify_failure(error=exc)
        error_code = reason[:200]
        logger.exception("Stock approval execution failed approval_id=%s order_id=%s", parsed_id, approval.order_id)
        await run_in_threadpool(_finish_adjustment, parsed_id, "EXECUTION_FAILED", {"error": error_code})
        await run_in_threadpool(
            _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
            input_hash=input_hash, event_type="workflow.failed", status=502 if retryable else 422,
            approval_id=parsed_id, order_id=approval.order_id, detail={"error": error_code},
        )
        status = 502 if retryable else 422
        return _decision_response(request_id, status, {
            "request_id": str(request_id), "approval_id": str(parsed_id), "status": "EXECUTION_FAILED",
            "error": error_code,
        })
    await run_in_threadpool(_finish_adjustment, parsed_id, "EXECUTED", result)
    await run_in_threadpool(
        _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
        input_hash=input_hash, event_type="workflow.executed", status=200,
        approval_id=parsed_id, order_id=approval.order_id, detail={"result": result},
    )
    return _decision_response(request_id, 200, {
        "request_id": str(request_id), "approval_id": str(parsed_id), "status": "EXECUTED", "result": result,
    })


async def _record_approved(
    request_id: uuid.UUID, api_key: ApiKey, approval: Approval, input_hash: str, reason: str | None,
) -> None:
    order_id = approval.order_id
    if approval.workflow == "cancel_order":
        order_id = uuid.UUID(approval.input["order_id"])
    await run_in_threadpool(
        _event, request_id=request_id, api_key=api_key, workflow=approval.workflow,
        input_hash=input_hash, event_type="workflow.approved", status=200,
        approval_id=approval.approval_id, order_id=order_id,
        detail={"decider": api_key.name, "reason": reason},
    )
