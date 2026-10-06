import json
import logging
import os
import uuid
from datetime import timezone

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, select

from app.db.models import AuditEvent, Job, Order
from app.db.session import SessionLocal
from app.services.orders import serialize_order, submit_order
from app.services.shopify_webhooks import UnmappableShopifyOrder, map_shopify_order, verify_signature
app = FastAPI()
logger = logging.getLogger(__name__)


@app.post("/orders")
async def create_order(request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    raw_body = await request.body()
    status_code, body = await run_in_threadpool(submit_order, raw_body, idempotency_key)
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

    try:
        payload = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        canonical_bytes = raw_body
    else:
        try:
            canonical = map_shopify_order(payload)
        except UnmappableShopifyOrder:
            canonical_bytes = raw_body
        else:
            canonical_bytes = json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")

    status_code, body = await run_in_threadpool(
        submit_order,
        canonical_bytes,
        f"shopify:{webhook_id}",
    )
    if status_code in (200, 201, 422):
        return JSONResponse(
            status_code=200,
            content={"order_id": body["order_id"], "status": body["status"]},
        )
    return JSONResponse(status_code=status_code, content=body)


@app.get("/orders/{order_id}")
def get_order(order_id: uuid.UUID):
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
def retry_order(order_id: uuid.UUID):
    with SessionLocal.begin() as db:
        order = db.get(Order, order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        job = db.scalar(select(Job).where(Job.order_id == order_id).with_for_update())
        if job is None:
            raise HTTPException(status_code=409, detail="Order has no retryable job")
        db.refresh(order)
        if order.status != "FAILED_DEAD" or job.status != "FAILED":
            raise HTTPException(status_code=409, detail="Order is not failed dead")
        previous_attempts = job.attempts
        job.status = "QUEUED"
        job.attempts = 0
        job.next_attempt_at = func.now()
        job.locked_at = None
        job.updated_at = func.now()
        order.status = "QUEUED"
        db.add(AuditEvent(order_id=order_id, event_type="order.requeued", details={"previous_attempts": previous_attempts}))
    return {"order_id": str(order_id), "status": "QUEUED"}


@app.get("/health")
def health():
    return {"status": "ok"}
