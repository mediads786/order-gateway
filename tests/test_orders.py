from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import time
from threading import Barrier
import uuid

import httpx
import pytest
from sqlalchemy import event, func, select, text

from app.db.models import AuditEvent, IdempotencyKey, Job, Order
from app.db.session import SessionLocal
from app.services.orders import submit_order
from app.workers import worker
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
        assert events[2].details == {"attempt": 1}
        assert events[3].details == {"erp_order_id": order.erp_order_id, "attempt": 1}


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
        processing_events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "order.processing")).all()
        assert all(event.details == {"attempt": 1} for event in processing_events)
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


def test_worker_failure_marks_order_failed_dead(client, monkeypatch):
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 1)
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
        failed = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.failed",
        ))
        assert failed.details == {"attempt": 1, "error": job.last_error, "reason": "max_attempts"}


def _make_job_due(order_id):
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=2)


def _submit_one(client, key):
    response = client.post("/orders", json=valid_payload(), headers={"Idempotency-Key": key})
    assert response.status_code == 201
    return uuid.UUID(response.json()["order_id"])


@pytest.mark.parametrize("attempt,base,cap,random_value,expected", [
    (1, 2, 20, 0, 1), (2, 2, 20, 0, 2), (3, 2, 20, 0, 4),
    (8, 2, 5, 1, 5), (2, 2, 20, 1, 4),
])
def test_backoff_bounds_and_growth(attempt, base, cap, random_value, expected):
    assert worker.compute_backoff(attempt, base, cap, lambda: random_value) == expected


@pytest.mark.parametrize("status,retryable,reason", [
    (500, True, "http_500"), (502, True, "http_502"), (429, True, "http_429"),
    (400, False, "non_retryable"), (404, False, "non_retryable"), (422, False, "non_retryable"),
])
def test_failure_classification_http_status(status, retryable, reason):
    assert worker.classify_failure(status_code=status) == (retryable, reason)


@pytest.mark.parametrize("exc", [
    httpx.TimeoutException("timeout"), httpx.ConnectError("connect"),
    httpx.ReadError("read"), httpx.RemoteProtocolError("remote protocol"),
])
def test_failure_classification_transport_errors(exc):
    assert worker.classify_failure(error=exc) == (True, type(exc).__name__)


def test_failure_classification_malformed_success_response():
    assert worker.classify_failure(status_code=200, invalid_response=True) == (True, "invalid_response")


@pytest.mark.parametrize("mode", ["error_500", "rate_limit_429", "timeout"])
def test_temporary_erp_failures_retry_then_succeed(client, monkeypatch, mode):
    if mode == "timeout":
        monkeypatch.setattr(worker, "ERP_TIMEOUT_SECONDS", 0.05)
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": mode}, timeout=3)
    order_id = _submit_one(client, f"retry-{mode}")
    assert process_one() is True
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        order = db.get(Order, order_id)
        assert job.status == "QUEUED"
        assert job.attempts == 1
        assert job.last_error
        assert job.next_attempt_at > datetime.now(timezone.utc)
        assert order.status == "RETRYING"
        retry_event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.retrying",
        ))
        assert set(retry_event.details) == {"attempt", "error", "retry_in_seconds", "next_attempt_at"}
        assert retry_event.details["attempt"] == 1
        assert retry_event.details["error"] == job.last_error
        assert retry_event.details["retry_in_seconds"] > 0
        assert retry_event.details["next_attempt_at"].endswith("Z")
        assert datetime.fromisoformat(retry_event.details["next_attempt_at"].replace("Z", "+00:00")) == job.next_attempt_at
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "none"}, timeout=3)
    _make_job_due(order_id)
    assert process_one() is True
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        assert job.status == "DONE"
        assert db.get(Order, order_id).status == "CONFIRMED"
        processing = db.scalars(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.processing",
        ).order_by(AuditEvent.event_id)).all()
        assert [event.details for event in processing] == [{"attempt": 1}, {"attempt": 2}]
        confirmed = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.confirmed",
        ))
        assert confirmed.details == {"erp_order_id": db.get(Order, order_id).erp_order_id, "attempt": 2}


def test_permanent_erp_422_does_not_retry(client):
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "error_422"}, timeout=3)
    order_id = _submit_one(client, "erp-422-permanent")
    assert process_one()
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        assert job.status == "FAILED"
        assert job.attempts == 1
        assert db.get(Order, order_id).status == "FAILED_DEAD"
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id)).all()
        assert sum(event.event_type == "order.retrying" for event in events) == 0
        failed = next(event for event in events if event.event_type == "order.failed")
        assert failed.details == {"attempt": 1, "error": job.last_error, "reason": "non_retryable"}


def test_attempt_limit_moves_retryable_job_to_dead_letter(client, monkeypatch):
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 3)
    monkeypatch.setattr(worker, "RETRY_BASE_SECONDS", 0)
    monkeypatch.setattr(worker, "RETRY_MAX_SECONDS", 0)
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "error_500"}, timeout=3)
    order_id = _submit_one(client, "retry-exhausted")
    for attempt in range(3):
        assert process_one()
        if attempt < 2:
            _make_job_due(order_id)
    with SessionLocal() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        assert job.status == "FAILED" and job.attempts == 3
        assert db.get(Order, order_id).status == "FAILED_DEAD"
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id)).all()
        assert sum(event.event_type == "order.retrying" for event in events) == 2
        assert sum(event.event_type == "order.failed" for event in events) == 1
        assert sum(event.event_type == "order.processing" for event in events) == 3
        processing = [event.details for event in events if event.event_type == "order.processing"]
        assert processing == [{"attempt": 1}, {"attempt": 2}, {"attempt": 3}]
        retrying = [event for event in events if event.event_type == "order.retrying"]
        assert [event.details["attempt"] for event in retrying] == [1, 2]
        assert all(set(event.details) == {"attempt", "error", "retry_in_seconds", "next_attempt_at"} for event in retrying)
        failed = next(event for event in events if event.event_type == "order.failed")
        assert failed.details == {"attempt": 3, "error": job.last_error, "reason": "max_attempts"}


def test_claim_skips_job_whose_retry_time_is_in_future(client):
    order_id = _submit_one(client, "future-retry")
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        job.next_attempt_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    with SessionLocal.begin() as db:
        assert claim_next_job(db) is None


def test_manual_retry_requeues_dead_job_and_worker_confirms(client):
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "error_422"}, timeout=3)
    order_id = _submit_one(client, "manual-retry")
    assert process_one()
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "none"}, timeout=3)
    result = client.post(f"/orders/{order_id}/retry")
    assert result.status_code == 200
    assert result.json() == {"order_id": str(order_id), "status": "QUEUED"}
    assert process_one()
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CONFIRMED"
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        assert job.status == "DONE" and job.attempts == 1
        requeued = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued",
        ))
        assert requeued.details == {"previous_attempts": 1}


def test_manual_retry_rejects_non_dead_and_unknown_orders(client):
    order_id = _submit_one(client, "manual-retry-not-dead")
    assert client.post(f"/orders/{order_id}/retry").status_code == 409
    assert client.post(f"/orders/{uuid.uuid4()}/retry").status_code == 404


def test_simultaneous_manual_retries_only_requeue_once(client):
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "error_422"}, timeout=3)
    order_id = _submit_one(client, "manual-retry-race")
    assert process_one()
    barrier = Barrier(2)

    def retry_together(_):
        barrier.wait(timeout=10)
        return client.post(f"/orders/{order_id}/retry").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(retry_together, range(2)))
    assert sorted(statuses) == [200, 409]
    with SessionLocal() as db:
        assert db.scalar(select(Job).where(Job.order_id == order_id)).status == "QUEUED"
        requeued_events = db.scalars(select(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued",
        )).all()
        assert len(requeued_events) == 1
        assert requeued_events[0].details == {"previous_attempts": 1}


def test_stale_jobs_are_recovered_or_dead_lettered(client, monkeypatch):
    monkeypatch.setattr(worker, "MAX_ATTEMPTS", 3)
    retry_id = _submit_one(client, "stale-retry")
    dead_id = _submit_one(client, "stale-dead")
    fresh_id = _submit_one(client, "stale-fresh")
    with SessionLocal.begin() as db:
        retry_job = db.scalar(select(Job).where(Job.order_id == retry_id))
        dead_job = db.scalar(select(Job).where(Job.order_id == dead_id))
        fresh_job = db.scalar(select(Job).where(Job.order_id == fresh_id))
        for job, attempts in ((retry_job, 1), (dead_job, 3)):
            job.status = "PROCESSING"
            job.attempts = attempts
            job.locked_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.get(Order, retry_id).status = "PROCESSING"
        db.get(Order, dead_id).status = "PROCESSING"
        fresh_job.status = "PROCESSING"
        fresh_job.attempts = 1
        fresh_job.locked_at = datetime.now(timezone.utc)
        db.get(Order, fresh_id).status = "PROCESSING"
    assert worker.recover_stale_jobs() == 2
    with SessionLocal() as db:
        assert db.scalar(select(Job).where(Job.order_id == retry_id)).status == "QUEUED"
        assert db.get(Order, retry_id).status == "RETRYING"
        assert db.scalar(select(Job).where(Job.order_id == dead_id)).status == "FAILED"
        assert db.get(Order, dead_id).status == "FAILED_DEAD"
        assert db.scalar(select(Job).where(Job.order_id == fresh_id)).status == "PROCESSING"
        assert db.get(Order, fresh_id).status == "PROCESSING"
        retry_events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == retry_id)).all()
        dead_events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == dead_id)).all()
        recovered = next(event for event in retry_events if event.event_type == "order.recovered_stale")
        assert recovered.details == {"attempt": 1}
        failed = next(event for event in dead_events if event.event_type == "order.failed")
        assert failed.details == {"attempt": 3, "error": "worker_lost", "reason": "worker_lost"}


def test_mock_erp_configurable_faults_and_reset():
    httpx.post(f"{ERP_URL}/admin/reset", timeout=3)
    payload = {"external_id": "fault-config", "customer": {"name": "Ada"}, "currency": "USD",
               "lines": [{"sku": "X", "qty": 1, "unit_price": "1.00"}], "total": "1.00"}
    configured = httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "none", "fail_rate": 1, "latency_ms": 0}, timeout=3)
    assert configured.status_code == 200
    assert httpx.post(f"{ERP_URL}/sales-orders", json=payload, timeout=3).status_code == 500
    httpx.post(f"{ERP_URL}/admin/faults", json={"mode": "none", "fail_rate": 0, "latency_ms": 150}, timeout=3)
    started = time.monotonic()
    created = httpx.post(f"{ERP_URL}/sales-orders", json=payload, timeout=3)
    elapsed = time.monotonic() - started
    assert created.status_code == 201
    assert elapsed >= 0.15
    httpx.post(f"{ERP_URL}/admin/reset", timeout=3)
    assert httpx.get(f"{ERP_URL}/sales-orders", timeout=3).json() == []
    assert httpx.post(f"{ERP_URL}/sales-orders", json=payload, timeout=3).status_code == 201


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
    assert second.status_code == 409
    assert second.json()["erp_order_id"] == first.json()["erp_order_id"]
    assert len(httpx.get(f"{ERP_URL}/sales-orders", timeout=3).json()) == 1


def test_duplicate_on_retry_confirms_with_existing_erp_order(client):
    order_id = _submit_one(client, "duplicate-on-retry")
    external_id = str(order_id)
    mock_order = {
        "external_id": external_id,
        "customer": {"name": "Ada", "email": "ada@example.com"},
        "currency": "USD",
        "lines": [{"sku": "BOOK", "qty": 1, "unit_price": "12.50"}],
        "total": "12.50",
    }
    created = httpx.post(f"{ERP_URL}/sales-orders", json=mock_order, timeout=3)
    assert created.status_code == 201
    existing_erp_order_id = created.json()["erp_order_id"]

    assert process_one() is True

    with SessionLocal() as db:
        order = db.get(Order, order_id)
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id)).all()
        assert order.status == "CONFIRMED"
        assert order.erp_order_id == existing_erp_order_id
        assert job.status == "DONE"
        confirmed = next(event for event in events if event.event_type == "order.confirmed")
        assert confirmed.details == {"erp_order_id": existing_erp_order_id, "attempt": 1}
    matching_orders = [row for row in httpx.get(f"{ERP_URL}/sales-orders", timeout=3).json()
                       if row["external_id"] == external_id]
    assert len(matching_orders) == 1


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
