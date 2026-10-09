from decimal import Decimal
import uuid

import httpx
from sqlalchemy import select

from app.db.models import ApiKey, Approval, Job, Order, WorkflowEvent
from app.db.session import SessionLocal
from app.governance.keys import hash_api_key


def _key(role: str) -> str:
    value = "gw_" + uuid.uuid4().hex
    with SessionLocal.begin() as db:
        db.add(ApiKey(
            name=f"hardening-{role}-{uuid.uuid4()}", role=role, key_hash=hash_api_key(value),
        ))
    return value


def _request_cancel(client, key: str, order_id: uuid.UUID):
    return client.post(
        "/workflows/cancel_order/requests",
        json={"input": {"order_id": str(order_id), "reason": "hardening test"}},
        headers={"X-API-Key": key},
    )


def test_cancel_approved_event_has_order_id(client):
    requester, approver = _key("operator"), _key("approver")
    order_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(Order(order_id=order_id, total=Decimal("1.00"), status="QUEUED"))
        db.add(Job(order_id=order_id, status="QUEUED", attempts=0))

    pending = _request_cancel(client, requester, order_id)
    assert pending.status_code == 202
    approval_id = pending.json()["approval_id"]
    decided = client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve", "reason": "approved"},
        headers={"X-API-Key": approver},
    )
    assert decided.status_code == 200

    with SessionLocal() as db:
        approval = db.get(Approval, uuid.UUID(approval_id))
        assert approval.order_id is None
        event = db.scalar(select(WorkflowEvent).where(
            WorkflowEvent.event_type == "workflow.approved",
            WorkflowEvent.detail["approval_id"].as_string() == approval_id,
        ))
        assert event is not None
        assert event.order_id == order_id


def test_duplicate_open_cancel_still_409(client):
    requester = _key("operator")
    order_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(Order(order_id=order_id, total=Decimal("1.00"), status="QUEUED"))
        db.add(Job(order_id=order_id, status="QUEUED", attempts=0))

    first = _request_cancel(client, requester, order_id)
    second = _request_cancel(client, requester, order_id)
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"] == "cancel_already_pending"


def test_mock_erp_rejects_zero_delta():
    response = httpx.post("http://127.0.0.1:9001/stock-adjustments", json={
        "reference": f"GW-ADJ:zero:{uuid.uuid4()}",
        "sku": "BOOK",
        "qty_delta": 0,
        "reason": "zero-delta validation",
    }, timeout=3)
    assert response.status_code == 422
