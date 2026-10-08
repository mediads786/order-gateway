from decimal import Decimal
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Approval, ApiKey, AuditEvent, Job, Order, WorkflowEvent
from app.db.session import SessionLocal
from app.governance.keys import hash_api_key
from app.governance.registry import WORKFLOWS
from app.adapters.base import NonRetryableAdapterError


def new_key(role: str) -> str:
    value = "gw_" + uuid.uuid4().hex
    with SessionLocal.begin() as db:
        db.add(ApiKey(name=f"cancel-{role}-{uuid.uuid4()}", role=role, key_hash=hash_api_key(value)))
    return value


def seed_order(status="QUEUED", *, attempts=0, job_status="QUEUED", erp_order_id=None):
    order_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(Order(order_id=order_id, total=Decimal("1.00"), status=status, erp_order_id=erp_order_id))
        db.add(Job(order_id=order_id, status=job_status, attempts=attempts))
    return order_id


def request_cancel(client, key: str, order_id: uuid.UUID, reason="wrong item"):
    return client.post("/workflows/cancel_order/requests", json={
        "input": {"order_id": str(order_id), "reason": reason},
    }, headers={"X-API-Key": key})


def decide(client, approval_id: str, key: str, decision="approve"):
    return client.post(f"/approvals/{approval_id}/decision", json={"decision": decision, "reason": "reviewed"},
                       headers={"X-API-Key": key})


def row_count(model):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model)) or 0


def test_cancel_registry_and_refusal_matrix_create_no_approval(client):
    workflow = WORKFLOWS["cancel_order"]
    assert workflow.executable and workflow.risk == "medium"
    assert workflow.request_roles == ("operator", "admin")
    assert workflow.decision_roles == ("approver", "admin") and workflow.approval == "always"
    key = new_key("operator")
    cases = [
        (uuid.uuid4(), None, 404, "order_not_found"),
        (seed_order("CANCELLED"), "order_not_cancellable", 409, "order_not_cancellable"),
        (seed_order("REJECTED"), "order_not_cancellable", 409, "order_not_cancellable"),
        (seed_order("PENDING_APPROVAL"), "order_pending_approval", 409, "order_pending_approval"),
        (seed_order("APPROVED"), "order_in_progress", 409, "order_in_progress"),
        (seed_order("PROCESSING"), "order_in_progress", 409, "order_in_progress"),
        (seed_order("RETRYING", attempts=1, job_status="QUEUED"), "erp_state_unknown", 409, "erp_state_unknown"),
        (seed_order("FAILED_DEAD", attempts=5, job_status="FAILED"), "erp_state_unknown", 409, "erp_state_unknown"),
    ]
    for order_id, _, status, error in cases:
        before = row_count(Approval)
        response = request_cancel(client, key, order_id)
        assert response.status_code == status and response.json()["error"] == error
        assert row_count(Approval) == before
    invalid = client.post("/workflows/cancel_order/requests", json={"input": {"order_id": "bad", "reason": ""}},
                          headers={"X-API-Key": key})
    assert invalid.status_code == 422


def test_local_cancel_waits_for_approver_and_reject_preserves_rows(client):
    requester, operator, approver = new_key("admin"), new_key("operator"), new_key("approver")
    order_id = seed_order()
    response = request_cancel(client, requester, order_id)
    assert response.status_code == 202
    approval_id = response.json()["approval_id"]
    with SessionLocal() as db:
        approval = db.get(Approval, uuid.UUID(approval_id))
        assert approval.status == "PENDING" and approval.order_id is None
        assert approval.input == {"order_id": str(order_id), "reason": "wrong item"}
        assert db.get(Order, order_id).status == "QUEUED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "QUEUED"
        events = db.scalars(select(WorkflowEvent).where(WorkflowEvent.request_id == approval.request_id)
                            .order_by(WorkflowEvent.event_id)).all()
        assert [event.event_type for event in events] == ["workflow.requested", "workflow.approval_requested"]
    operator_decision = decide(client, approval_id, operator)
    assert operator_decision.status_code == 403 and operator_decision.json()["error"] == "forbidden"
    self_approval = decide(client, approval_id, requester)
    assert self_approval.status_code == 403 and self_approval.json()["error"] == "self_approval_not_allowed"
    rejected = decide(client, approval_id, approver, "reject")
    assert rejected.status_code == 200
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "QUEUED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "QUEUED"
        assert db.get(Approval, uuid.UUID(approval_id)).status == "REJECTED"
        assert db.scalar(select(AuditEvent).where(AuditEvent.order_id == order_id,
                                                AuditEvent.event_type == "order.cancelled")) is None


def test_local_cancel_marks_job_unclaimable_and_retry_refuses(client):
    requester, approver = new_key("operator"), new_key("approver")
    order_id = seed_order()
    pending = request_cancel(client, requester, order_id)
    result = decide(client, pending.json()["approval_id"], approver)
    assert result.status_code == 200 and result.json()["status"] == "EXECUTED"
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CANCELLED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "CANCELLED"
        event = db.scalar(select(AuditEvent).where(AuditEvent.order_id == order_id,
                                                   AuditEvent.event_type == "order.cancelled"))
        assert event.details["mode"] == "local"
        assert event.details["approval_id"] == pending.json()["approval_id"]
        approval_id = pending.json()["approval_id"]
        workflow_rows = db.scalars(select(WorkflowEvent).where(
            WorkflowEvent.detail["approval_id"].as_string() == approval_id,
        )).all()
        assert {row.event_type for row in workflow_rows} >= {"workflow.approved", "workflow.executed"}
    from app.workers.worker import claim_next_job
    with SessionLocal.begin() as db:
        assert claim_next_job(db) is None
    from app.workers.worker import process_one
    assert process_one() is False
    from app.services.retry import requeue_failed_order
    assert requeue_failed_order(order_id) == "not_dead"


def test_erp_cancel_uses_stable_reference_and_marks_order(client, monkeypatch):
    import app.adapters
    import app.governance.approvals as approval_module

    class FakeAdapter:
        def __init__(self): self.calls = []
        def cancel_order(self, reference, erp_order_id, reason):
            self.calls.append((reference, erp_order_id, reason))
            return {"cancel_id": reference, "applied": True}

    adapter = FakeAdapter()
    monkeypatch.setattr(app.adapters, "get_adapter", lambda: adapter)
    monkeypatch.setattr(approval_module, "get_adapter", lambda: adapter)
    order_id = seed_order("CONFIRMED", job_status="DONE", erp_order_id="erp-42")
    requester, approver = new_key("operator"), new_key("approver")
    pending = request_cancel(client, requester, order_id)
    approval_id = pending.json()["approval_id"]
    assert adapter.calls == []
    first = decide(client, approval_id, approver)
    second = decide(client, approval_id, approver)
    assert first.status_code == 200 and second.status_code == 409
    assert adapter.calls == [(f"GW-CANCEL:{approval_id}", "erp-42", "wrong item")]
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CANCELLED"
        assert db.get(Approval, uuid.UUID(approval_id)).status == "EXECUTED"


def test_mock_cancel_endpoint_is_idempotent_faultable_and_resettable(client):
    from fastapi.testclient import TestClient
    from mock_erp.main import app as erp_app

    erp_client = TestClient(erp_app)
    created = erp_client.post("/sales-orders", json={
        "external_id": "cancel-test", "customer": {"name": "A"}, "currency": "USD",
        "lines": [], "total": "1.00",
    })
    erp_id = created.json()["erp_order_id"]
    first = erp_client.post(f"/sales-orders/{erp_id}/cancel", json={"reference": "GW-CANCEL:a", "reason": "x"})
    replay = erp_client.post(f"/sales-orders/{erp_id}/cancel", json={"reference": "GW-CANCEL:a", "reason": "x"})
    assert first.status_code == 200 and first.json()["applied"] is True
    assert replay.json() == {"cancel_id": first.json()["cancel_id"], "applied": False}
    assert erp_client.post("/sales-orders/missing/cancel", json={"reference": "x", "reason": "x"}).status_code == 404
    erp_client.post("/admin/faults", json={"mode": "error_422"})
    assert erp_client.post(f"/sales-orders/{erp_id}/cancel", json={"reference": "y", "reason": "x"}).status_code == 422
    erp_client.post("/admin/reset")
    assert erp_client.get(f"/sales-orders/{erp_id}").status_code == 404


def test_duplicate_open_cancel_is_conflict_and_admin_summary_is_escaped(client, monkeypatch):
    from app.admin.auth import COOKIE_NAME, session_cookie_value

    monkeypatch.setenv("ADMIN_TOKEN", "a-long-test-admin-token")
    order_id = seed_order()
    requester = new_key("operator")
    pending = request_cancel(client, requester, order_id, "<script>alert(1)</script>")
    duplicate = request_cancel(client, requester, order_id)
    assert pending.status_code == 202
    assert duplicate.status_code == 409 and duplicate.json()["error"] == "cancel_already_pending"
    response = client.get("/admin/approvals", cookies={
        COOKIE_NAME: session_cookie_value("a-long-test-admin-token"),
    })
    assert response.status_code == 200
    assert str(order_id) in response.text and "&lt;script&gt;" in response.text
    assert "<script>alert(1)</script>" not in response.text


def test_requester_and_operator_cannot_decide_and_admin_retry_refuses_cancelled(client, monkeypatch):
    from app.governance.registry import can_decide, can_request

    assert not can_request("approver", "cancel_order")
    assert not can_decide("operator", "cancel_order")
    token = "a-long-test-admin-token"
    monkeypatch.setenv("ADMIN_TOKEN", token)
    order_id = seed_order("CANCELLED", job_status="CANCELLED")
    login = client.post("/admin/login", data={"token": token}, follow_redirects=False)
    assert login.status_code == 303
    response = client.post(f"/admin/orders/{order_id}/retry", follow_redirects=False)
    assert response.status_code == 303 and "not_dead" in response.headers["location"]
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CANCELLED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "CANCELLED"
        assert db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued",
        )) is None


def test_cancel_partial_unique_index_exists(client):
    from sqlalchemy import text

    with SessionLocal() as db:
        exists = db.scalar(text("SELECT to_regclass('public.uq_approvals_open_cancel')"))
    assert exists == "uq_approvals_open_cancel"


def test_odoo_cancel_flow_guards_delivery_and_checks_readback(caplog):
    from app.adapters.base import AdapterLine, AdapterOrder, NonRetryableAdapterError
    from app.adapters.odoo import OdooAdapter
    from fakes.fake_odoo import FakeOdoo

    fake = FakeOdoo()
    adapter = OdooAdapter(base_url="http://odoo.test", api_key="secret-test-key",
                          client=httpx.Client(transport=fake.transport()))
    order = AdapterOrder(
        order_id=uuid.uuid4(), job_id=uuid.uuid4(), external_id="cancel-odoo",
        customer={"name": "Ada", "phone": None, "email": "ada@example.com"}, currency="USD",
        lines=[AdapterLine("BOOK", 1, Decimal("2.00"))], total=Decimal("2.00"),
    )
    created = adapter.create_sales_order(order)
    assert adapter.cancel_order("GW-CANCEL:one", created.erp_order_id, "wrong item")["applied"] is True
    assert adapter.cancel_order("GW-CANCEL:two", created.erp_order_id, "wrong item")["applied"] is False

    second = adapter.create_sales_order(AdapterOrder(
        order_id=uuid.uuid4(), job_id=uuid.uuid4(), external_id="cancel-delivered",
        customer=order.customer, currency="USD", lines=order.lines, total=order.total,
    ))
    fake.deliveries.append({"id": 700, "sale_id": int(second.erp_order_id), "state": "done"})
    with pytest.raises(NonRetryableAdapterError, match="already_delivered"):
        adapter.cancel_order("GW-CANCEL:delivered", second.erp_order_id, "wrong item")

    third = adapter.create_sales_order(AdapterOrder(
        order_id=uuid.uuid4(), job_id=uuid.uuid4(), external_id="cancel-no-effect",
        customer=order.customer, currency="USD", lines=order.lines, total=order.total,
    ))
    fake.cancel_without_effect = True
    with pytest.raises(ValueError, match="not cancelled"):
        adapter.cancel_order("GW-CANCEL:no-effect", third.erp_order_id, "wrong item")
    assert "secret-test-key" not in caplog.text


def test_rule_proposer_cancellation_pattern_requires_valid_uuid_and_reason():
    from app.proposals.proposers import AllowedWorkflow, RuleProposer

    workflow = AllowedWorkflow("cancel_order", "cancel", {})
    proposer = RuleProposer()
    order_id = uuid.uuid4()
    proposal = proposer.propose(f"Cancel order {order_id} reason: wrong item", [workflow])
    assert proposal.workflow == "cancel_order"
    assert proposal.input == {"order_id": str(order_id), "reason": "wrong item"}
    assert WORKFLOWS[proposal.workflow].approval == "always"
    assert proposer.propose("cancel order nope reason: x", [workflow]).workflow is None
    assert proposer.propose(f"cancel order {order_id} reason:   ", [workflow]).workflow is None


def test_proposal_confirmation_uses_governed_cancellation_path(client):
    order_id = seed_order()
    key = new_key("operator")
    proposal = client.post("/proposals", json={
        "text": f"cancel order {order_id} reason: wrong item",
    }, headers={"X-API-Key": key})
    assert proposal.status_code == 201
    assert proposal.json()["workflow"] == "cancel_order"
    assert proposal.json()["needs_approval"] == "always"
    confirmed = client.post(
        f"/proposals/{proposal.json()['proposal_id']}/confirm", headers={"X-API-Key": key},
    )
    assert confirmed.status_code == 202
    approval_id = confirmed.json()["approval_id"]
    with SessionLocal() as db:
        approval = db.get(Approval, uuid.UUID(approval_id))
        assert approval.workflow == "cancel_order" and approval.status == "PENDING"
        assert approval.input["order_id"] == str(order_id)


def test_simultaneous_cancel_requests_create_one_open_approval(client):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    order_id = seed_order()
    key = new_key("operator")
    barrier = Barrier(2)

    def submit(_):
        barrier.wait(timeout=10)
        return request_cancel(client, key, order_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2), timeout=30))
    assert sorted(response.status_code for response in responses) == [202, 409]
    assert row_count(Approval) == 1


@pytest.mark.parametrize("error,status", [
    (httpx.ReadTimeout("timeout"), 502),
    (NonRetryableAdapterError("already_delivered", "already_delivered"), 422),
])
def test_erp_cancel_failure_marks_approval_failed_and_preserves_order(client, monkeypatch, error, status):
    import app.adapters
    import app.governance.approvals as approval_module

    class FailingAdapter:
        def cancel_order(self, *args):
            raise error

    adapter = FailingAdapter()
    monkeypatch.setattr(app.adapters, "get_adapter", lambda: adapter)
    monkeypatch.setattr(approval_module, "get_adapter", lambda: adapter)
    order_id = seed_order("CONFIRMED", job_status="DONE", erp_order_id="erp-fail")
    pending = request_cancel(client, new_key("operator"), order_id)
    response = decide(client, pending.json()["approval_id"], new_key("approver"))
    assert response.status_code == status and response.json()["status"] == "EXECUTION_FAILED"
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CONFIRMED"
        assert db.get(Approval, uuid.UUID(pending.json()["approval_id"])).status == "EXECUTION_FAILED"


@pytest.mark.parametrize("new_status,error", [("PROCESSING", "order_in_progress"), ("CANCELLED", "order_not_cancellable")])
def test_state_change_after_request_fails_execution(client, new_status, error):
    order_id = seed_order()
    pending = request_cancel(client, new_key("operator"), order_id)
    with SessionLocal.begin() as db:
        db.get(Order, order_id).status = new_status
    response = decide(client, pending.json()["approval_id"], new_key("approver"))
    assert response.status_code == 409 and response.json()["error"] == error
    assert response.json()["status"] == "EXECUTION_FAILED"
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == new_status
        assert db.get(Approval, uuid.UUID(pending.json()["approval_id"])).status == "EXECUTION_FAILED"
