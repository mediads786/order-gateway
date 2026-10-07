from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
from threading import Barrier
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.adapters.base import NonRetryableAdapterError
from app.adapters.odoo import OdooAdapter
from app.admin.auth import COOKIE_NAME, session_cookie_value
from app.db.models import Approval, ApiKey, AuditEvent, IdempotencyKey, Job, Order, WorkflowEvent
from app.db.session import SessionLocal
from app.governance.keys import hash_api_key
from app.workers.worker import process_one
from fakes.fake_odoo import FakeOdoo
from test_governance import OPERATOR_INPUT, create_key, workflow_request


class RecordingAdapter:
    def __init__(self, error: Exception | None = None):
        self.calls = []
        self.error = error

    def adjust_stock(self, reference: str, sku: str, qty_delta: int, reason: str) -> dict:
        self.calls.append((reference, sku, qty_delta, reason))
        if self.error:
            raise self.error
        return {"adjustment_id": reference, "applied": True}


def pending_order(client: TestClient, monkeypatch, *, role: str = "operator", key: str | None = None,
                  reference: str | None = None, total: str = "2500.00"):
    monkeypatch.setenv("APPROVAL_THRESHOLD", "1000.00")
    requester = key or create_key(role)
    order_input = deepcopy(OPERATOR_INPUT)
    order_input["external_ref"] = reference or f"approval-{uuid.uuid4()}"
    order_input["lines"] = [{"sku": "BOOK", "qty": 100, "unit_price": str(Decimal(total) / 100)}]
    response = workflow_request(client, requester, "create_order", order_input, f"idem-{uuid.uuid4()}")
    assert response.status_code == 202
    return requester, response


def decide(client: TestClient, approval_id: str, key: str, decision: str = "approve", reason: str | None = None):
    body = {"decision": decision}
    if reason is not None:
        body["reason"] = reason
    return client.post(f"/approvals/{approval_id}/decision", json=body, headers={"X-API-Key": key})


def approval_row(approval_id: str) -> Approval:
    with SessionLocal() as db:
        row = db.get(Approval, uuid.UUID(approval_id))
        db.expunge(row)
        return row


def count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model)) or 0


def test_threshold_below_equal_and_above_hold_policy(client, monkeypatch):
    for amount, threshold, code, order_status, needs_approval in [
        ("9.99", "10.00", 201, "RECEIVED", False),
        ("10.00", "10.00", 201, "RECEIVED", False),
        ("10.01", "10.00", 202, "PENDING_APPROVAL", True),
    ]:
        monkeypatch.setenv("APPROVAL_THRESHOLD", threshold)
        key = create_key("operator")
        payload = deepcopy(OPERATOR_INPUT)
        payload["external_ref"] = f"threshold-{uuid.uuid4()}"
        payload["lines"] = [{"sku": "BOOK", "qty": 1, "unit_price": amount}]
        response = workflow_request(client, key, "create_order", payload, f"threshold-{uuid.uuid4()}")
        assert response.status_code == code
        order_id = uuid.UUID(response.json().get("result", response.json()).get("order_id"))
        with SessionLocal() as db:
            order = db.get(Order, order_id)
            assert order.status == order_status
            assert (db.scalar(select(Job).where(Job.order_id == order_id)) is not None) is not needs_approval
            assert (db.scalar(select(Approval).where(Approval.order_id == order_id)) is not None) is needs_approval


def test_threshold_default_invalid_and_zero_cases(client, monkeypatch):
    monkeypatch.delenv("APPROVAL_THRESHOLD", raising=False)
    key = create_key("operator")
    normal = workflow_request(client, key, "create_order", OPERATOR_INPUT, "threshold-default")
    assert normal.status_code == 201

    for value in ("abc", "-5"):
        monkeypatch.setenv("APPROVAL_THRESHOLD", value)
        invalid = workflow_request(client, key, "create_order", OPERATOR_INPUT,
                                   f"threshold-invalid-{value}")
        assert invalid.status_code == 503
        assert invalid.json()["error"] == "approval_threshold_invalid"
    assert count(Order) == 1

    monkeypatch.setenv("APPROVAL_THRESHOLD", "0")
    positive = workflow_request(client, key, "create_order", OPERATOR_INPUT, "threshold-zero")
    assert positive.status_code == 202


def test_pending_order_has_no_job_and_worker_does_not_call_adapter(client, monkeypatch):
    _, response = pending_order(client, monkeypatch)
    order_id = uuid.UUID(response.json()["result"]["order_id"])
    recorder = RecordingAdapter()
    assert process_one(recorder) is False
    assert recorder.calls == []
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        assert order.status == "PENDING_APPROVAL"
        assert db.scalar(select(Job).where(Job.order_id == order_id)) is None
        assert [event.event_type for event in order.events] == ["order.received", "order.pending_approval"]


def test_pending_order_replay_returns_202_and_creates_one_approval(client, monkeypatch):
    monkeypatch.setenv("APPROVAL_THRESHOLD", "1")
    key = create_key("operator")
    body = deepcopy(OPERATOR_INPUT)
    body["external_ref"] = "approval-replay"
    first = workflow_request(client, key, "create_order", body, "approval-replay-key")
    second = workflow_request(client, key, "create_order", body, "approval-replay-key")
    assert first.status_code == second.status_code == 202
    assert first.json()["result"]["order_id"] == second.json()["result"]["order_id"]
    assert first.json()["approval_id"] == second.json()["approval_id"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Approval)) == 1
        assert db.scalar(select(IdempotencyKey.status_code).where(IdempotencyKey.key == "approval-replay-key")) == 202


def test_approve_order_records_complete_audit_and_worker_confirms(client, monkeypatch):
    requester, created = pending_order(client, monkeypatch)
    approval_id = created.json()["approval_id"]
    order_id = uuid.UUID(created.json()["result"]["order_id"])
    decider = create_key("approver")
    decision = decide(client, approval_id, decider, reason="Verified")
    assert decision.status_code == 200
    assert decision.headers["X-Request-Id"] == decision.json()["request_id"]
    row = approval_row(approval_id)
    assert row.status == "EXECUTED" and row.decided_by_name
    assert row.decided_at is not None
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        assert order.status == "QUEUED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)) is not None
        assert [event.event_type for event in order.events] == [
            "order.received", "order.pending_approval", "order.approved", "order.queued",
        ]
    assert process_one()
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CONFIRMED"
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id).order_by(AuditEvent.event_id)).all()
        assert [event.event_type for event in events] == [
            "order.received", "order.pending_approval", "order.approved", "order.queued",
            "order.processing", "order.confirmed",
        ]
    with SessionLocal() as db:
        rows = db.scalars(
            select(WorkflowEvent).where(WorkflowEvent.request_id.in_(
                [uuid.UUID(created.json()["request_id"]), uuid.UUID(decision.json()["request_id"])]
            )).order_by(WorkflowEvent.created_at, WorkflowEvent.event_id)
        ).all()
    assert [event.event_type for event in rows] == [
        "workflow.requested", "workflow.approval_requested", "workflow.approved", "workflow.executed",
    ]
    assert rows[0].actor_name and rows[0].actor_name != rows[2].actor_name
    assert rows[2].created_at and rows[2].detail["decider"].startswith("test-approver-")


def test_reject_order_requires_reason_cancels_and_never_queues(client, monkeypatch):
    _, created = pending_order(client, monkeypatch)
    approval_id = created.json()["approval_id"]
    decider = create_key("approver")
    assert decide(client, approval_id, decider, "reject").status_code == 422
    rejected = decide(client, approval_id, decider, "reject", "Duplicate request")
    assert rejected.status_code == 200
    order_id = uuid.UUID(created.json()["result"]["order_id"])
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CANCELLED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)) is None
        assert db.get(Approval, uuid.UUID(approval_id)).status == "REJECTED"
        assert [event.event_type for event in db.scalars(
            select(AuditEvent).where(AuditEvent.order_id == order_id).order_by(AuditEvent.event_id)
        )] == ["order.received", "order.pending_approval", "order.cancelled"]
    assert process_one() is False


def test_self_approval_and_database_check_constraint(client, monkeypatch):
    requester, created = pending_order(client, monkeypatch, role="admin")
    denied = decide(client, created.json()["approval_id"], requester)
    assert denied.status_code == 403
    assert denied.json()["error"] == "self_approval_not_allowed"
    approval_id = uuid.UUID(created.json()["approval_id"])
    with pytest.raises(IntegrityError):
        with SessionLocal.begin() as db:
            db.execute(update(Approval).where(Approval.approval_id == approval_id).values(
                decided_by_key_id=Approval.requested_by_key_id,
            ))
    assert approval_row(str(approval_id)).status == "PENDING"


@pytest.mark.parametrize("role", ["operator", "approver", "admin"])
def test_decision_roles_and_inactive_key(client, monkeypatch, role):
    requester, created = pending_order(client, monkeypatch)
    decider = create_key(role)
    result = decide(client, created.json()["approval_id"], decider)
    if role == "operator":
        assert result.status_code == 403 and result.json()["error"] == "forbidden"
        assert approval_row(created.json()["approval_id"]).status == "PENDING"
    else:
        assert result.status_code == 200
    inactive = create_key("approver", f"inactive-{uuid.uuid4()}")
    with SessionLocal.begin() as db:
        key_hash = hash_api_key(inactive)
        db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash)).active = False
    assert decide(client, created.json()["approval_id"], inactive).status_code == 401


def test_double_decision_and_concurrent_decisions_are_serialized(client, monkeypatch):
    _, created = pending_order(client, monkeypatch)
    approval_id = created.json()["approval_id"]
    keys = [create_key("approver"), create_key("admin")]
    barrier = Barrier(2)

    def approve(key):
        barrier.wait(timeout=10)
        return decide(client, approval_id, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(approve, keys))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert decide(client, approval_id, keys[0]).status_code == 409
    assert decide(client, approval_id, keys[0], "reject", "too late").status_code == 409
    order_id = uuid.UUID(created.json()["result"]["order_id"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.order_id == order_id)) == 1
        workflow = db.scalars(select(WorkflowEvent).where(WorkflowEvent.event_type == "workflow.executed")).all()
        assert len(workflow) == 1


def test_unknown_and_non_uuid_approval_ids_return_404(client):
    key = create_key("approver")
    assert client.get("/approvals/not-a-uuid", headers={"X-API-Key": key}).status_code == 404
    assert client.get(f"/approvals/{uuid.uuid4()}", headers={"X-API-Key": key}).status_code == 404


def test_adjust_stock_request_waits_for_approval_and_stores_input(client, monkeypatch):
    import app.adapters

    recorder = RecordingAdapter()
    monkeypatch.setattr(app.adapters, "get_adapter", lambda: recorder)
    key = create_key("operator")
    input_data = {"sku": "BOOK", "qty_delta": 2, "reason": "Cycle count"}
    response = workflow_request(client, key, "adjust_stock", input_data)
    assert response.status_code == 202
    assert recorder.calls == []
    approval = approval_row(response.json()["approval_id"])
    assert approval.status == "PENDING" and approval.input["sku"] == "BOOK"
    assert approval.requested_by_key_id


@pytest.mark.parametrize("input_data", [
    {"sku": "BOOK", "qty_delta": 0, "reason": "bad"},
    {"sku": "", "qty_delta": 1, "reason": "bad"},
    {"sku": "BOOK", "qty_delta": 1, "reason": "x" * 201},
    {"sku": "BOOK", "qty_delta": 1, "reason": "ok", "extra": True},
])
def test_invalid_adjust_stock_creates_no_approval(client, input_data):
    key = create_key("operator")
    before = count(Approval)
    response = workflow_request(client, key, "adjust_stock", input_data)
    assert response.status_code == 422
    assert count(Approval) == before


def test_adjust_stock_approval_calls_adapter_once_and_replay_is_409(client, monkeypatch):
    import app.adapters
    import app.governance.approvals as approvals

    recorder = RecordingAdapter()
    monkeypatch.setattr(app.adapters, "get_adapter", lambda: recorder)
    monkeypatch.setattr(approvals, "get_adapter", lambda: recorder)
    requester = create_key("operator")
    response = workflow_request(client, requester, "adjust_stock", {"sku": "BOOK", "qty_delta": 3, "reason": "count"})
    approval_id = response.json()["approval_id"]
    approver = create_key("approver")
    completed = decide(client, approval_id, approver, reason="Approved")
    assert completed.status_code == 200
    assert recorder.calls == [(f"GW-ADJ:{approval_id}:BOOK", "BOOK", 3, "count")]
    assert approval_row(approval_id).status == "EXECUTED"
    assert decide(client, approval_id, approver, reason="Again").status_code == 409
    assert len(recorder.calls) == 1


def test_adjust_stock_adapter_call_runs_in_threadpool_once(client, monkeypatch):
    import app.governance.approvals as approvals

    recorder = RecordingAdapter()
    monkeypatch.setattr(approvals, "get_adapter", lambda: recorder)
    original = approvals.run_in_threadpool
    executed_functions = []

    async def counting_threadpool(function, *args, **kwargs):
        if function is approvals._execute_stock_adjustment:
            executed_functions.append(function)
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(approvals, "run_in_threadpool", counting_threadpool)
    requester = create_key("operator")
    request = workflow_request(
        client, requester, "adjust_stock", {"sku": "BOOK", "qty_delta": 2, "reason": "count"},
    )
    result = decide(client, request.json()["approval_id"], create_key("approver"))

    assert result.status_code == 200
    assert len(executed_functions) == 1
    assert len(recorder.calls) == 1


def test_reject_adjust_stock_never_calls_adapter(client, monkeypatch):
    import app.adapters

    recorder = RecordingAdapter()
    monkeypatch.setattr(app.adapters, "get_adapter", lambda: recorder)
    requester = create_key("operator")
    response = workflow_request(client, requester, "adjust_stock", {"sku": "BOOK", "qty_delta": -1, "reason": "correction"})
    rejected = decide(client, response.json()["approval_id"], create_key("admin"), "reject", "Not allowed")
    assert rejected.status_code == 200
    assert recorder.calls == []
    assert approval_row(response.json()["approval_id"]).status == "REJECTED"


@pytest.mark.parametrize("error,status", [
    (httpx.HTTPStatusError("500", request=httpx.Request("POST", "http://erp"),
                           response=httpx.Response(500, request=httpx.Request("POST", "http://erp"))), 502),
    (NonRetryableAdapterError("insufficient_stock", "insufficient_stock"), 422),
])
def test_adjust_stock_execution_failure_is_final_and_audited(client, monkeypatch, error, status):
    import app.adapters
    import app.governance.approvals as approvals

    recorder = RecordingAdapter(error)
    monkeypatch.setattr(app.adapters, "get_adapter", lambda: recorder)
    monkeypatch.setattr(approvals, "get_adapter", lambda: recorder)
    created = workflow_request(client, create_key("operator"), "adjust_stock",
                              {"sku": "BOOK", "qty_delta": -1, "reason": "correction"})
    result = decide(client, created.json()["approval_id"], create_key("approver"))
    assert result.status_code == status
    assert approval_row(created.json()["approval_id"]).status == "EXECUTION_FAILED"
    assert len(recorder.calls) == 1
    with SessionLocal() as db:
        event = db.scalar(select(WorkflowEvent).where(
            WorkflowEvent.request_id == uuid.UUID(result.json()["request_id"]),
            WorkflowEvent.event_type == "workflow.failed",
        ))
        assert event and event.detail["error"]


def test_unsupported_adapter_rejects_stock_request_without_row(client, monkeypatch):
    import app.adapters

    monkeypatch.setattr(app.adapters, "get_adapter", lambda: object())
    before = count(Approval)
    response = workflow_request(client, create_key("operator"), "adjust_stock",
                                {"sku": "BOOK", "qty_delta": 1, "reason": "test"})
    assert response.status_code == 501 and response.json()["error"] == "adapter_not_supported"
    assert count(Approval) == before


def test_approval_visibility_and_registry_metadata(client, monkeypatch):
    requester, created = pending_order(client, monkeypatch)
    operator = client.get("/approvals", headers={"X-API-Key": requester})
    other = create_key("operator")
    other_list = client.get("/approvals", headers={"X-API-Key": other})
    approver = create_key("approver")
    approver_list = client.get("/approvals", headers={"X-API-Key": approver})
    assert len(operator.json()) == 1 and other_list.json() == []
    assert len(approver_list.json()) == 1
    assert client.get(f"/approvals/{created.json()['approval_id']}", headers={"X-API-Key": other}).status_code == 404
    assert client.get("/approvals").status_code == 401
    serialized = str(approver_list.json())
    assert "customer" not in serialized and "email" not in serialized and "key_hash" not in serialized
    registry = client.get("/workflows", headers={"X-API-Key": requester}).json()
    by_name = {item["name"]: item for item in registry}
    assert by_name["create_order"]["approval"] == "conditional"
    assert by_name["adjust_stock"]["approval"] == "always" and by_name["adjust_stock"]["executable"]
    assert by_name["cancel_order"]["approval"] == "always" and not by_name["cancel_order"]["executable"]
    cancel = workflow_request(client, requester, "cancel_order", {"order_id": str(uuid.uuid4()), "reason": "x"})
    assert cancel.status_code == 501


def test_admin_approvals_page_is_read_only_filtered_and_escaped(client, monkeypatch):
    from app.admin.auth import COOKIE_NAME

    monkeypatch.setenv("APPROVAL_THRESHOLD", "1")
    actor = "<script>alert(1)</script>"
    requester = create_key("operator", actor)
    created = workflow_request(
        client, requester, "create_order", {**OPERATOR_INPUT, "external_ref": f"admin-{uuid.uuid4()}"},
        f"admin-approval-{uuid.uuid4()}",
    )
    assert created.status_code == 202
    monkeypatch.setenv("ADMIN_TOKEN", "approval-admin-token-012345")
    before = count(Approval)
    assert client.get("/admin/approvals", follow_redirects=False).status_code == 303
    client.cookies.set(COOKIE_NAME, session_cookie_value())
    page = client.get("/admin/approvals")
    assert page.status_code == 200
    assert "Approvals" in page.text and "PENDING" in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<script>alert(1)</script>" not in page.text
    assert "<form" not in page.text and "<button" not in page.text
    assert "/admin/approvals" in client.get("/admin/orders").text
    assert "PENDING_APPROVAL" in client.get("/admin/orders").text
    order_id = created.json()["result"]["order_id"]
    detail = client.get(f"/admin/orders/{order_id}")
    assert "Waiting for approval" in detail.text and "Retry" not in detail.text
    assert "PENDING" in client.get("/admin/approvals?status=PENDING").text
    assert "PENDING" in client.get("/admin/approvals?status=unknown").text
    assert count(Approval) == before


def test_workflow_carryover_threadpool_truncation_and_missing_key_request_id(client, monkeypatch):
    import app.governance.routes as routes

    original = routes.run_in_threadpool
    calls = []

    async def tracked(function, *args, **kwargs):
        calls.append(function.__name__)
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(routes, "run_in_threadpool", tracked)
    key = create_key("operator")
    missing = client.post("/workflows/create_order/requests", json={"input": OPERATOR_INPUT},
                          headers={"X-API-Key": key})
    assert missing.status_code == 400
    assert missing.json()["request_id"] == missing.headers["x-request-id"]
    long_name = "x" * 300
    response = client.post(f"/workflows/{long_name}/requests", json={"input": {"x": 1}},
                           headers={"X-API-Key": key})
    assert response.status_code == 404
    with SessionLocal() as db:
        row = db.scalar(select(WorkflowEvent).where(WorkflowEvent.request_id == uuid.UUID(response.json()["request_id"])))
        assert len(row.workflow) == 100
    assert "authenticate_api_key" in calls and "_record_for_actor" in calls


def test_mock_erp_adjustment_reference_is_idempotent_and_reset_clears():
    url = "http://127.0.0.1:9001"
    reference = f"GW-ADJ:test:{uuid.uuid4()}"
    payload = {"reference": reference, "sku": "BOOK", "qty_delta": 2, "reason": "test"}
    first = httpx.post(f"{url}/stock-adjustments", json=payload, timeout=3)
    second = httpx.post(f"{url}/stock-adjustments", json=payload, timeout=3)
    assert first.status_code == second.status_code == 200
    assert first.json()["applied"] is True and second.json()["applied"] is False
    httpx.post(f"{url}/admin/reset", timeout=3).raise_for_status()
    again = httpx.post(f"{url}/stock-adjustments", json=payload, timeout=3)
    assert again.status_code == 200 and again.json()["applied"] is True


def test_odoo_adjustment_marker_is_checked_before_apply_and_insufficient_is_read_only():
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    adapter = OdooAdapter("http://odoo.test", "test-key", client=httpx.Client(transport=fake.transport()))
    marker = f"GW-ADJ:{uuid.uuid4()}:BOOK"
    result = adapter.adjust_stock(marker, "BOOK", -2, "shipment correction")
    assert result == {"adjustment_id": marker, "applied": True}
    assert fake.quants[(1, 5)]["quantity"] == 3
    duplicate = adapter.adjust_stock(marker, "BOOK", -2, "shipment correction")
    assert duplicate["applied"] is False and fake.quants[(1, 5)]["quantity"] == 3

    insufficient_marker = f"GW-ADJ:{uuid.uuid4()}:BOOK"
    prior_calls = len(fake.calls)
    with pytest.raises(NonRetryableAdapterError, match="insufficient_stock"):
        adapter.adjust_stock(insufficient_marker, "BOOK", -4, "too much")
    changes = fake.calls[prior_calls:]
    assert not any(method in {"write", "create", "action_apply_inventory"} for _, method, _ in changes)
    assert fake.quants[(1, 5)]["quantity"] == 3
