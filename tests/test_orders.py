from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
import uuid

import httpx
import pytest
from sqlalchemy import event, func, select, text

from app.db.models import AuditEvent, IdempotencyKey, Job, Order
from app.db.session import SessionLocal
from app.services.orders import submit_order
from app.workers.worker import claim_next_job, process_one, run_until_idle

ERP_URL = "http://127.0.0.1:9001"


def valid_payload():
    return {
        "source": "web",
        "external_ref": "web-123",
        "customer": {"name": "Ada", "email": "ada@example.com"},
        "currency": "USD",
        "lines": [
            {"sku": "BOOK", "qty": 2, "unit_price": "12.50"},
            {"sku": "PEN", "qty": 1, "unit_price": "1.25"},
        ],
    }


def test_valid_order_returns_201_and_computed_total(client):
    result = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "valid-1"})
    assert result.status_code == 201
    assert result.json()["total"] == "26.25"
    assert result.json()["status"] == "RECEIVED"
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.order_id == result.json()["order_id"]))
        assert job is not None
        assert job.status == "QUEUED"


def test_same_key_and_body_returns_original_without_new_order(client):
    first = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "same-1"})
    second = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "same-1"})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1
        saved_status = db.scalar(select(IdempotencyKey.status_code).where(IdempotencyKey.key == "same-1"))
        assert saved_status == 201


def test_same_key_and_different_body_returns_409(client):
    client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "different-1"})
    changed = valid_payload()
    changed["customer"]["name"] = "Grace"
    assert client.post("/orders", json=changed, headers={"Idempotency-Key": "different-1"}).status_code == 409


def test_invalid_orders_are_rejected_and_stored(client):
    cases = []
    zero_qty = valid_payload()
    zero_qty["lines"][0]["qty"] = 0
    cases.append(zero_qty)
    negative_price = valid_payload()
    negative_price["lines"][0]["unit_price"] = "-1"
    cases.append(negative_price)
    empty_lines = valid_payload()
    empty_lines["lines"] = []
    cases.append(empty_lines)
    missing_contact = valid_payload()
    missing_contact["customer"] = {"name": "No Contact"}
    cases.append(missing_contact)

    for index, payload in enumerate(cases):
        result = client.post("/orders", json=payload, headers={"Idempotency-Key": f"invalid-{index}"})
        assert result.status_code == 422
        assert result.json()["status"] == "REJECTED"
        assert result.json()["reasons"]

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Order).where(Order.status == "REJECTED")) == 4
        rejected = db.scalar(select(Order).where(Order.status == "REJECTED", Order.customer_name == "No Contact"))
        assert rejected is not None
        assert rejected.customer_phone is None
        assert rejected.customer_email is None
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_ten_parallel_service_calls_with_same_key_create_one_order():
    body = json.dumps(valid_payload()).encode()
    barrier = Barrier(10)

    def submit_together():
        barrier.wait(timeout=10)
        return submit_order(body, "parallel-10")

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(lambda _: submit_together(), range(10)))
    statuses = [status for status, _ in results]
    assert statuses.count(201) == 1
    assert statuses.count(200) == 9
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 1


def test_received_and_rejected_audit_events_exist(client):
    received = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "audit-good"}).json()
    invalid = valid_payload()
    invalid["lines"] = []
    rejected = client.post("/orders", json=invalid, headers={"Idempotency-Key": "audit-bad"}).json()
    detail = client.get(f"/orders/{received['order_id']}")
    assert detail.status_code == 200
    assert detail.json()["audit_events"][0]["event_type"] == "order.received"
    rejected_detail = client.get(f"/orders/{rejected['order_id']}")
    assert rejected_detail.json()["audit_events"][0]["event_type"] == "order.rejected"


def test_missing_idempotency_key_is_400(client):
    assert client.post("/orders", json=valid_payload()).status_code == 400


def test_health_and_unknown_order(client):
    assert client.get("/health").status_code == 200
    assert client.get(f"/orders/{uuid.uuid4()}").status_code == 404


def test_invalid_json_is_rejected_and_stored(client):
    response = client.post("/orders", content=b"{not-json", headers={"Idempotency-Key": "invalid-json"})
    assert response.status_code == 422
    assert response.json()["status"] == "REJECTED"


def test_extra_field_bad_email_currency_and_source_are_rejected(client):
    cases = []
    extra_field = valid_payload()
    extra_field["unexpected"] = "value"
    cases.append(extra_field)
    nested_extra = valid_payload()
    nested_extra["customer"]["unexpected"] = "value"
    cases.append(nested_extra)
    bad_email = valid_payload()
    bad_email["customer"] = {"name": "Ada", "email": "not-an-email"}
    cases.append(bad_email)
    bad_currency = valid_payload()
    bad_currency["currency"] = "US"
    cases.append(bad_currency)
    bad_source = valid_payload()
    bad_source["source"] = "phone"
    cases.append(bad_source)

    for index, payload in enumerate(cases):
        response = client.post("/orders", json=payload, headers={"Idempotency-Key": f"invalid-shape-{index}"})
        assert response.status_code == 422
        assert response.json()["status"] == "REJECTED"


def test_numeric_unit_price_is_rejected(client):
    payload = valid_payload()
    payload["lines"][0]["unit_price"] = 12.5
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "numeric-price"})
    assert response.status_code == 422
    assert response.json()["status"] == "REJECTED"


def test_nonfinite_numbers_and_nul_are_rejected(client):
    invalid_bodies = [
        b'{"unit_price":NaN}',
        b'{"unit_price":Infinity}',
        b'{"unit_price":-Infinity}',
        b'{"sku":"bad\\u0000sku"}',
    ]
    for index, raw_body in enumerate(invalid_bodies):
        response = client.post("/orders", content=raw_body, headers={"Idempotency-Key": f"unsafe-json-{index}"})
        assert response.status_code == 422
        assert response.json()["status"] == "REJECTED"


def test_rejected_replay_keeps_422_and_same_body(client):
    payload = valid_payload()
    payload["lines"] = []
    first = client.post("/orders", json=payload, headers={"Idempotency-Key": "replay-rejected"})
    replay = client.post("/orders", json=payload, headers={"Idempotency-Key": "replay-rejected"})
    assert first.status_code == 422
    assert replay.status_code == 422
    assert replay.json() == first.json()
    with SessionLocal() as db:
        saved_status = db.scalar(select(IdempotencyKey.status_code).where(IdempotencyKey.key == "replay-rejected"))
        assert saved_status == 422


def test_worker_confirms_order_and_writes_events_in_order(client):
    response = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "worker-success"})
    assert response.status_code == 201
    order_id = uuid.UUID(response.json()["order_id"])
    assert response.json()["status"] == "RECEIVED"
    assert process_one() is True
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id).order_by(AuditEvent.event_id)).all()
        assert order.status == "CONFIRMED"
        assert order.erp_order_id
        assert job.status == "DONE"
        assert [event.event_type for event in events] == [
            "order.received", "order.queued", "order.processing", "order.confirmed"
        ]


def test_two_workers_process_twenty_orders_once(client):
    for index in range(20):
        payload = valid_payload()
        payload["external_ref"] = f"batch-{index}"
        response = client.post("/orders", json=payload, headers={"Idempotency-Key": f"batch-{index}"})
        assert response.status_code == 201

    barrier = Barrier(2)

    def drain_together():
        barrier.wait(timeout=10)
        return run_until_idle()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: drain_together(), range(2)))

    with SessionLocal() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 20
        assert all(job.status == "DONE" and job.attempts == 1 for job in jobs)
        assert all(job.attempts == 1 for job in jobs)
        assert len({job.order_id for job in jobs}) == 20
        order_count = db.scalar(select(func.count()).select_from(Order))
        processing_counts = db.execute(
            select(AuditEvent.order_id, func.count())
            .where(AuditEvent.event_type == "order.processing")
            .group_by(AuditEvent.order_id)
        ).all()
        assert order_count == 20
        assert len(processing_counts) == 20
        assert all(count == 1 for _, count in processing_counts)
    erp_orders = httpx.get(f"{ERP_URL}/sales-orders", timeout=3).json()
    assert len(erp_orders) == 20
    assert len({order["external_id"] for order in erp_orders}) == 20


def test_skip_locked_claims_distinct_jobs_in_separate_transactions():
    seeded_job_ids = []
    for index in range(2):
        payload = valid_payload()
        payload["external_ref"] = f"skip-locked-{index}"
        status, body = submit_order(json.dumps(payload).encode(), f"skip-locked-{index}")
        assert status == 201
        seeded_job_ids.append(uuid.UUID(body["order_id"]))

    session_a = SessionLocal()
    session_b = SessionLocal()
    try:
        claimed_a = claim_next_job(session_a)
        assert claimed_a is not None

        session_b.execute(text("SET LOCAL lock_timeout = '1s'"))
        claimed_b = claim_next_job(session_b)
        assert claimed_b is None or claimed_b.job_id != claimed_a.job_id

        session_a.commit()
        session_b.commit()

        claimed_ids = {claimed_a.job_id}
        if claimed_b is not None:
            claimed_ids.add(claimed_b.job_id)
        if claimed_b is None:
            with SessionLocal.begin() as session_c:
                claimed_c = claim_next_job(session_c)
                assert claimed_c is not None
                claimed_ids.add(claimed_c.job_id)

        assert len(claimed_ids) == 2
    finally:
        session_a.close()
        session_b.close()

    with SessionLocal() as db:
        jobs = db.scalars(select(Job).where(Job.order_id.in_(seeded_job_ids))).all()
        assert len(jobs) == 2
        assert all(job.status == "PROCESSING" and job.attempts == 1 for job in jobs)
        processing_counts = db.execute(
            select(AuditEvent.order_id, func.count())
            .where(AuditEvent.order_id.in_(seeded_job_ids), AuditEvent.event_type == "order.processing")
            .group_by(AuditEvent.order_id)
        ).all()
        assert len(processing_counts) == 2
        assert all(count == 1 for _, count in processing_counts)


def test_worker_failure_marks_order_failed_dead(client):
    fault = httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "error_500"}, timeout=3)
    assert fault.status_code == 200
    response = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": "worker-failure"})
    order_id = uuid.UUID(response.json()["order_id"])
    assert process_one() is True
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        assert order.status == "FAILED_DEAD"
        assert job.status == "FAILED"
        assert job.attempts == 1
        assert job.last_error


def test_mock_erp_deduplicates_external_id():
    order = {
        "external_id": "same-external-id",
        "customer": {"name": "Ada", "email": "ada@example.com"},
        "currency": "USD",
        "lines": [{"sku": "BOOK", "qty": 1, "unit_price": "12.50"}],
        "total": "12.50",
    }
    first = httpx.post(f"{ERP_URL}/sales-orders", json=order, timeout=3)
    second = httpx.post(f"{ERP_URL}/sales-orders", json=order, timeout=3)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["erp_order_id"] == first.json()["erp_order_id"]
    assert len(httpx.get(f"{ERP_URL}/sales-orders", timeout=3).json()) == 1


def test_rejected_order_creates_no_job(client):
    payload = valid_payload()
    payload["lines"] = []
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "rejected-no-job"})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_order_and_job_rollback_if_transaction_fails_after_order_insert():
    class InjectedFailure(RuntimeError):
        pass

    def fail_after_order_insert(session, flush_context):
        if any(isinstance(item, Order) for item in session.new):
            raise InjectedFailure("failure after order insert")

    raw_body = json.dumps(valid_payload()).encode()
    event.listen(SessionLocal.class_, "after_flush", fail_after_order_insert)
    try:
        with pytest.raises(InjectedFailure, match="failure after order insert"):
            submit_order(raw_body, "atomic-failure")
    finally:
        event.remove(SessionLocal.class_, "after_flush", fail_after_order_insert)

    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Order)) == 0
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 0
