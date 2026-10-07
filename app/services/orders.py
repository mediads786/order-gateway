import hashlib
import json
import logging
from datetime import timezone
from dataclasses import dataclass
from decimal import Decimal
import uuid

from pydantic import ValidationError
from sqlalchemy import text

from app.core.schemas import OrderInput
from app.db.models import Approval, AuditEvent, IdempotencyKey, Job, Order, OrderLine
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)


class NonFiniteJSON(ValueError):
    pass


@dataclass(frozen=True)
class HoldPolicy:
    threshold: Decimal
    request_id: uuid.UUID
    requester_key_id: uuid.UUID
    requester_name: str


def create_queued_job(db, order: Order) -> Job:
    job = Job(order_id=order.order_id, status="QUEUED", attempts=0)
    db.add(job)
    db.add(AuditEvent(order_id=order.order_id, event_type="order.queued", details={}))
    db.flush()
    return job


def _replay_status(status_code: int) -> int:
    if status_code in (202, 422):
        return status_code
    return 200


def _reject_constant(value: str) -> None:
    raise NonFiniteJSON(f"Non-finite number {value} is not allowed")


def _contains_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, dict):
        return any(_contains_nul(key) or _contains_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_nul(item) for item in value)
    return False


def _request_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def serialize_order(order: Order) -> dict:
    created_at = order.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "order_id": str(order.order_id),
        "source": order.source,
        "external_ref": order.external_ref,
        "customer": {"name": order.customer_name, "phone": order.customer_phone, "email": order.customer_email},
        "currency": order.currency,
        "lines": [{"sku": line.sku, "qty": line.qty, "unit_price": str(line.unit_price)} for line in order.lines],
        "total": str(order.total),
        "status": order.status,
        "created_at": created_at.astimezone(timezone.utc).isoformat(),
    }


def _lock_idempotency_key(db, key: str) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": key})


def _optional_text(value: object, max_length: int | None = None) -> str | None:
    if not isinstance(value, str) or "\x00" in value:
        return None
    if max_length is not None and len(value) > max_length:
        return None
    return value


def _rejected_order(payload: object, reasons: list[dict], key: str, digest: str) -> tuple[int, dict]:
    with SessionLocal.begin() as db:
        _lock_idempotency_key(db, key)
        previous = db.get(IdempotencyKey, key)
        if previous:
            if previous.request_hash != digest:
                return 409, {"detail": "Idempotency-Key was used with a different request body"}
            return _replay_status(previous.status_code), previous.response_json
        customer = payload.get("customer") if isinstance(payload, dict) else None
        customer = customer if isinstance(customer, dict) else {}
        order = Order(source=_optional_text(payload.get("source"), 20) if isinstance(payload, dict) else None,
                      external_ref=_optional_text(payload.get("external_ref")) if isinstance(payload, dict) else None,
                      customer_name=_optional_text(customer.get("name")),
                      customer_phone=_optional_text(customer.get("phone")),
                      customer_email=_optional_text(customer.get("email")),
                      currency=_optional_text(payload.get("currency"), 3) if isinstance(payload, dict) else None,
                      total=Decimal("0"), status="REJECTED",
                      raw_payload=payload)
        db.add(order)
        db.flush()
        db.add(AuditEvent(order_id=order.order_id, event_type="order.rejected", details={"reasons": reasons}))
        response_body = {"order_id": str(order.order_id), "status": "REJECTED", "reasons": reasons}
        db.add(IdempotencyKey(key=key, request_hash=digest, response_json=response_body,
                              status_code=422, order_id=order.order_id))
        logger.info("Rejected order_id=%s reasons=%s", order.order_id, reasons)
        return 422, response_body


def _validation_reasons(exc: ValidationError) -> list[dict]:
    return [{"field": ".".join(str(part) for part in error["loc"]), "reason": error["msg"]} for error in exc.errors()]


def submit_order(
    raw_body: bytes, idempotency_key: str, *, hold: HoldPolicy | None = None,
) -> tuple[int, dict]:
    try:
        payload = json.loads(raw_body, parse_constant=_reject_constant)
    except NonFiniteJSON as exc:
        raw_payload = raw_body.decode("utf-8", errors="replace").replace("\x00", "\\u0000")
        digest = hashlib.sha256(raw_body).hexdigest()
        return _rejected_order(raw_payload, [{"field": "body", "reason": str(exc)}], idempotency_key, digest)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raw_payload = raw_body.decode("utf-8", errors="replace").replace("\x00", "\\u0000")
        digest = hashlib.sha256(raw_body).hexdigest()
        return _rejected_order(raw_payload, [{"field": "body", "reason": "Invalid JSON"}], idempotency_key, digest)

    if _contains_nul(payload):
        raw_payload = raw_body.decode("utf-8", errors="replace").replace("\x00", "\\u0000")
        digest = hashlib.sha256(raw_body).hexdigest()
        return _rejected_order(raw_payload, [{"field": "body", "reason": "NUL characters are not allowed"}], idempotency_key, digest)

    digest = _request_hash(payload)
    try:
        order_data = OrderInput.model_validate(payload)
    except ValidationError as exc:
        return _rejected_order(payload, _validation_reasons(exc), idempotency_key, digest)

    with SessionLocal.begin() as db:
        _lock_idempotency_key(db, idempotency_key)
        previous = db.get(IdempotencyKey, idempotency_key)
        if previous:
            if previous.request_hash != digest:
                return 409, {"detail": "Idempotency-Key was used with a different request body"}
            return _replay_status(previous.status_code), previous.response_json
        total = sum((line.unit_price * line.qty for line in order_data.lines), Decimal("0"))
        requires_approval = hold is not None and total > hold.threshold
        approval_id = uuid.uuid4() if requires_approval else None
        order = Order(source=order_data.source, external_ref=order_data.external_ref,
                      customer_name=order_data.customer.name, customer_phone=order_data.customer.phone,
                      customer_email=str(order_data.customer.email) if order_data.customer.email else None,
                      currency=order_data.currency.upper(), total=total,
                      status="PENDING_APPROVAL" if requires_approval else "RECEIVED", raw_payload=payload,
                      lines=[OrderLine(sku=line.sku, qty=line.qty, unit_price=line.unit_price) for line in order_data.lines])
        db.add(order)
        db.flush()
        db.add(AuditEvent(order_id=order.order_id, event_type="order.received", details={}))
        db.flush()
        response_status = 201
        if requires_approval:
            approval = Approval(
                approval_id=approval_id,
                request_id=hold.request_id,
                workflow="create_order",
                status="PENDING",
                order_id=order.order_id,
                requested_by_key_id=hold.requester_key_id,
                requested_by_name=hold.requester_name,
            )
            db.add(approval)
            db.add(AuditEvent(
                order_id=order.order_id,
                event_type="order.pending_approval",
                details={"approval_id": str(approval_id)},
            ))
            response_status = 202
        else:
            create_queued_job(db, order)
        response_body = serialize_order(order)
        if approval_id is not None:
            response_body["approval_id"] = str(approval_id)
        db.add(IdempotencyKey(key=idempotency_key, request_hash=digest, response_json=response_body,
                              status_code=response_status, order_id=order.order_id))
        logger.info("Received order_id=%s", order.order_id)
        return response_status, response_body
