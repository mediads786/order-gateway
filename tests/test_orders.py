from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

from sqlalchemy import func, select

from app.db.models import IdempotencyKey, Order
from app.db.session import SessionLocal
from app.services.orders import submit_order


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
    import uuid

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
