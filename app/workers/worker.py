import logging
import os
import time
import uuid
from decimal import Decimal

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AuditEvent, Job, Order
from app.db.session import SessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ERP_BASE_URL = os.environ["ERP_BASE_URL"].rstrip("/")
ERP_TIMEOUT_SECONDS = float(os.getenv("ERP_TIMEOUT_SECONDS", "5"))
WORKER_POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1"))


def claim_next_job(db: Session) -> Job | None:
    job = db.scalar(
        select(Job)
        .where(Job.status == "QUEUED")
        .order_by(Job.created_at, Job.job_id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        return None
    order = db.get(Order, job.order_id)
    if order is None:
        raise RuntimeError(f"Order not found for job_id={job.job_id}")
    job.status = "PROCESSING"
    job.attempts += 1
    job.locked_at = func.now()
    job.updated_at = func.now()
    order.status = "PROCESSING"
    db.add(AuditEvent(order_id=order.order_id, event_type="order.processing", details={}))
    return job


def _claim_one() -> tuple[uuid.UUID, uuid.UUID, dict] | None:
    with SessionLocal.begin() as db:
        job = claim_next_job(db)
        if job is None:
            return None
        order = db.get(Order, job.order_id)
        payload = {
            "external_id": str(order.order_id),
            "customer": {"name": order.customer_name, "phone": order.customer_phone, "email": order.customer_email},
            "currency": order.currency,
            "lines": [{"sku": line.sku, "qty": line.qty, "unit_price": str(line.unit_price)} for line in order.lines],
            "total": str(order.total),
        }
        return job.job_id, order.order_id, payload


def _mark_done(job_id: uuid.UUID, order_id: uuid.UUID, erp_order_id: str) -> None:
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        order = db.get(Order, order_id)
        job.status = "DONE"
        job.last_error = None
        job.updated_at = func.now()
        order.status = "CONFIRMED"
        order.erp_order_id = erp_order_id
        db.add(AuditEvent(order_id=order_id, event_type="order.confirmed", details={"erp_order_id": erp_order_id}))


def _mark_failed(job_id: uuid.UUID, order_id: uuid.UUID, error: str) -> None:
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        order = db.get(Order, order_id)
        job.status = "FAILED"
        job.last_error = error
        job.updated_at = func.now()
        order.status = "FAILED_DEAD"
        db.add(AuditEvent(order_id=order_id, event_type="order.failed", details={"error": error}))


def process_one() -> bool:
    claimed = _claim_one()
    if claimed is None:
        return False
    job_id, order_id, payload = claimed
    try:
        response = httpx.post(
            f"{ERP_BASE_URL}/sales-orders",
            json=payload,
            timeout=ERP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        erp_order_id = response.json()["erp_order_id"]
        if not isinstance(erp_order_id, str) or not erp_order_id:
            raise ValueError("ERP response did not include a valid erp_order_id")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("ERP call failed job_id=%s order_id=%s", job_id, order_id)
        _mark_failed(job_id, order_id, error)
        return True
    _mark_done(job_id, order_id, erp_order_id)
    return True


def run_until_idle() -> int:
    processed = 0
    while process_one():
        processed += 1
    return processed


def run_forever() -> None:
    while True:
        if not process_one():
            time.sleep(WORKER_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
