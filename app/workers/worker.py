from datetime import datetime, timedelta, timezone
import logging
import os
import random
import time
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters import AdapterLine, AdapterOrder, ErpAdapter, NonRetryableAdapterError, get_adapter
from app.adapters.odoo import OdooAdapter
from app.db.models import AuditEvent, Job, Order
from app.db.session import SessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ERP_TIMEOUT_SECONDS = float(os.getenv("ERP_TIMEOUT_SECONDS", "5"))
WORKER_POLL_INTERVAL_SECONDS = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1"))
RETRY_BASE_SECONDS = float(os.getenv("RETRY_BASE_SECONDS", "2"))
RETRY_MAX_SECONDS = float(os.getenv("RETRY_MAX_SECONDS", "60"))
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
STALE_JOB_SECONDS = int(os.getenv("STALE_JOB_SECONDS", "120"))


def compute_backoff(attempt: int, base: float, cap: float, rand=random.random) -> float:
    delay = min(cap, base * (2 ** max(0, attempt - 1)))
    return delay / 2 + rand() * delay / 2


def classify_failure(
    status_code: int | None = None,
    error: Exception | None = None,
    invalid_response: bool = False,
) -> tuple[bool, str]:
    if isinstance(error, NonRetryableAdapterError):
        return False, error.reason
    if isinstance(error, httpx.HTTPStatusError):
        status_code = error.response.status_code
        if status_code == 429 or status_code >= 500:
            return True, f"http_{status_code}"
        return False, "non_retryable"
    if isinstance(error, httpx.TransportError):
        return True, type(error).__name__
    if error is not None:
        return False, type(error).__name__
    if invalid_response:
        return True, "invalid_response"
    if status_code is None:
        return True, "unknown_failure"
    if status_code == 429 or status_code >= 500:
        return True, f"http_{status_code}"
    if status_code >= 400:
        return False, "non_retryable"
    return True, "invalid_response"


def claim_next_job(db: Session) -> Job | None:
    job = db.scalar(
        select(Job)
        .where(Job.status == "QUEUED", Job.next_attempt_at <= func.now())
        .order_by(Job.next_attempt_at, Job.created_at, Job.job_id)
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
    db.add(AuditEvent(order_id=order.order_id, event_type="order.processing", details={"attempt": job.attempts}))
    return job


def _claim_one() -> AdapterOrder | None:
    with SessionLocal.begin() as db:
        job = claim_next_job(db)
        if job is None:
            return None
        order = db.get(Order, job.order_id)
        return AdapterOrder(
            order_id=order.order_id,
            job_id=job.job_id,
            external_id=str(order.order_id),
            customer={"name": order.customer_name, "phone": order.customer_phone, "email": order.customer_email},
            currency=order.currency,
            lines=[AdapterLine(sku=line.sku, qty=line.qty, unit_price=line.unit_price) for line in order.lines],
            total=order.total,
        )


def _mark_done(job_id: uuid.UUID, order_id: uuid.UUID, erp_order_id: str, duplicate: bool = False) -> None:
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        order = db.get(Order, order_id)
        job.status = "DONE"
        job.last_error = None
        job.locked_at = None
        job.updated_at = func.now()
        order.status = "CONFIRMED"
        order.erp_order_id = erp_order_id
        details = {"erp_order_id": erp_order_id, "attempt": job.attempts}
        if duplicate:
            details["duplicate"] = True
        db.add(AuditEvent(order_id=order_id, event_type="order.confirmed", details=details))


def _mark_failure(job_id: uuid.UUID, order_id: uuid.UUID, error: str, retryable: bool) -> None:
    with SessionLocal.begin() as db:
        job = db.get(Job, job_id)
        order = db.get(Order, order_id)
        job.last_error = error
        job.locked_at = None
        job.updated_at = func.now()
        if retryable and job.attempts < MAX_ATTEMPTS:
            delay = compute_backoff(job.attempts, RETRY_BASE_SECONDS, RETRY_MAX_SECONDS)
            job.status = "QUEUED"
            job.next_attempt_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
            order.status = "RETRYING"
            db.add(AuditEvent(order_id=order_id, event_type="order.retrying", details={
                "attempt": job.attempts,
                "error": error,
                "retry_in_seconds": delay,
                "next_attempt_at": job.next_attempt_at.isoformat().replace("+00:00", "Z"),
            }))
        else:
            job.status = "FAILED"
            order.status = "FAILED_DEAD"
            failure_reason = "max_attempts" if retryable else "non_retryable"
            db.add(AuditEvent(order_id=order_id, event_type="order.failed", details={
                "attempt": job.attempts, "error": error, "reason": failure_reason,
            }))


def recover_stale_jobs() -> int:
    recovered = 0
    cutoff = func.now() - timedelta(seconds=STALE_JOB_SECONDS)
    with SessionLocal.begin() as db:
        jobs = db.scalars(
            select(Job).where(Job.status == "PROCESSING", Job.locked_at < cutoff)
            .order_by(Job.locked_at, Job.job_id).with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            order = db.get(Order, job.order_id)
            job.last_error = "worker_lost"
            job.locked_at = None
            job.updated_at = func.now()
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "FAILED"
                order.status = "FAILED_DEAD"
                db.add(AuditEvent(order_id=order.order_id, event_type="order.failed", details={
                    "attempt": job.attempts, "error": "worker_lost", "reason": "worker_lost",
                }))
            else:
                job.status = "QUEUED"
                job.next_attempt_at = func.now()
                order.status = "RETRYING"
                db.add(AuditEvent(order_id=order.order_id, event_type="order.recovered_stale", details={"attempt": job.attempts}))
            recovered += 1
    return recovered


def process_one(adapter: ErpAdapter | None = None) -> bool:
    selected_adapter = adapter or get_adapter()
    order = _claim_one()
    if order is None:
        return False
    job_id, order_id = order.job_id, order.order_id
    try:
        result = selected_adapter.create_sales_order(order)
        if not isinstance(result.erp_order_id, str) or not result.erp_order_id:
            raise ValueError("ERP adapter did not return a valid erp_order_id")
        duplicate = result.duplicate and isinstance(selected_adapter, OdooAdapter)
        _mark_done(job_id, order_id, result.erp_order_id, duplicate)
        return True
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        retryable, _ = classify_failure(invalid_response=True) if isinstance(exc, ValueError) else classify_failure(error=exc)
        logger.exception("ERP call failed job_id=%s order_id=%s", job_id, order_id)
        _mark_failure(job_id, order_id, error, retryable)
        return True


def run_until_idle() -> int:
    processed = 0
    while process_one():
        processed += 1
    return processed


def run_forever() -> None:
    adapter = get_adapter()
    if os.getenv("ERP_ADAPTER", "mock").strip().lower() == "odoo" and 6 * ERP_TIMEOUT_SECONDS >= STALE_JOB_SECONDS:
        logger.warning(
            "Odoo call budget may exceed stale-job timeout: 6 * ERP_TIMEOUT_SECONDS=%s STALE_JOB_SECONDS=%s",
            6 * ERP_TIMEOUT_SECONDS, STALE_JOB_SECONDS,
        )
    while True:
        recover_stale_jobs()
        if not process_one(adapter):
            time.sleep(WORKER_POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
