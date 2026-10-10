import hashlib
import json
import logging
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool

from app.adapters import ErpAdapter, NonRetryableAdapterError, ShipmentLine
from app.db.models import AuditEvent, Order, Shipment
from app.db.session import SessionLocal
from app.workers.worker import classify_failure

logger = logging.getLogger(__name__)


async def apply_shipment(shipment_id: str, order_id: uuid.UUID, lines: list[dict[str, str | int]], request_hash: str,
                         adapter: ErpAdapter) -> tuple[int, dict]:
    canonical_lines = [{"sku": line["sku"], "qty": line["qty"]} for line in lines]
    seen_skus: set[str] = set()
    for line in canonical_lines:
        sku = line["sku"]
        if sku in seen_skus:
            return 422, {"error": f"duplicate_sku:{sku}"}
        seen_skus.add(sku)
    with SessionLocal.begin() as db:
        order = db.get(Order, order_id)
        if order is None:
            return 404, {"error": "order_not_found"}
        if order.status != "CONFIRMED":
            return 409, {"error": "order_not_confirmed"}
        order_skus = {line.sku for line in order.lines}
        unknown_skus = [line["sku"] for line in canonical_lines if line["sku"] not in order_skus]
        if unknown_skus:
            return 422, {"error": "sku_not_on_order", "sku": unknown_skus[0]}
        insert_result = db.execute(
            insert(Shipment).values(
                shipment_id=shipment_id,
                order_id=order_id,
                status="PENDING",
                request_hash=request_hash,
                lines=canonical_lines,
            ).on_conflict_do_nothing(index_elements=[Shipment.shipment_id])
            .returning(Shipment.shipment_id)
        )
        is_new = insert_result.scalar_one_or_none() is not None
        shipment = db.scalar(select(Shipment).where(Shipment.shipment_id == shipment_id).with_for_update())
        if shipment is None:
            raise RuntimeError(f"Shipment row unavailable shipment_id={shipment_id}")
        if shipment.request_hash != request_hash:
            return 409, {"error": "shipment_id_conflict"}
        if shipment.status == "APPLIED":
            return 200, {"shipment_id": shipment_id, "status": "APPLIED", "erp_reference": shipment.erp_reference}

        if is_new:
            db.add(AuditEvent(order_id=order_id, event_type="shipment.received",
                              details={"shipment_id": shipment_id, "lines": canonical_lines}))
        db.flush()
        adapter_lines = [ShipmentLine(sku=line["sku"], qty=line["qty"]) for line in canonical_lines]
        try:
            erp_reference = await run_in_threadpool(
                adapter.adjust_stock_for_shipment, shipment_id, adapter_lines,
            )
        except Exception as exc:
            retryable, _ = classify_failure(invalid_response=True) if isinstance(exc, ValueError) else classify_failure(error=exc)
            error = f"{type(exc).__name__}: {exc}"[:500]
            shipment.status = "PENDING"
            shipment.last_error = error
            db.add(AuditEvent(order_id=order_id, event_type="shipment.failed",
                              details={"shipment_id": shipment_id, "error": error, "retryable": retryable}))
            logger.exception("Shipment ERP call failed shipment_id=%s order_id=%s", shipment_id, order_id)
            return (502 if retryable else 422), {"status": "PENDING", "error": error}

        shipment.status = "APPLIED"
        shipment.last_error = None
        shipment.erp_reference = erp_reference
        shipment.applied_at = datetime.now(timezone.utc)
        db.add(AuditEvent(order_id=order_id, event_type="shipment.applied",
                          details={"shipment_id": shipment_id, "erp_reference": erp_reference}))
        logger.info("Shipment applied shipment_id=%s order_id=%s", shipment_id, order_id)
        return 200, {"shipment_id": shipment_id, "status": "APPLIED", "erp_reference": erp_reference}


def canonical_request_hash(shipment_id: str, order_id: str, lines: list[dict]) -> str:
    body = {"shipment_id": shipment_id, "order_id": order_id, "lines": lines}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
