import hashlib
import uuid
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import func, select, text, update

from app.db.models import Proposal, WorkflowEvent
from app.db.session import SessionLocal

WORKFLOW_NAME_MAX_LENGTH = 100


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_day_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    current = current.astimezone(timezone.utc)
    start = datetime.combine(current.date(), time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _add_event(
    db, *, request_id: uuid.UUID, api_key, event_type: str, workflow: str | None,
    text_hash: str, proposal_id: uuid.UUID, status: int | None = None, detail: dict | None = None,
) -> None:
    event_workflow = workflow[:WORKFLOW_NAME_MAX_LENGTH] if workflow is not None else None
    db.add(WorkflowEvent(
        request_id=request_id,
        key_id=api_key.key_id,
        actor_name=api_key.name,
        role=api_key.role,
        workflow=event_workflow,
        event_type=event_type,
        http_status=status,
        input_hash=text_hash,
        detail={
            "proposal_id": str(proposal_id),
            "proposer": detail["proposer"],
            "workflow": event_workflow,
            "text_hash": text_hash,
            **{
                key: value for key, value in (detail or {}).items()
                if key not in {"proposer", "workflow"}
            },
        },
        created_at=datetime.now(timezone.utc),
    ))


def count_today(api_key_id: uuid.UUID, now: datetime | None = None) -> int:
    start, end = utc_day_window(now)
    with SessionLocal() as db:
        return db.scalar(
            select(func.count()).select_from(Proposal).where(
                Proposal.requested_by_key_id == api_key_id,
                Proposal.created_at >= start,
                Proposal.created_at < end,
            )
        ) or 0


def store_proposal(
    *, proposal_id: uuid.UUID, request_id: uuid.UUID, api_key, text_value: str, text_hash: str,
    proposer: str, status: str, workflow: str | None, input_data: dict | None,
    explanation: str | None, invalid_reason: str | None, daily_limit: int,
) -> bool:
    now = datetime.now(timezone.utc)
    start, end = utc_day_window(now)
    with SessionLocal.begin() as db:
        lock_key = f"proposal-limit:{api_key.key_id}:{start.date().isoformat()}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})
        count = db.scalar(
            select(func.count()).select_from(Proposal).where(
                Proposal.requested_by_key_id == api_key.key_id,
                Proposal.created_at >= start,
                Proposal.created_at < end,
            )
        ) or 0
        if count >= daily_limit:
            return False
        db.add(Proposal(
            proposal_id=proposal_id,
            requested_by_key_id=api_key.key_id,
            requested_by_name=api_key.name,
            created_at=now,
            text=text_value,
            text_hash=text_hash,
            proposer=proposer,
            status=status,
            workflow=workflow,
            input=input_data,
            explanation=explanation,
            invalid_reason=invalid_reason,
        ))
        _add_event(
            db, request_id=request_id, api_key=api_key,
            event_type="workflow.proposed" if status == "PROPOSED" else "workflow.proposal_invalid",
            workflow=workflow, text_hash=text_hash, proposal_id=proposal_id,
            status=201 if status == "PROPOSED" else 200,
            detail={"proposer": proposer, "invalid_reason": invalid_reason},
        )
    return True


def proposal_body(proposal: Proposal) -> dict:
    return {
        "proposal_id": str(proposal.proposal_id),
        "status": proposal.status,
        "workflow": proposal.workflow[:WORKFLOW_NAME_MAX_LENGTH]
        if proposal.workflow is not None else None,
        "input": proposal.input,
        "explanation": proposal.explanation,
        "invalid_reason": proposal.invalid_reason,
        "text_length": len(proposal.text),
        "proposer": proposal.proposer,
        "created_at": proposal.created_at.astimezone(timezone.utc).isoformat(),
        "decided_at": proposal.decided_at.astimezone(timezone.utc).isoformat() if proposal.decided_at else None,
        "result": proposal.result,
    }


def proposal_event(
    db, *, request_id: uuid.UUID, api_key, event_type: str, proposal: Proposal,
    status: int | None, detail: dict,
) -> None:
    _add_event(
        db, request_id=request_id, api_key=api_key, event_type=event_type,
        workflow=proposal.workflow, text_hash=proposal.text_hash,
        proposal_id=proposal.proposal_id, status=status,
        detail={"proposer": proposal.proposer, **detail},
    )


def transition_proposal(db, proposal_id: uuid.UUID, status: str, decided_at: datetime,
                        result: dict | None = None) -> bool:
    values = {"status": status, "decided_at": decided_at}
    if result is not None:
        values["result"] = result
    changed = db.execute(
        update(Proposal)
        .where(Proposal.proposal_id == proposal_id, Proposal.status == "PROPOSED")
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return changed.rowcount == 1
