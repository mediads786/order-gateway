import hmac
import logging
import uuid
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select

from app.admin.auth import COOKIE_NAME, authenticated, configured_token, session_cookie_value
from app.admin.views import ORDER_STATES, PAGE_SIZE, approvals_page, login_page, not_found_page, order_detail_page, orders_page, proposals_page, workflow_events_page
from app.db.models import Approval, AuditEvent, Job, Order, Proposal, Shipment, WorkflowEvent
from app.db.session import SessionLocal
from app.services.retry import requeue_failed_order

logger = logging.getLogger(__name__)
router = APIRouter()


def _login_response(message: str = "", status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(login_page(message), status_code=status_code)


def _authorized(request: Request, *, post: bool = False) -> Response | None:
    if authenticated(request):
        return None
    if post:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return RedirectResponse("/admin/login", status_code=303)


def _refresh_value(value: str | None) -> bool:
    return value != "0"


@router.get("/admin", include_in_schema=False)
def admin_root():
    return RedirectResponse("/admin/orders", status_code=303)


@router.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
def login_get():
    return _login_response()


@router.post("/admin/login", response_class=HTMLResponse, include_in_schema=False)
async def login_post(request: Request):
    form = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
    submitted = form.get("token", [""])[0]
    token = configured_token()
    if not submitted or not hmac.compare_digest(submitted.encode("utf-8"), token.encode("utf-8")):
        logger.warning("Rejected admin login")
        return _login_response("Invalid token", status_code=401)
    response = RedirectResponse("/admin/orders", status_code=303)
    response.set_cookie(
        COOKIE_NAME,
        session_cookie_value(token),
        httponly=True,
        samesite="strict",
        path="/admin",
        secure=request.url.scheme == "https",
    )
    return response


@router.post("/admin/logout", include_in_schema=False)
def logout(request: Request):
    denied = _authorized(request, post=True)
    if denied:
        return denied
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(
        COOKIE_NAME, path="/admin", httponly=True, samesite="strict", secure=request.url.scheme == "https",
    )
    return response


@router.get("/admin/orders", response_class=HTMLResponse, include_in_schema=False)
def list_orders(request: Request, status: str = "", page: str = "1", refresh: str | None = None):
    denied = _authorized(request)
    if denied:
        return denied
    status_filter = status if status in ORDER_STATES else ""
    try:
        page_number = max(1, int(page))
    except (TypeError, ValueError):
        page_number = 1
    with SessionLocal() as db:
        grouped = db.execute(select(Order.status, func.count()).group_by(Order.status)).all()
        counts = {state: count for state, count in grouped}
        query = select(Order, Job).outerjoin(Job, Job.order_id == Order.order_id)
        if status_filter:
            query = query.where(Order.status == status_filter)
        total = counts.get(status_filter, 0) if status_filter else sum(counts.values())
        rows = db.execute(
            query.order_by(Order.created_at.desc(), Order.order_id.desc())
            .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
    return HTMLResponse(orders_page(rows, counts, status_filter, page_number, total, _refresh_value(refresh)))


@router.get("/admin/orders/{order_id}", response_class=HTMLResponse, include_in_schema=False)
def order_detail(request: Request, order_id: str, refresh: str | None = None, msg: str = ""):
    denied = _authorized(request)
    if denied:
        return denied
    try:
        parsed_id = uuid.UUID(order_id)
    except (ValueError, AttributeError):
        return HTMLResponse(not_found_page(), status_code=404)
    with SessionLocal() as db:
        order = db.get(Order, parsed_id)
        if order is None:
            return HTMLResponse(not_found_page(), status_code=404)
        job = db.scalar(select(Job).where(Job.order_id == parsed_id))
        shipments = db.scalars(select(Shipment).where(Shipment.order_id == parsed_id).order_by(Shipment.created_at)).all()
        events = db.scalars(
            select(AuditEvent).where(AuditEvent.order_id == parsed_id).order_by(AuditEvent.event_id)
        ).all()
        html = order_detail_page(order, job, shipments, events, _refresh_value(refresh), msg)
    return HTMLResponse(html)


@router.post("/admin/orders/{order_id}/retry", include_in_schema=False)
def retry_order_admin(request: Request, order_id: str):
    denied = _authorized(request, post=True)
    if denied:
        return denied
    try:
        parsed_id = uuid.UUID(order_id)
    except (ValueError, AttributeError):
        return RedirectResponse("/admin/orders?msg=not_found", status_code=303)
    result = requeue_failed_order(parsed_id, via_admin=True)
    if result == "not_found":
        return RedirectResponse("/admin/orders?msg=not_found", status_code=303)
    message = "requeued" if result == "requeued" else "not_dead"
    return RedirectResponse(f"/admin/orders/{parsed_id}?msg={message}", status_code=303)


@router.get("/admin/workflow-events", response_class=HTMLResponse, include_in_schema=False)
def list_workflow_events(request: Request, type: str = "", page: str = "1", refresh: str | None = None):
    denied = _authorized(request)
    if denied:
        return denied
    event_types = {
        "workflow.requested", "workflow.denied", "workflow.rejected",
        "workflow.executed", "workflow.not_executable", "workflow.failed",
        "workflow.approval_requested", "workflow.approved", "workflow.approval_rejected",
        "workflow.proposed", "workflow.proposal_invalid", "workflow.proposal_confirmed",
        "workflow.proposal_discarded",
    }
    event_filter = type if type in event_types else ""
    try:
        page_number = max(1, int(page))
    except (TypeError, ValueError):
        page_number = 1
    with SessionLocal() as db:
        query = select(WorkflowEvent)
        if event_filter:
            query = query.where(WorkflowEvent.event_type == event_filter)
            total = db.scalar(select(func.count()).select_from(WorkflowEvent).where(WorkflowEvent.event_type == event_filter)) or 0
        else:
            total = db.scalar(select(func.count()).select_from(WorkflowEvent)) or 0
        rows = db.scalars(
            query.order_by(WorkflowEvent.created_at.desc(), WorkflowEvent.event_id.desc())
            .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
    return HTMLResponse(workflow_events_page(rows, event_filter, page_number, total, _refresh_value(refresh)))


@router.get("/admin/approvals", response_class=HTMLResponse, include_in_schema=False)
def list_approvals(request: Request, status: str = "", page: str = "1", refresh: str | None = None):
    denied = _authorized(request)
    if denied:
        return denied
    approval_states = {"PENDING", "APPROVED", "REJECTED", "EXECUTED", "EXECUTION_FAILED"}
    status_filter = status if status in approval_states else ""
    try:
        page_number = max(1, int(page))
    except (TypeError, ValueError):
        page_number = 1
    with SessionLocal() as db:
        query = select(Approval)
        if status_filter:
            query = query.where(Approval.status == status_filter)
            total = db.scalar(
                select(func.count()).select_from(Approval).where(Approval.status == status_filter)
            ) or 0
        else:
            total = db.scalar(select(func.count()).select_from(Approval)) or 0
        rows = db.scalars(
            query.order_by(Approval.created_at.desc(), Approval.approval_id.desc())
            .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
        rendered = []
        for approval in rows:
            if approval.workflow == "create_order" and approval.order_id:
                order = db.get(Order, approval.order_id)
                summary = {"total": str(order.total), "currency": order.currency} if order else {}
            else:
                values = approval.input or {}
                summary = {"sku": values.get("sku"), "qty_delta": values.get("qty_delta")}
            rendered.append((approval, summary))
        html = approvals_page(rendered, status_filter, page_number, total, _refresh_value(refresh))
    return HTMLResponse(html)


@router.get("/admin/proposals", response_class=HTMLResponse, include_in_schema=False)
def list_proposals(request: Request, status: str = "", page: str = "1", refresh: str | None = None):
    denied = _authorized(request)
    if denied:
        return denied
    proposal_states = {"PROPOSED", "INVALID", "CONFIRMED", "DISCARDED"}
    status_filter = status if status in proposal_states else ""
    try:
        page_number = max(1, int(page))
    except (TypeError, ValueError):
        page_number = 1
    with SessionLocal() as db:
        query = select(Proposal)
        if status_filter:
            query = query.where(Proposal.status == status_filter)
            total = db.scalar(
                select(func.count()).select_from(Proposal).where(Proposal.status == status_filter)
            ) or 0
        else:
            total = db.scalar(select(func.count()).select_from(Proposal)) or 0
        rows = db.scalars(
            query.order_by(Proposal.created_at.desc(), Proposal.proposal_id.desc())
            .offset((page_number - 1) * PAGE_SIZE).limit(PAGE_SIZE)
        ).all()
        rendered = []
        for proposal in rows:
            order_id = (proposal.result or {}).get("order_id")
            rendered.append((proposal, order_id))
        html = proposals_page(rendered, status_filter, page_number, total, _refresh_value(refresh))
    return HTMLResponse(html)
