import json
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.adapters.odoo import OdooAdapter
from app.db.models import AuditEvent, Job, Order
from app.db.session import SessionLocal
from app.services.orders import submit_order
from app.workers.worker import process_one
from fakes.fake_odoo import FakeOdoo


def submit(key="odoo-test", sku="BOOK", currency="USD"):
    payload = {
        "source": "web", "customer": {"name": "Ada", "email": "ada@example.com"},
        "currency": currency,
        "lines": [{"sku": sku, "qty": 1, "unit_price": "19.99"}],
    }
    status, body = submit_order(json.dumps(payload).encode(), key)
    assert status == 201
    return uuid.UUID(body["order_id"])


def build_adapter(fake):
    client = httpx.Client(transport=fake.transport())
    return OdooAdapter("http://odoo.test", "key", client=client)


def make_due(order_id):
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        job.next_attempt_at = func.now()


def order_and_job(order_id):
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id).order_by(AuditEvent.event_id)).all()
        return order.status, order.erp_order_id, job.status, job.attempts, [event.event_type for event in events], events


def test_submitted_order_reaches_confirmed_with_odoo_id():
    order_id = submit()
    fake = FakeOdoo()
    assert process_one(build_adapter(fake))
    status, erp_id, job_status, attempts, events, _ = order_and_job(order_id)
    assert status == "CONFIRMED" and erp_id
    assert job_status == "DONE" and attempts == 1
    assert events == ["order.received", "order.queued", "order.processing", "order.confirmed"]
    assert len(fake.orders) == 1


def test_timeout_after_odoo_create_retries_and_reuses_the_same_order():
    order_id = submit("create-timeout")
    fake = FakeOdoo()
    fake.timeout_after_order_create = True
    adapter = build_adapter(fake)
    assert process_one(adapter)
    assert order_and_job(order_id)[0] == "RETRYING"
    assert len(fake.orders) == 1
    make_due(order_id)
    assert process_one(adapter)
    status, erp_id, job_status, attempts, _, _ = order_and_job(order_id)
    assert status == "CONFIRMED" and erp_id == str(next(iter(fake.orders.values()))["id"])
    assert job_status == "DONE" and attempts == 2
    assert len(fake.orders) == 1
    with SessionLocal() as db:
        confirmed = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.confirmed",
        ))
        assert confirmed.details["duplicate"] is True


@pytest.mark.parametrize("sku,currency,error", [
    ("UNKNOWN", "USD", "unknown_sku:UNKNOWN"),
    ("BOOK", "EUR", "currency_mismatch:EUR"),
])
def test_non_retryable_mapping_failures_stop_after_one_attempt(sku, currency, error):
    order_id = submit(f"nonretry-{sku}-{currency}", sku, currency)
    fake = FakeOdoo()
    assert process_one(build_adapter(fake))
    status, _, job_status, attempts, events, rows = order_and_job(order_id)
    assert status == "FAILED_DEAD" and job_status == "FAILED" and attempts == 1
    assert "order.retrying" not in events
    failed = next(event for event in rows if event.event_type == "order.failed")
    assert failed.details["reason"] == "non_retryable"
    assert error in failed.details["error"]


@pytest.mark.parametrize("failure", ["500", "429", "timeout"])
def test_retryable_odoo_failures_retry_then_confirm(failure):
    order_id = submit(f"retry-{failure}")
    fake = FakeOdoo()
    if failure == "timeout":
        fake.timeout_next = True
    else:
        fake.fail_next = (int(failure), {"name": "test.Failure", "message": f"temporary {failure}"})
    adapter = build_adapter(fake)
    assert process_one(adapter)
    assert order_and_job(order_id)[0] == "RETRYING"
    make_due(order_id)
    assert process_one(adapter)
    assert order_and_job(order_id)[0] == "CONFIRMED"


def test_bad_key_and_business_error_are_non_retryable_with_message():
    for key, status, body, message in [
        ("auth-odoo", 401, {"name": "werkzeug.exceptions.Unauthorized", "message": "Invalid apikey"}, "odoo_auth"),
        ("validation-odoo", 422, {"name": "odoo.exceptions.ValidationError", "message": "Validation rejected"}, "Validation rejected"),
    ]:
        order_id = submit(key)
        fake = FakeOdoo()
        fake.fail_next = status, body
        assert process_one(build_adapter(fake))
        status_order, _, job_status, attempts, events, rows = order_and_job(order_id)
        assert status_order == "FAILED_DEAD" and job_status == "FAILED" and attempts == 1
        assert "order.retrying" not in events
        failed = next(event for event in rows if event.event_type == "order.failed")
        assert message in failed.details["error"]


def test_existing_draft_is_confirmed_without_creating_again():
    order_id = submit("existing-draft")
    fake = FakeOdoo()
    external_ref = f"GW-{order_id}"
    fake.orders[external_ref] = {"id": 600, "state": "draft"}
    assert process_one(build_adapter(fake))
    status, erp_id, job_status, _, _, _ = order_and_job(order_id)
    assert status == "CONFIRMED" and erp_id == "600" and job_status == "DONE"
    assert len(fake.orders) == 1
    assert not any(model == "sale.order" and method == "create" for model, method, _ in fake.calls)


def test_existing_cancelled_order_fails_dead():
    order_id = submit("existing-cancelled")
    fake = FakeOdoo()
    fake.orders[f"GW-{order_id}"] = {"id": 601, "state": "cancel"}
    assert process_one(build_adapter(fake))
    status, _, job_status, attempts, events, rows = order_and_job(order_id)
    assert status == "FAILED_DEAD" and job_status == "FAILED" and attempts == 1
    failed = next(event for event in rows if event.event_type == "order.failed")
    assert failed.details["reason"] == "non_retryable"
    assert "odoo_order_cancelled" in failed.details["error"]
    assert "order.retrying" not in events
