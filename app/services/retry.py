import uuid
from typing import Literal

from sqlalchemy import func, select

from app.db.models import AuditEvent, Job, Order
from app.db.session import SessionLocal


def requeue_failed_order(
    order_id: uuid.UUID, *, via_admin: bool = False,
) -> Literal["not_found", "no_job", "not_dead", "requeued"]:
    with SessionLocal.begin() as db:
        order = db.scalar(select(Order).where(Order.order_id == order_id).with_for_update())
        if order is None:
            return "not_found"
        job = db.scalar(select(Job).where(Job.order_id == order_id).with_for_update())
        if job is None:
            return "no_job"
        if order.status != "FAILED_DEAD" or job.status != "FAILED":
            return "not_dead"
        previous_attempts = job.attempts
        job.status = "QUEUED"
        job.attempts = 0
        job.next_attempt_at = func.now()
        job.locked_at = None
        job.updated_at = func.now()
        order.status = "QUEUED"
        details = {"previous_attempts": previous_attempts}
        if via_admin:
            details["via"] = "admin"
        db.add(AuditEvent(order_id=order_id, event_type="order.requeued", details=details))
    return "requeued"
