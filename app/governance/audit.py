import uuid

from app.db.models import WorkflowEvent
from app.db.session import SessionLocal


def write_event(
    *, request_id: uuid.UUID, event_type: str, workflow: str, input_hash: str,
    key_id: uuid.UUID | None, actor_name: str | None, role: str | None,
    http_status: int | None = None, order_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> None:
    with SessionLocal.begin() as db:
        db.add(WorkflowEvent(
            request_id=request_id,
            key_id=key_id,
            actor_name=actor_name,
            role=role,
            workflow=workflow[:100],
            event_type=event_type,
            http_status=http_status,
            order_id=order_id,
            input_hash=input_hash,
            detail=detail,
        ))
