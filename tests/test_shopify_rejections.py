import json
import uuid

from sqlalchemy import func, select

from app.db.models import AuditEvent, IdempotencyKey, Job, Order
from app.db.session import SessionLocal
from test_shopify_webhooks import TEST_SECRET, _fixture_body, _headers


def _body_with(**changes) -> bytes:
    payload = json.loads(_fixture_body())
    payload.update(changes)
    return json.dumps(payload).encode("utf-8")


def _assert_rejected(response, reason: str) -> str:
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "REJECTED"
    order_id = uuid.UUID(result["order_id"])
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        assert order is not None
        assert order.status == "REJECTED"
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order.order_id,
            AuditEvent.event_type == "order.rejected",
        ))
        assert event is not None
        assert event.details == {"reasons": [{"field": "shopify_order", "reason": reason}]}
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 1
    return str(order_id)


def test_missing_customer_name_stores_specific_rejection(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _body_with(
        customer=None,
        email=None,
        phone=None,
        shipping_address={"phone": None},
        billing_address={"phone": None},
    )
    response = client.post(
        "/webhooks/shopify/orders-create",
        content=body,
        headers=_headers(body, webhook_id="no-customer-name"),
    )
    order_id = _assert_rejected(response, "Shopify order has no usable customer name")
    with SessionLocal() as db:
        order = db.get(Order, uuid.UUID(order_id))
        assert order.source is None
        assert order.raw_payload == json.loads(body)
        assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_missing_customer_contact_stores_specific_rejection(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    payload = json.loads(_fixture_body())
    payload["email"] = payload["phone"] = None
    payload["customer"]["email"] = payload["customer"]["phone"] = None
    payload["shipping_address"]["phone"] = None
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/shopify/orders-create",
        content=body,
        headers=_headers(body, webhook_id="no-customer-contact"),
    )
    _assert_rejected(response, "Shopify order has no customer email or phone")


def test_empty_line_item_sku_stores_specific_rejection(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    payload = json.loads(_fixture_body())
    payload["line_items"][0]["sku"] = ""
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/webhooks/shopify/orders-create",
        content=body,
        headers=_headers(body, webhook_id="empty-line-sku"),
    )
    _assert_rejected(response, "Shopify line item SKU is required")


def test_repeated_unmappable_webhook_reuses_rejected_order(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _body_with(
        customer=None,
        email=None,
        phone=None,
        shipping_address={"phone": None},
        billing_address={"phone": None},
    )
    headers = _headers(body, webhook_id="repeat-no-customer-name")
    first = client.post("/webhooks/shopify/orders-create", content=body, headers=headers)
    second = client.post("/webhooks/shopify/orders-create", content=body, headers=headers)
    first_id = _assert_rejected(first, "Shopify order has no usable customer name")
    assert second.status_code == 200
    assert second.json() == {"order_id": first_id, "status": "REJECTED"}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_invalid_json_keeps_existing_rejection_reason(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = b"{invalid json"
    response = client.post(
        "/webhooks/shopify/orders-create",
        content=body,
        headers=_headers(body, webhook_id="invalid-json"),
    )
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "REJECTED"
    with SessionLocal() as db:
        order = db.get(Order, uuid.UUID(result["order_id"]))
        assert order is not None
        assert order.status == "REJECTED"
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order.order_id,
            AuditEvent.event_type == "order.rejected",
        ))
        assert event is not None
        assert event.details == {"reasons": [{"field": "body", "reason": "Invalid JSON"}]}
        assert db.scalar(select(func.count()).select_from(Job)) == 0
