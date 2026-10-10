from decimal import Decimal
import uuid

from sqlalchemy import select

from app.db.models import Job, Order
from app.db.session import SessionLocal
from fakes.fake_odoo import FakeOdoo
from test_orders import valid_payload
from test_shipments import (
    body_for, configure, send, set_order_status, shipment_count, submit_order_id,
)


def assert_stored_rejection(response, field: str) -> None:
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "REJECTED"
    assert any(reason["field"] == field for reason in body["reasons"])
    order_id = uuid.UUID(body["order_id"])
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "REJECTED"
        assert db.scalar(select(Job).where(Job.order_id == order_id)) is None


def test_duplicate_sku_shipment_rejected_without_stock_change(client, monkeypatch):
    order_id = submit_order_id()
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    configure(monkeypatch, fake)
    body = body_for(order_id, [{"sku": "BOOK", "qty": 1}, {"sku": "BOOK", "qty": 2}])
    response = send(client, body)
    assert response.status_code == 422
    assert response.json()["error"] == "duplicate_sku:BOOK"
    assert fake.quants[(1, 5)]["quantity"] == 5
    assert not fake.moves
    assert shipment_count() == 0


def test_shipment_id_with_colon_rejected(client, monkeypatch):
    order_id = submit_order_id()
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    configure(monkeypatch, fake)
    response = send(client, body_for(order_id, shipment_id="SHP:BAD"))
    assert response.status_code == 422
    assert "shipment_id" in response.json()["detail"]
    assert fake.quants[(1, 5)]["quantity"] == 5
    assert shipment_count() == 0


def test_numeric_customer_name_is_stored_rejected(client):
    payload = valid_payload()
    payload["customer"]["name"] = 123
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "hardening-name"})
    assert_stored_rejection(response, "customer.name")


def test_overflow_price_is_stored_rejected(client):
    body = b'{"source":"manual","customer":{"name":"A"},"currency":"USD","lines":[{"sku":"S","qty":1,"unit_price":1e309}]}'
    response = client.post("/orders", content=body, headers={"Idempotency-Key": "hardening-overflow"})
    assert_stored_rejection(response, "body")
    assert "Non-finite" in response.json()["reasons"][0]["reason"]
    replay = client.post("/orders", content=body, headers={"Idempotency-Key": "hardening-overflow"})
    assert replay.status_code == 422
    assert replay.json() == response.json()


def test_oversized_quantity_is_stored_rejected(client):
    payload = valid_payload()
    payload["lines"][0]["qty"] = 2147483648
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "hardening-qty"})
    assert_stored_rejection(response, "lines.0.qty")


def test_five_decimal_price_is_stored_rejected(client):
    payload = valid_payload()
    payload["lines"][0]["unit_price"] = "1.00001"
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "hardening-scale"})
    assert_stored_rejection(response, "lines.0.unit_price")


def test_201_lines_is_stored_rejected(client):
    payload = valid_payload()
    payload["lines"] = [{"sku": "BOOK", "qty": 1, "unit_price": "1.00"} for _ in range(201)]
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "hardening-lines"})
    assert_stored_rejection(response, "lines")


def test_maximum_quantity_and_price_have_exact_total(client):
    payload = valid_payload()
    payload["lines"] = [{"sku": "BOOK", "qty": 1000000, "unit_price": "999999999.9999"}]
    response = client.post("/orders", json=payload, headers={"Idempotency-Key": "hardening-exact"})
    assert response.status_code == 201
    expected = Decimal("999999999999900.0000")
    assert Decimal(response.json()["total"]) == expected
    with SessionLocal() as db:
        assert db.get(Order, uuid.UUID(response.json()["order_id"])).total == expected
