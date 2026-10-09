import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timezone

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select

from app.db.models import Order
from app.db.session import SessionLocal
from app.adapters import get_adapter
from app.adapters.factory import AdapterConfigurationError
from app.core.schemas import ShipmentInput
from app.services.orders import serialize_order, submit_order, submit_unmappable_order
from app.services.shipments import apply_shipment, canonical_request_hash
from app.services.shopify_webhooks import UnmappableShopifyOrder, map_shopify_order, verify_signature
from app.admin.auth import admin_enabled
from app.admin.routes import router as admin_router
from app.services.retry import requeue_failed_order
from app.governance.routes import router as governance_router
from app.governance.approvals import router as approvals_router
from app.governance.legacy import legacy_guard
from app.proposals.routes import router as proposals_router


def validate_erp_adapter_setting() -> None:
    adapter = os.getenv("ERP_ADAPTER", "mock").strip().lower()
    if adapter not in {"mock", "odoo"}:
        raise RuntimeError(f"Unsupported ERP_ADAPTER value: {adapter!r}; expected 'mock' or 'odoo'")
    if not admin_enabled():
        logger.warning("Admin pages disabled: ADMIN_TOKEN is empty or shorter than 16 characters")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    validate_erp_adapter_setting()
    yield


app = FastAPI(lifespan=lifespan)
logger = logging.getLogger(__name__)
app.include_router(admin_router)
app.include_router(governance_router)
app.include_router(approvals_router)
app.include_router(proposals_router)


@app.middleware("http")
async def admin_response_headers(request: Request, call_next):
    if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
        if not admin_enabled():
            response = JSONResponse(status_code=503, content={"error": "admin_disabled"})
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        return response
    return await call_next(request)


@app.post("/orders")
async def create_order(request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    actor = await legacy_guard(request, "legacy:create_order", ("operator", "admin"))
    if isinstance(actor, JSONResponse):
        return actor
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    raw_body = await request.body()
    if actor is None:
        status_code, body = await run_in_threadpool(submit_order, raw_body, idempotency_key)
    else:
        status_code, body = await run_in_threadpool(
            submit_order, raw_body, idempotency_key, requested_by=actor.name,
        )
    response.status_code = status_code
    return body


@app.post("/webhooks/shopify/orders-create")
async def shopify_orders_create(request: Request):
    raw_body = await request.body()
    secret = os.getenv("SHOPIFY_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("Shopify webhook secret is not configured")
        raise HTTPException(status_code=503, detail="Shopify webhook is not configured")

    signature = request.headers.get("X-Shopify-Hmac-Sha256")
    if not verify_signature(secret, signature, raw_body):
        logger.warning("Rejected Shopify webhook with invalid signature")
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature")

    webhook_id = request.headers.get("X-Shopify-Webhook-Id")
    if not webhook_id or not webhook_id.strip():
        raise HTTPException(status_code=400, detail="X-Shopify-Webhook-Id header is required")
    topic = request.headers.get("X-Shopify-Topic")
    if topic is not None and topic != "orders/create":
        return {"status": "ignored"}

    idempotency_key = f"shopify:{webhook_id}"
    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        status_code, body = await run_in_threadpool(submit_order, raw_body, idempotency_key)
    else:
        try:
            canonical = map_shopify_order(payload)
        except UnmappableShopifyOrder as exc:
            status_code, body = await run_in_threadpool(
                submit_unmappable_order, raw_body, str(exc), idempotency_key,
            )
        else:
            canonical_bytes = json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            status_code, body = await run_in_threadpool(submit_order, canonical_bytes, idempotency_key)
    if status_code in (200, 201, 422):
        return JSONResponse(
            status_code=200,
            content={"order_id": body["order_id"], "status": body["status"]},
        )
    return JSONResponse(status_code=status_code, content=body)


def _shipment_json_constant(value: str):
    raise ValueError(f"Invalid JSON constant: {value}")


@app.post("/shipments")
async def create_shipment(request: Request):
    raw_body = await request.body()
    secret = os.getenv("SHIPMENT_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("Shipment webhook secret is not configured")
        return JSONResponse(status_code=503, content={"error": "shipment webhook is not configured"})
    signature = request.headers.get("X-Gateway-Signature")
    if not verify_signature(secret, signature, raw_body):
        logger.warning("Rejected shipment webhook with invalid signature")
        return JSONResponse(status_code=401, content={"error": "invalid signature"})
    if os.getenv("ERP_ADAPTER", "mock").strip().lower() != "odoo":
        return JSONResponse(status_code=501, content={"error": "shipments require ERP_ADAPTER=odoo"})
    try:
        adapter = get_adapter()
    except AdapterConfigurationError:
        logger.error("Shipment adapter is not configured")
        return JSONResponse(status_code=503, content={"error": "erp_adapter_not_configured"})
    try:
        decoded = json.loads(raw_body, parse_constant=_shipment_json_constant)
        shipment = ShipmentInput.model_validate(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, ValidationError) as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    shipment_id = shipment.shipment_id
    order_id = str(shipment.order_id)
    lines = [line.model_dump() for line in shipment.lines]
    digest = canonical_request_hash(shipment_id, order_id, lines)
    status_code, body = await apply_shipment(shipment_id, shipment.order_id, lines, digest, adapter)
    return JSONResponse(status_code=status_code, content=body)


@app.get("/orders/{order_id}")
async def get_order(request: Request, order_id: uuid.UUID):
    actor = await legacy_guard(request, "legacy:get_order", ("operator", "approver", "admin"))
    if isinstance(actor, JSONResponse):
        return actor
    return await run_in_threadpool(_get_order, order_id)


def _get_order(order_id: uuid.UUID) -> dict:
    with SessionLocal() as db:
        order = db.scalar(select(Order).where(Order.order_id == order_id))
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        result = serialize_order(order)
        result["audit_events"] = [{"event_type": event.event_type, "details": event.details,
                                   "created_at": event.created_at.astimezone(timezone.utc).isoformat()}
                                  for event in order.events]
        return result


@app.post("/orders/{order_id}/retry")
async def retry_order(request: Request, order_id: uuid.UUID):
    actor = await legacy_guard(request, "legacy:retry_order", ("operator", "admin"))
    if isinstance(actor, JSONResponse):
        return actor
    if actor is None:
        result = await run_in_threadpool(requeue_failed_order, order_id)
    else:
        result = await run_in_threadpool(requeue_failed_order, order_id, requested_by=actor.name)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="Order not found")
    if result == "no_job":
        raise HTTPException(status_code=409, detail="Order has no retryable job")
    if result == "not_dead":
        raise HTTPException(status_code=409, detail="Order is not failed dead")
    return {"order_id": str(order_id), "status": "QUEUED"}


@app.get("/health")
def health():
    return {"status": "ok"}
