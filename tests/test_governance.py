from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from fastapi.testclient import TestClient

from app.admin.auth import session_cookie_value
from app.db.models import ApiKey, Job, Order, Shipment, WorkflowEvent
from app.db.session import SessionLocal
from app.governance.keys import hash_api_key
from app.workers.worker import process_one

OPERATOR_INPUT = {
    "source": "manual",
    "external_ref": "governance-test",
    "customer": {"name": "Ada", "email": "ada@example.com"},
    "currency": "USD",
    "lines": [{"sku": "BOOK", "qty": 2, "unit_price": "12.50"}],
}


@pytest.fixture(autouse=True)
def reset_governance_tables(clear_database):
    with SessionLocal.begin() as db:
        db.execute(text("TRUNCATE workflow_events, api_keys RESTART IDENTITY CASCADE"))


def create_key(role: str, name: str | None = None) -> str:
    plain_key = "gw_" + uuid.uuid4().hex
    row = ApiKey(name=name or f"test-{role}-{uuid.uuid4()}", role=role, key_hash=hash_api_key(plain_key))
    with SessionLocal.begin() as db:
        db.add(row)
    return plain_key


def workflow_request(client: TestClient, key: str, workflow: str, input_data: dict,
                     idempotency_key: str | None = None, **headers):
    request_headers = {"X-API-Key": key, **headers}
    if idempotency_key is not None:
        request_headers["Idempotency-Key"] = idempotency_key
    return client.post(f"/workflows/{workflow}/requests", json={"input": input_data}, headers=request_headers)


def events_for_request(request_id: str) -> list[WorkflowEvent]:
    with SessionLocal() as db:
        return db.scalars(
            select(WorkflowEvent).where(WorkflowEvent.request_id == uuid.UUID(request_id))
            .order_by(WorkflowEvent.created_at, WorkflowEvent.event_id)
        ).all()


def table_count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model)) or 0


def test_unauthenticated_workflow_routes_do_not_write_events(client):
    active = create_key("operator")
    inactive = create_key("admin", "inactive-test-key")
    with SessionLocal.begin() as db:
        db.scalar(select(ApiKey).where(ApiKey.name == "inactive-test-key")).active = False
    for headers in ({}, {"X-API-Key": "gw_unknown"}, {"X-API-Key": inactive}):
        assert client.get("/workflows", headers=headers).status_code == 401
        assert client.post("/workflows/create_order/requests", json={"input": OPERATOR_INPUT}, headers=headers).status_code == 401
    assert client.get("/workflows", headers={"X-API-Key": active}).status_code == 200
    assert table_count(WorkflowEvent) == 0


@pytest.mark.parametrize("role", ["operator", "approver", "admin"])
@pytest.mark.parametrize("workflow", ["create_order", "adjust_stock", "cancel_order"])
def test_permission_matrix(client, role, workflow):
    key = create_key(role)
    inputs = {
        "create_order": OPERATOR_INPUT,
        "adjust_stock": {"order_id": None, "sku": "BOOK", "qty_delta": 1, "reason": "cycle count"},
        "cancel_order": {"order_id": str(uuid.uuid4()), "reason": "duplicate"},
    }
    response = workflow_request(client, key, workflow, inputs[workflow], "permission-matrix")
    if role == "approver":
        assert response.status_code == 403 and response.json()["error"] == "forbidden"
        event = events_for_request(response.json()["request_id"])[0]
        assert event.event_type == "workflow.denied"
        assert event.actor_name and event.role == "approver" and event.workflow == workflow
        assert event.input_hash and event.detail == {"reason": "forbidden"}
        assert table_count(Order) == table_count(Job) == table_count(Shipment) == 0
        assert httpx.get("http://127.0.0.1:9001/sales-orders", timeout=3).json() == []
    elif workflow == "create_order":
        assert response.status_code == 201
    else:
        assert response.status_code == 501


def test_role_is_from_database_not_headers_or_body(client):
    key = create_key("operator")
    response = client.post(
        "/workflows/create_order/requests",
        json={"input": OPERATOR_INPUT, "role": "admin"},
        headers={"X-API-Key": key, "X-Role": "admin", "Idempotency-Key": "role-from-db"},
    )
    assert response.status_code == 201
    request_events = events_for_request(response.json()["request_id"])
    assert all(event.role == "operator" for event in request_events)


def test_unknown_workflow_is_denied_and_audited(client):
    key = create_key("operator")
    response = workflow_request(client, key, "missing_workflow", {"x": 1})
    assert response.status_code == 404
    assert response.json()["error"] == "unknown_workflow"
    event = events_for_request(response.json()["request_id"])[0]
    assert event.event_type == "workflow.denied" and event.detail == {"reason": "unknown_workflow"}
    assert event.http_status == 404 and event.input_hash


def test_governed_create_order_audits_and_worker_confirms(client):
    key = create_key("operator")
    response = workflow_request(client, key, "create_order", OPERATOR_INPUT, "governed-success")
    assert response.status_code == 201
    assert response.headers["X-Request-Id"] == response.json()["request_id"]
    assert response.json()["workflow"] == "create_order"
    order_id = uuid.UUID(response.json()["result"]["order_id"])
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "RECEIVED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "QUEUED"
    events = events_for_request(response.json()["request_id"])
    assert [event.event_type for event in events] == ["workflow.requested", "workflow.executed"]
    assert len({event.request_id for event in events}) == 1
    assert all(event.actor_name and event.role == "operator" for event in events)
    assert events[1].order_id == order_id and events[1].http_status == 201
    assert process_one()
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CONFIRMED"


def test_governed_idempotency_replays_and_conflicts(client):
    key = create_key("operator")
    first = workflow_request(client, key, "create_order", OPERATOR_INPUT, "governed-idem")
    second = workflow_request(client, key, "create_order", OPERATOR_INPUT, "governed-idem")
    assert first.status_code == 201 and second.status_code == 200
    first_order = first.json()["result"]["order_id"]
    assert second.json()["result"]["order_id"] == first_order
    assert first.json()["request_id"] != second.json()["request_id"]
    assert len(events_for_request(first.json()["request_id"])) == 2
    assert len(events_for_request(second.json()["request_id"])) == 2
    changed = {**OPERATOR_INPUT, "external_ref": "changed"}
    conflict = workflow_request(client, key, "create_order", changed, "governed-idem")
    assert conflict.status_code == 409
    assert table_count(Order) == 1
    conflict_events = events_for_request(conflict.json()["request_id"])
    assert [event.event_type for event in conflict_events] == ["workflow.requested", "workflow.rejected"]


def test_missing_idempotency_key_is_rejected_and_audited(client):
    key = create_key("operator")
    response = workflow_request(client, key, "create_order", OPERATOR_INPUT)
    assert response.status_code == 400
    assert response.json() == {"detail": "Idempotency-Key header is required"}
    assert table_count(Order) == 0
    events = events_for_request(response.headers["X-Request-Id"])
    assert [event.event_type for event in events] == ["workflow.requested", "workflow.rejected"]


@pytest.mark.parametrize("field,value", [
    ("currency", "US"), ("qty", 0), ("lines", []),
])
def test_invalid_create_order_input_is_rejected_by_intake(client, field, value):
    key = create_key("operator")
    invalid = json.loads(json.dumps(OPERATOR_INPUT))
    if field == "qty":
        invalid["lines"][0][field] = value
    else:
        invalid[field] = value
    response = workflow_request(client, key, "create_order", invalid, f"invalid-{field}")
    assert response.status_code == 422
    assert response.json()["result"]["status"] == "REJECTED"
    order_id = uuid.UUID(response.json()["result"]["order_id"])
    events = events_for_request(response.json()["request_id"])
    assert events[-1].event_type == "workflow.rejected" and events[-1].order_id == order_id
    assert events[-1].detail["error"]
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "REJECTED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)) is None


@pytest.mark.parametrize("role", ["operator", "admin"])
@pytest.mark.parametrize("workflow", ["adjust_stock", "cancel_order"])
def test_registered_non_executable_workflows_do_not_change_domain_rows(client, role, workflow):
    key = create_key(role)
    input_data = (
        {"order_id": None, "sku": "BOOK", "qty_delta": 1, "reason": "cycle count"}
        if workflow == "adjust_stock" else {"order_id": str(uuid.uuid4()), "reason": "duplicate"}
    )
    before = (table_count(Order), table_count(Job), table_count(Shipment))
    response = workflow_request(client, key, workflow, input_data)
    assert response.status_code == 501
    assert response.json()["error"] == "workflow_not_executable"
    events = events_for_request(response.json()["request_id"])
    assert [event.event_type for event in events] == ["workflow.requested", "workflow.not_executable"]
    assert events[-1].http_status == 501
    assert (table_count(Order), table_count(Job), table_count(Shipment)) == before


def test_registry_lists_three_schemas_and_accepts_approver(client):
    key = create_key("approver")
    workflows = client.get("/workflows", headers={"X-API-Key": key})
    assert workflows.status_code == 200
    assert {item["name"] for item in workflows.json()} == {"create_order", "adjust_stock", "cancel_order"}
    mapping = {item["name"]: item for item in workflows.json()}
    assert mapping["create_order"]["risk"] == "low" and mapping["create_order"]["executable"] is True
    assert mapping["adjust_stock"]["risk"] == "high" and mapping["adjust_stock"]["executable"] is False
    assert mapping["cancel_order"]["risk"] == "medium" and mapping["cancel_order"]["executable"] is False
    assert mapping["create_order"]["input_schema"]["properties"]["currency"]
    assert "api_key" not in json.dumps(workflows.json()).lower()


def test_api_key_cli_stores_only_hash_lists_safely_and_deactivates(client, capsys):
    from scripts.api_keys import main

    assert main(["create", "--name", "cli-operator", "--role", "operator"]) == 0
    create_output = capsys.readouterr().out
    plain_key = create_output.splitlines()[-1]
    assert plain_key.startswith("gw_")
    with SessionLocal() as db:
        row = db.scalar(select(ApiKey).where(ApiKey.name == "cli-operator"))
        assert row.key_hash == hash_api_key(plain_key) and plain_key not in row.key_hash
    with pytest.raises(SystemExit):
        main(["create", "--name", "bad-role", "--role", "root"])
    assert main(["create", "--name", "cli-operator", "--role", "operator"]) == 1
    assert main(["list"]) == 0
    list_output = capsys.readouterr().out
    assert "cli-operator" in list_output and plain_key not in list_output and row.key_hash not in list_output
    assert main(["deactivate", "--name", "cli-operator"]) == 0
    assert client.get("/workflows", headers={"X-API-Key": plain_key}).status_code == 401


def test_workflow_events_reject_update_delete_but_allow_insert():
    key_id = uuid.uuid4()
    event_id = uuid.uuid4()
    request_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(ApiKey(key_id=key_id, name="append-only-key", role="operator", key_hash="f" * 64))
        db.add(WorkflowEvent(event_id=event_id, request_id=request_id, key_id=key_id,
                             actor_name="append-only-key", role="operator", workflow="create_order",
                             event_type="workflow.requested", input_hash="a" * 64, detail={}))
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as db:
            db.execute(text("UPDATE workflow_events SET workflow='changed' WHERE event_id=:id"), {"id": event_id})
    with pytest.raises(DBAPIError):
        with SessionLocal.begin() as db:
            db.execute(text("DELETE FROM workflow_events WHERE event_id=:id"), {"id": event_id})
    with SessionLocal() as db:
        assert db.get(WorkflowEvent, event_id).workflow == "create_order"
        assert db.scalar(select(func.count()).select_from(WorkflowEvent)) == 1


def test_two_governed_requests_with_same_key_create_one_order(client):
    key = create_key("operator")
    barrier = Barrier(2)

    def submit_together(_):
        barrier.wait(timeout=10)
        return workflow_request(client, key, "create_order", OPERATOR_INPUT, "governed-race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit_together, range(2)))
    assert sorted(response.status_code for response in responses) == [200, 201]
    assert len({response.json()["result"]["order_id"] for response in responses}) == 1
    assert table_count(Order) == 1
    all_events = [event for response in responses for event in events_for_request(response.json()["request_id"])]
    assert len(all_events) == 4 and len({event.request_id for event in all_events}) == 2


def test_submit_order_exception_leaves_requested_and_records_failed(client, monkeypatch):
    import app.governance.routes as routes

    key = create_key("operator")

    def fail_submit(*args):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(routes, "submit_order", fail_submit)
    response = workflow_request(client, key, "create_order", OPERATOR_INPUT, "governed-failure")
    assert response.status_code == 500 and response.json()["error"] == "internal_error"
    events = events_for_request(response.json()["request_id"])
    assert [event.event_type for event in events] == ["workflow.requested", "workflow.failed"]
    assert events[-1].http_status == 500


def test_admin_workflow_events_auth_filter_escape_and_read_only(client, monkeypatch):
    from app.admin.auth import COOKIE_NAME

    monkeypatch.setenv("ADMIN_TOKEN", "governance-admin-token-012345")
    actor = "<script>alert(1)</script>"
    key_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(ApiKey(key_id=key_id, name=actor, role="operator", key_hash="b" * 64))
        db.add_all([
            WorkflowEvent(request_id=uuid.uuid4(), key_id=key_id, actor_name=actor, role="operator",
                          workflow="create_order", event_type="workflow.executed", http_status=201,
                          input_hash="c" * 64, detail={"text": actor}, created_at=datetime.now(timezone.utc)),
            WorkflowEvent(request_id=uuid.uuid4(), key_id=key_id, actor_name="safe-actor", role="approver",
                          workflow="create_order", event_type="workflow.denied", http_status=403,
                          input_hash="d" * 64, detail={"text": actor},
                          created_at=datetime.now(timezone.utc) + timedelta(seconds=1)),
        ])
    assert client.get("/admin/workflow-events", follow_redirects=False).status_code == 303
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(WorkflowEvent))
    client.cookies.set(COOKIE_NAME, session_cookie_value())
    response = client.get("/admin/workflow-events")
    assert response.status_code == 200
    assert response.text.index("safe-actor") < response.text.index("operator")
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert actor not in response.text
    assert "/admin/workflow-events" in response.text
    filtered = client.get("/admin/workflow-events?type=workflow.denied")
    assert "safe-actor" in filtered.text and actor not in filtered.text
    ignored = client.get("/admin/workflow-events?type=unknown")
    assert "safe-actor" in ignored.text and "workflow.executed" in ignored.text
    assert table_count(WorkflowEvent) == before
