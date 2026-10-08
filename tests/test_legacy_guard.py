import hashlib
import uuid

from sqlalchemy import func, select

from app.db.models import ApiKey, AuditEvent, IdempotencyKey, Job, Order, OrderLine, WorkflowEvent
from app.db.session import SessionLocal
from app.governance.keys import hash_api_key
from test_governance import OPERATOR_INPUT, create_key


def counts() -> tuple[int, ...]:
    with SessionLocal() as db:
        return tuple(db.scalar(select(func.count()).select_from(model)) or 0 for model in (
            Order, OrderLine, IdempotencyKey, AuditEvent, Job, WorkflowEvent,
        ))


def test_legacy_auth_off_keeps_original_order_routes_open(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "off")
    created = client.post("/orders", json=OPERATOR_INPUT, headers={"Idempotency-Key": "legacy-open"})
    assert created.status_code == 201
    fetched = client.get(f"/orders/{created.json()['order_id']}")
    assert fetched.status_code == 200 and fetched.json()["order_id"] == created.json()["order_id"]


def test_legacy_create_rejects_missing_unknown_and_deactivated_keys_without_writes(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "key")
    deactivated = create_key("operator", "legacy-deactivated")
    with SessionLocal.begin() as db:
        db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(deactivated))).active = False
    before = counts()
    requests = (
        client.post("/orders", json=OPERATOR_INPUT, headers={"Idempotency-Key": "legacy-no-key"}),
        client.post("/orders", json=OPERATOR_INPUT,
                    headers={"X-API-Key": "gw_unknown", "Idempotency-Key": "legacy-unknown"}),
        client.post("/orders", json=OPERATOR_INPUT,
                    headers={"X-API-Key": deactivated, "Idempotency-Key": "legacy-deactivated"}),
    )
    assert [response.status_code for response in requests] == [401, 401, 401]
    assert all(response.json() == {"error": "unauthorized"} for response in requests)
    assert counts() == before


def test_legacy_create_role_denial_writes_one_attributed_workflow_event(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "key")
    approver = create_key("approver", "legacy-approver")
    with SessionLocal() as db:
        actor = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(approver)))
        actor_id, actor_name = actor.key_id, actor.name
    raw_body = b'{"input":{"source":"manual"}}'
    before = counts()
    response = client.post("/orders", content=raw_body, headers={
        "X-API-Key": approver, "Idempotency-Key": "legacy-forbidden",
        "Content-Type": "application/json",
    })
    assert response.status_code == 403 and response.json() == {"error": "forbidden"}
    after = counts()
    assert after[:5] == before[:5]
    assert after[5] == before[5] + 1
    with SessionLocal() as db:
        event = db.scalar(select(WorkflowEvent).where(WorkflowEvent.workflow == "legacy:create_order"))
        assert event.event_type == "workflow.denied"
        assert event.http_status == 403 and event.key_id == actor_id and event.actor_name == actor_name
        assert event.role == "approver" and event.input_hash == hashlib.sha256(raw_body).hexdigest()
        assert event.detail == {"reason": "forbidden"}


def test_legacy_operator_create_attributes_received_event_and_replay_is_idempotent(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "key")
    operator = create_key("operator", "legacy-operator")
    with SessionLocal() as db:
        name = db.scalar(select(ApiKey.name).where(ApiKey.key_hash == hash_api_key(operator)))
    headers = {"X-API-Key": operator, "Idempotency-Key": "legacy-attributed"}
    first = client.post("/orders", json=OPERATOR_INPUT, headers=headers)
    before_replay = counts()
    replay = client.post("/orders", json=OPERATOR_INPUT, headers=headers)
    assert first.status_code == 201 and replay.status_code == 200
    assert replay.json() == first.json()
    assert counts() == before_replay
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == uuid.UUID(first.json()["order_id"]),
            AuditEvent.event_type == "order.received",
        ))
        assert event.details == {"requested_by": name}


def test_legacy_get_requires_key_and_all_three_roles_can_read(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "key")
    operator = create_key("operator", "legacy-read-operator")
    order = client.post("/orders", json=OPERATOR_INPUT,
                        headers={"X-API-Key": operator, "Idempotency-Key": "legacy-read-order"})
    order_id = order.json()["order_id"]
    assert client.get(f"/orders/{order_id}").status_code == 401
    for role in ("operator", "approver", "admin"):
        key = operator if role == "operator" else create_key(role, f"legacy-read-{role}")
        response = client.get(f"/orders/{order_id}", headers={"X-API-Key": key})
        assert response.status_code == 200 and response.json()["order_id"] == order_id


def test_legacy_retry_requires_key_role_and_attributes_requeue(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "key")
    operator = create_key("operator", "legacy-retry-operator")
    approver = create_key("approver", "legacy-retry-approver")
    order = client.post("/orders", json=OPERATOR_INPUT,
                        headers={"X-API-Key": operator, "Idempotency-Key": "legacy-retry-order"})
    order_id = uuid.UUID(order.json()["order_id"])
    with SessionLocal.begin() as db:
        db.get(Order, order_id).status = "FAILED_DEAD"
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        job.status = "FAILED"
        job.attempts = 1
    before = counts()
    assert client.post(f"/orders/{order_id}/retry").status_code == 401
    forbidden = client.post(f"/orders/{order_id}/retry", headers={"X-API-Key": approver})
    assert forbidden.status_code == 403 and forbidden.json() == {"error": "forbidden"}
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "FAILED_DEAD"
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "FAILED"
    response = client.post(f"/orders/{order_id}/retry", headers={"X-API-Key": operator})
    assert response.status_code == 200
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued",
        ))
        assert event.details == {"previous_attempts": 1, "requested_by": "legacy-retry-operator"}
    assert counts()[0] == before[0] and counts()[1] == before[1]


def test_legacy_mode_does_not_guard_signed_shopify_or_health(client, monkeypatch):
    from test_shopify_webhooks import TEST_SECRET, _fixture_body, _headers

    monkeypatch.setenv("LEGACY_AUTH", "key")
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    response = client.post("/webhooks/shopify/orders-create", content=body, headers=_headers(body))
    assert response.status_code == 200
    assert client.get("/health").status_code == 200


def test_unrecognized_legacy_auth_value_fails_closed(client, monkeypatch):
    monkeypatch.setenv("LEGACY_AUTH", "on")
    response = client.post("/orders", json=OPERATOR_INPUT,
                           headers={"Idempotency-Key": "legacy-fail-closed"})
    assert response.status_code == 401 and response.json() == {"error": "unauthorized"}
