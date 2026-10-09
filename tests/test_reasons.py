import json
import uuid

from sqlalchemy import select

from app.core.reasons import summarize_reasons
from app.db.models import AuditEvent, Order
from app.db.session import SessionLocal


def test_summarize_reasons_collapses_ninety_extra_fields():
    raw_reasons = [
        {"field": f"extra_{index}", "reason": "Extra inputs are not permitted"}
        for index in range(90)
    ]

    assert summarize_reasons(raw_reasons, {}) == [{
        "field": "(unexpected fields)",
        "reason": "90 fields are not part of the order contract: extra_0, extra_1, extra_2, extra_3, extra_4, and 85 more",
    }]


def test_summarize_reasons_puts_shopify_hint_first_and_limits_other_reasons():
    raw_reasons = [{"field": f"field_{index}", "reason": "Field required"} for index in range(12)]
    body = {"line_items": [], "admin_graphql_api_id": "gid://shopify/Order/1"}

    summarized = summarize_reasons(raw_reasons, body)

    assert summarized[0] == {
        "field": "(payload)",
        "reason": "This looks like a Shopify order. Send it to POST /webhooks/shopify/orders-create, not to POST /orders.",
    }
    assert summarized[1:11] == raw_reasons[:10]
    assert summarized[11] == {"field": "(more)", "reason": "2 further problems not shown"}


def test_shopify_shaped_order_gets_short_stored_rejection_reasons(client):
    payload = {
        "admin_graphql_api_id": "gid://shopify/Order/1",
        "line_items": [],
        "currency": "USD",
        "customer": {"first_name": "Shopify Customer", "email": "shopify@example.com"},
        **{f"shopify_extra_{index}": index for index in range(90)},
    }
    response = client.post(
        "/orders",
        content=json.dumps(payload).encode("utf-8"),
        headers={"Idempotency-Key": "shopify-shaped-order"},
    )

    assert response.status_code == 422
    result = response.json()
    assert result["status"] == "REJECTED"
    assert len(result["reasons"]) <= 12
    assert result["reasons"][0]["field"] == "(payload)"
    with SessionLocal() as db:
        order = db.get(Order, uuid.UUID(result["order_id"]))
        assert order is not None
        assert order.status == "REJECTED"
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order.order_id,
            AuditEvent.event_type == "order.rejected",
        ))
        assert event is not None
        assert event.details["reasons"] == result["reasons"]
