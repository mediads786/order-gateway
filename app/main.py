import uuid
from datetime import timezone

from fastapi import FastAPI, Header, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select

from app.db.models import Order
from app.db.session import SessionLocal
from app.services.orders import serialize_order, submit_order
app = FastAPI()


@app.post("/orders")
async def create_order(request: Request, response: Response, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    raw_body = await request.body()
    status_code, body = await run_in_threadpool(submit_order, raw_body, idempotency_key)
    response.status_code = status_code
    return body


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


@app.get("/health")
def health():
    return {"status": "ok"}
