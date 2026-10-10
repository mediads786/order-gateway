import asyncio
import json
import logging
import math
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import func, select, update
from starlette.concurrency import run_in_threadpool

from app.db.models import ApiKey, Proposal, WorkflowEvent
from app.core.schemas import reject_nul
from app.db.session import SessionLocal
from app.governance.keys import authenticate_api_key
from app.governance.registry import WORKFLOWS, can_request
from app.proposals.proposers import (
    AllowedWorkflow,
    ProposerConfigurationError,
    ProposerResult,
    get_proposer,
)
from app.proposals.service import (
    WORKFLOW_NAME_MAX_LENGTH,
    count_today,
    proposal_body,
    proposal_event,
    store_proposal,
    text_digest,
    transition_proposal,
)

logger = logging.getLogger(__name__)
router = APIRouter()
PROPOSAL_STATES = ("PROPOSED", "INVALID", "CONFIRMED", "DISCARDED")
PAGE_SIZE = 25


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return reject_nul(value)


def _response(status: int, body: dict, request_id: uuid.UUID | None = None) -> JSONResponse:
    headers = {"X-Request-Id": str(request_id)} if request_id else None
    return JSONResponse(status_code=status, content=body, headers=headers)


def _config_values() -> tuple[int, int, float] | None:
    try:
        daily_limit = int(os.getenv("PROPOSAL_DAILY_LIMIT", "50"))
        max_chars = int(os.getenv("PROPOSAL_TEXT_MAX_CHARS", "1000"))
        timeout = float(os.getenv("PROPOSER_TIMEOUT_SECONDS", "20"))
        if daily_limit < 0 or max_chars < 1 or not math.isfinite(timeout) or timeout <= 0:
            return None
        return daily_limit, max_chars, timeout
    except (TypeError, ValueError, OverflowError):
        return None


def _allowed_workflows(role: str) -> list[AllowedWorkflow]:
    return [
        AllowedWorkflow(
            name=workflow.name,
            description=workflow.description,
            input_schema=workflow.input_model.model_json_schema(),
        )
        for workflow in WORKFLOWS.values()
        if workflow.executable and can_request(role, workflow.name)
    ]


def _invalid_explanation(exc: ValidationError) -> str:
    errors = [
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()
    ]
    return ("Input did not validate: " + "; ".join(errors))[:300]


def _proposal_visible(proposal: Proposal, api_key: ApiKey) -> bool:
    return api_key.role in ("approver", "admin") or proposal.requested_by_key_id == api_key.key_id


def _proposal_list(api_key: ApiKey, status: str, page: int) -> list[dict]:
    with SessionLocal() as db:
        query = select(Proposal)
        if api_key.role not in ("approver", "admin"):
            query = query.where(Proposal.requested_by_key_id == api_key.key_id)
        if status in PROPOSAL_STATES:
            query = query.where(Proposal.status == status)
        rows = db.scalars(
            query.order_by(Proposal.created_at.desc(), Proposal.proposal_id.desc())
            .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
        return [proposal_body(row) for row in rows]


@router.post("/proposals")
async def create_proposal(request: Request):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        logger.warning("Rejected unauthenticated proposal request")
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    request_id = uuid.uuid4()
    if api_key.role not in ("operator", "admin"):
        logger.info("Proposal request denied request_id=%s role=%s", request_id, api_key.role)
        return _response(403, {"error": "forbidden", "request_id": str(request_id)}, request_id)

    raw_body = await request.body()
    try:
        payload = ProposalRequest.model_validate_json(raw_body)
    except (ValidationError, ValueError, json.JSONDecodeError):
        return _response(422, {"error": "invalid_request", "request_id": str(request_id)}, request_id)

    settings = _config_values()
    if settings is None:
        logger.error("Invalid proposal configuration request_id=%s", request_id)
        return _response(503, {"error": "proposal_config_invalid", "request_id": str(request_id)}, request_id)
    daily_limit, max_chars, _ = settings
    if len(payload.text) > max_chars:
        return _response(422, {"error": "text_too_long", "request_id": str(request_id)}, request_id)
    if await run_in_threadpool(count_today, api_key.key_id) >= daily_limit:
        return _response(429, {"error": "proposal_limit_reached", "request_id": str(request_id)}, request_id)

    proposer_name = os.getenv("PROPOSER", "rule").strip().lower()
    try:
        proposer = get_proposer()
    except ProposerConfigurationError:
        logger.error("Proposal proposer is not configured request_id=%s", request_id)
        return _response(503, {"error": "proposer_not_configured", "request_id": str(request_id)}, request_id)
    except (TypeError, ValueError, OverflowError):
        logger.error("Invalid proposer configuration request_id=%s", request_id)
        return _response(503, {"error": "proposal_config_invalid", "request_id": str(request_id)}, request_id)

    text_hash = text_digest(payload.text)
    proposal_id = uuid.uuid4()
    status = "INVALID"
    workflow_name = None
    input_data = None
    explanation = None
    invalid_reason = None
    try:
        result = await run_in_threadpool(proposer.propose, payload.text, _allowed_workflows(api_key.role))
        if not isinstance(result, ProposerResult):
            raise TypeError("proposer returned an invalid result")
        if result.explanation is not None and not isinstance(result.explanation, str):
            raise TypeError("proposer explanation has an invalid type")
        explanation = result.explanation[:300] if result.explanation is not None else None
        workflow_name = (
            result.workflow[:WORKFLOW_NAME_MAX_LENGTH]
            if isinstance(result.workflow, str) else None
        )
        if result.workflow is None:
            invalid_reason = "no_matching_workflow"
        elif not isinstance(result.workflow, str) or result.workflow not in {
            item.name for item in _allowed_workflows(api_key.role)
        }:
            invalid_reason = "workflow_not_allowed"
        else:
            workflow = WORKFLOWS[result.workflow]
            try:
                validated = workflow.input_model.model_validate(result.input)
            except ValidationError as exc:
                invalid_reason = "schema_invalid"
                explanation = _invalid_explanation(exc)
            else:
                input_data = validated.model_dump(mode="json")
                status = "PROPOSED"
    except Exception as exc:
        response_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.warning(
            "Proposer failed request_id=%s exception=%s status=%s",
            request_id, type(exc).__name__, response_status,
        )
        invalid_reason = "proposer_error"
        workflow_name = None
        input_data = None
        explanation = "The proposer could not process this text."

    stored = await run_in_threadpool(
        store_proposal,
        proposal_id=proposal_id,
        request_id=request_id,
        api_key=api_key,
        text_value=payload.text,
        text_hash=text_hash,
        proposer=proposer_name,
        status=status,
        workflow=workflow_name,
        input_data=input_data,
        explanation=explanation,
        invalid_reason=invalid_reason,
        daily_limit=daily_limit,
    )
    if not stored:
        return _response(429, {"error": "proposal_limit_reached", "request_id": str(request_id)}, request_id)
    body = {
        "proposal_id": str(proposal_id),
        "status": status,
        "workflow": workflow_name,
        "input": input_data,
        "explanation": explanation,
        "needs_approval": WORKFLOWS[workflow_name].approval if status == "PROPOSED" else None,
    }
    if invalid_reason:
        body["invalid_reason"] = invalid_reason
    return _response(201 if status == "PROPOSED" else 200, body, request_id)


@router.get("/proposals")
async def list_proposals(request: Request, status: str = "", page: int = 1):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await run_in_threadpool(_proposal_list, api_key, status, max(1, page))


@router.get("/proposals/{proposal_id}")
async def get_proposal(request: Request, proposal_id: str):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    try:
        parsed_id = uuid.UUID(proposal_id)
    except (ValueError, AttributeError):
        return JSONResponse(status_code=404, content={"error": "not_found"})
    with SessionLocal() as db:
        proposal = db.get(Proposal, parsed_id)
        if proposal is None or not _proposal_visible(proposal, api_key):
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return proposal_body(proposal)


def _confirm_result(status: int, body: dict, request_id: uuid.UUID) -> dict:
    result = {"status": status, "request_id": str(request_id)}
    if body.get("approval_id"):
        result["approval_id"] = body["approval_id"]
    nested = body.get("result")
    if isinstance(nested, dict) and nested.get("order_id"):
        result["order_id"] = nested["order_id"]
    elif body.get("order_id"):
        result["order_id"] = body["order_id"]
    return result


@router.post("/proposals/{proposal_id}/confirm")
def confirm_proposal(request: Request, proposal_id: str):
    api_key = authenticate_api_key(request.headers.get("X-API-Key"))
    if api_key is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    try:
        parsed_id = uuid.UUID(proposal_id)
    except (ValueError, AttributeError):
        return JSONResponse(status_code=404, content={"error": "not_found"})

    request_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        proposal = db.scalar(
            select(Proposal).where(Proposal.proposal_id == parsed_id).with_for_update()
        )
        if proposal is None or proposal.requested_by_key_id != api_key.key_id:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        if proposal.status != "PROPOSED":
            return _response(409, {
                "error": "already_decided", "status": proposal.status, "request_id": str(request_id),
            }, request_id)
        if not can_request(api_key.role, proposal.workflow or ""):
            db.add(WorkflowEvent(
                request_id=request_id, key_id=api_key.key_id, actor_name=api_key.name,
                role=api_key.role,
                workflow=proposal.workflow[:WORKFLOW_NAME_MAX_LENGTH]
                if proposal.workflow is not None else None,
                event_type="workflow.denied",
                http_status=403, input_hash=proposal.text_hash,
                detail={"reason": "forbidden", "proposal_id": str(parsed_id),
                        "proposer": proposal.proposer, "text_hash": proposal.text_hash},
                created_at=datetime.now(timezone.utc),
            ))
            return _response(403, {"error": "forbidden", "request_id": str(request_id)}, request_id)
        from app.governance.routes import run_governed_request

        status, body = asyncio.run(run_governed_request(
            api_key,
            proposal.workflow,
            proposal.input,
            f"PROP:{parsed_id}",
            request_id,
            proposal.text_hash,
        ))
        outcome = _confirm_result(status, body, request_id)
        if not transition_proposal(db, parsed_id, "CONFIRMED", datetime.now(timezone.utc), outcome):
            return _response(409, {
                "error": "already_decided", "status": proposal.status, "request_id": str(request_id),
            }, request_id)
        db.add(WorkflowEvent(
            request_id=request_id, key_id=api_key.key_id, actor_name=api_key.name,
            role=api_key.role,
            workflow=proposal.workflow[:WORKFLOW_NAME_MAX_LENGTH]
            if proposal.workflow is not None else None,
            event_type="workflow.proposal_confirmed",
            http_status=status, input_hash=proposal.text_hash,
            detail={
                "proposal_id": str(parsed_id), "proposer": proposal.proposer,
                "workflow": proposal.workflow[:WORKFLOW_NAME_MAX_LENGTH]
                if proposal.workflow is not None else None,
                "text_hash": proposal.text_hash,
                "status": status, "request_id": str(request_id),
            },
            created_at=datetime.now(timezone.utc),
        ))
        response_body = {**body, "proposal_id": str(parsed_id)}
    return _response(status, response_body, request_id)


@router.post("/proposals/{proposal_id}/discard")
async def discard_proposal(request: Request, proposal_id: str):
    api_key = await run_in_threadpool(authenticate_api_key, request.headers.get("X-API-Key"))
    if api_key is None:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    try:
        parsed_id = uuid.UUID(proposal_id)
    except (ValueError, AttributeError):
        return JSONResponse(status_code=404, content={"error": "not_found"})
    request_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        proposal = db.scalar(
            select(Proposal).where(Proposal.proposal_id == parsed_id).with_for_update()
        )
        if proposal is None or proposal.requested_by_key_id != api_key.key_id:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        if proposal.status != "PROPOSED":
            return _response(409, {
                "error": "already_decided", "status": proposal.status, "request_id": str(request_id),
            }, request_id)
        if not transition_proposal(db, parsed_id, "DISCARDED", datetime.now(timezone.utc)):
            return _response(409, {
                "error": "already_decided", "status": proposal.status, "request_id": str(request_id),
            }, request_id)
        proposal_event(
            db, request_id=request_id, api_key=api_key, event_type="workflow.proposal_discarded",
            proposal=proposal, status=200, detail={"status": "DISCARDED"},
        )
        body = {"proposal_id": str(parsed_id), "status": "DISCARDED", "request_id": str(request_id)}
    return _response(200, body, request_id)
