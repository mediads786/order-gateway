import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import app.main as main_module
from app.adapters.odoo import OdooAdapter
from app.db.models import AuditEvent, Order, Shipment
from app.db.session import SessionLocal
from app.services.orders import submit_order
from fakes.fake_odoo import FakeOdoo


SECRET = "shipment-test-secret"


def sign(body):
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


def body_for(order_id, lines=None, shipment_id="SHP-TEST"):
    return json.dumps({
        "shipment_id": shipment_id, "order_id": str(order_id),
        "lines": lines or [{"sku": "BOOK", "qty": 2}],
    }, separators=(",", ":")).encode()


def submit_order_id(key="shipment-order", skus=("BOOK",)):
    lines = [{"sku": sku, "qty": 4, "unit_price": "10.00"} for sku in skus]
    order = {
        "source": "web", "customer": {"name": "Ada", "email": "ada@example.com"},
        "currency": "USD", "lines": lines,
    }
    status, result = submit_order(json.dumps(order).encode(), key)
    assert status == 201
    return uuid.UUID(result["order_id"])


def set_order_status(order_id, status):
    with SessionLocal.begin() as db:
        db.get(Order, order_id).status = status


def configure(monkeypatch, fake):
    monkeypatch.setenv("ERP_ADAPTER", "odoo")
    monkeypatch.setenv("SHIPMENT_WEBHOOK_SECRET", SECRET)
    adapter = OdooAdapter("http://odoo.test", "key", client=httpx.Client(transport=fake.transport()))
    monkeypatch.setattr(main_module, "get_adapter", lambda: adapter)
    return adapter


def send(client, body):
    return client.post("/shipments", content=body, headers={"X-Gateway-Signature": sign(body)})


def shipment_count():
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(Shipment))


def test_valid_signed_shipment_applies_stock_once_and_replays(client, monkeypatch):
    order_id = submit_order_id()
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    configure(monkeypatch, fake)
    body = body_for(order_id)
    first = send(client, body)
    second = send(client, body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["status"] == "APPLIED"
    assert first.json()["erp_reference"] == "GW-SHIP:SHP-TEST"
    assert fake.quants[(1, 5)]["quantity"] == 3
    assert shipment_count() == 1
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.order_id == order_id)
                            .order_by(AuditEvent.event_id)).all()
        assert [event.event_type for event in events][-2:] == ["shipment.received", "shipment.applied"]


def test_same_shipment_id_different_body_conflicts(client, monkeypatch):
    order_id = submit_order_id("shipment-conflict")
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    configure(monkeypatch, fake)
    assert send(client, body_for(order_id)).status_code == 200
    changed = body_for(order_id, [{"sku": "BOOK", "qty": 1}])
    assert send(client, changed).status_code == 409
    assert fake.quants[(1, 5)]["quantity"] == 3


@pytest.mark.parametrize("case,status", [("bad", 401), ("missing", 401), ("secret", 503), ("mock", 501)])
def test_signature_secret_and_adapter_gates_store_nothing(client, monkeypatch, case, status):
    body = body_for(uuid.uuid4())
    monkeypatch.setenv("ERP_ADAPTER", "odoo")
    monkeypatch.setenv("SHIPMENT_WEBHOOK_SECRET", SECRET)
    headers = {"X-Gateway-Signature": sign(body)}
    if case == "bad":
        headers["X-Gateway-Signature"] = "wrong"
    elif case == "missing":
        headers = {}
    elif case == "secret":
        monkeypatch.setenv("SHIPMENT_WEBHOOK_SECRET", "")
    elif case == "mock":
        monkeypatch.setenv("ERP_ADAPTER", "mock")
    response = client.post("/shipments", content=body, headers=headers)
    assert response.status_code == status
    assert shipment_count() == 0


def test_invalid_body_unknown_order_and_unconfirmed_order_store_nothing(client, monkeypatch):
    fake = FakeOdoo()
    configure(monkeypatch, fake)
    unknown = body_for(uuid.uuid4(), shipment_id="unknown")
    assert send(client, unknown).status_code == 404
    order_id = submit_order_id("shipment-unconfirmed")
    unconfirmed = body_for(order_id, shipment_id="unconfirmed")
    assert send(client, unconfirmed).status_code == 409
    set_order_status(order_id, "CONFIRMED")
    wrong_sku = body_for(order_id, [{"sku": "PEN", "qty": 1}], "wrong-sku")
    assert send(client, wrong_sku).status_code == 422
    invalid = json.dumps({"shipment_id": "extra", "order_id": str(order_id), "lines": [{"sku": "BOOK", "qty": 1}], "total": 1}).encode()
    invalid_bodies = [
        invalid,
        b'{invalid json',
        json.dumps({"shipment_id": "empty-lines", "order_id": str(order_id), "lines": []}).encode(),
        json.dumps({"shipment_id": "zero-qty", "order_id": str(order_id), "lines": [{"sku": "BOOK", "qty": 0}]}).encode(),
    ]
    assert all(send(client, invalid_body).status_code == 422 for invalid_body in invalid_bodies)
    assert shipment_count() == 0


def test_odoo_failure_leaves_pending_and_resend_applies(client, monkeypatch):
    order_id = submit_order_id("shipment-recover")
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 8)
    fake.fail_next = (500, {"name": "test.ServerError", "message": "temporarily down"})
    configure(monkeypatch, fake)
    body = body_for(order_id, shipment_id="SHP-RECOVER")
    failed = send(client, body)
    assert failed.status_code == 502 and failed.json()["status"] == "PENDING"
    with SessionLocal() as db:
        row = db.get(Shipment, "SHP-RECOVER")
        assert row.status == "PENDING" and row.last_error
        event = db.scalar(select(AuditEvent).where(AuditEvent.order_id == order_id,
                                                   AuditEvent.event_type == "shipment.failed"))
        assert event.details["retryable"] is True
        shipment_events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.order_id == order_id,
                AuditEvent.event_type.like("shipment.%"),
            ).order_by(AuditEvent.event_id)
        ).all()
        assert [event.event_type for event in shipment_events] == ["shipment.received", "shipment.failed"]
    assert send(client, body).status_code == 200
    assert fake.quants[(1, 5)]["quantity"] == 6
    with SessionLocal() as db:
        shipment_events = db.scalars(
            select(AuditEvent).where(
                AuditEvent.order_id == order_id,
                AuditEvent.event_type.like("shipment.%"),
            ).order_by(AuditEvent.event_id)
        ).all()
        assert [event.event_type for event in shipment_events] == [
            "shipment.received", "shipment.failed", "shipment.applied",
        ]


def test_insufficient_stock_and_existing_line_marker(client, monkeypatch):
    order_id = submit_order_id("shipment-stock", ("BOOK", "PEN"))
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 0)
    fake.set_stock("PEN", 6)
    fake.moves["GW-SHIP:SHP-MIXED:BOOK"] = {"id": 998, "reference": "GW-SHIP:SHP-MIXED:BOOK"}
    configure(monkeypatch, fake)
    insufficient = send(client, body_for(order_id, [{"sku": "BOOK", "qty": 1}], "SHP-LOW"))
    assert insufficient.status_code == 422
    assert fake.quants[(1, 5)]["quantity"] == 0
    mixed = send(client, body_for(order_id, [{"sku": "BOOK", "qty": 2}, {"sku": "PEN", "qty": 3}], "SHP-MIXED"))
    assert mixed.status_code == 200
    assert fake.quants[(1, 5)]["quantity"] == 0
    assert fake.quants[(2, 5)]["quantity"] == 3


def test_simultaneous_identical_shipments_apply_stock_once(client, monkeypatch):
    order_id = submit_order_id("shipment-concurrent")
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 10)
    configure(monkeypatch, fake)
    body = body_for(order_id, shipment_id="SHP-CONCURRENT")
    barrier = Barrier(2)

    def post_once():
        with TestClient(main_module.app) as local_client:
            barrier.wait()
            return send(local_client, body).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: post_once(), range(2)))
    assert sorted(statuses) == [200, 200]
    assert fake.quants[(1, 5)]["quantity"] == 8
    assert sum(model == "stock.quant" and method == "action_apply_inventory"
               for model, method, _ in fake.calls) == 1
    assert shipment_count() == 1
