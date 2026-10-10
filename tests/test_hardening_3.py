import asyncio
import time
import uuid

import httpx
import pytest
from sqlalchemy import func, select

import app.adapters as adapters_module
import app.main as main_module
from app.adapters.base import NonRetryableAdapterError
from app.adapters.mock import MockErpAdapter
from app.adapters.odoo import OdooAdapter
from app.core.schemas import MAX_REQUEST_BODY_BYTES
from app.db.models import Approval, Order, Proposal
from app.db.session import SessionLocal
from app.workers.worker import classify_failure
from fakes.fake_odoo import FakeOdoo
from test_governance import create_key, workflow_request
from test_hardening_2 import assert_stored_rejection
from test_odoo_adapter import adapter_order
from test_orders import valid_payload
from test_shipments import body_for, configure, send, set_order_status, sign, submit_order_id


def row_count(model) -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.parametrize("async_adapter", [False, True])
def test_identical_shipments_share_one_event_loop(monkeypatch, async_adapter):
    order_id = submit_order_id("hardening-thread")
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    adapter = configure(monkeypatch, fake)
    original = adapter.adjust_stock_for_shipment

    def slow_sync(shipment_id, lines):
        time.sleep(1)
        return original(shipment_id, lines)

    async def slow_async(shipment_id, lines):
        await asyncio.sleep(1)
        return original(shipment_id, lines)

    monkeypatch.setattr(adapter, "adjust_stock_for_shipment", slow_async if async_adapter else slow_sync)
    body = body_for(order_id, shipment_id="SHP-ONE-LOOP")

    async def run_requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app),
                                     base_url="http://gateway.test") as client:
            return await asyncio.wait_for(asyncio.gather(*[
                client.post("/shipments", content=body, headers={"X-Gateway-Signature": sign(body)})
                for _ in range(2)
            ]), 20)

    first, second = asyncio.run(run_requests())
    assert first.status_code == second.status_code == 200
    assert first.json()["erp_reference"] == second.json()["erp_reference"]
    assert fake.quants[(1, 5)]["quantity"] == 3
    assert sum(model == "stock.quant" and method == "action_apply_inventory"
               for model, method, _ in fake.calls) == 1


def test_shipment_validates_all_stock_before_applying(client, monkeypatch):
    order_id = submit_order_id("hardening-partial", ("BOOK", "PEN"))
    set_order_status(order_id, "CONFIRMED")
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    fake.set_stock("PEN", 0)
    configure(monkeypatch, fake)
    response = send(client, body_for(order_id, [{"sku": "BOOK", "qty": 1}, {"sku": "PEN", "qty": 1}]))
    assert response.status_code == 422
    assert "insufficient_stock" in response.json()["error"]
    assert fake.quants[(1, 5)]["quantity"] == 5
    assert fake.quants[(2, 5)]["quantity"] == 0
    assert not fake.moves


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("currency", ["USD", "EUR"])
def test_odoo_checks_actual_sale_order_currency(existing, currency):
    fake = FakeOdoo()
    fake.order_currency = currency
    if existing:
        fake.orders["GW-gateway-order"] = {"id": 77, "state": "sale", "currency": currency}
    adapter = OdooAdapter("http://odoo.test", "key", expected_currency="USD",
                          client=httpx.Client(transport=fake.transport()))
    if currency == "EUR":
        with pytest.raises(NonRetryableAdapterError, match="currency_mismatch:USD:EUR"):
            adapter.create_sales_order(adapter_order())
        assert not any(method == "action_confirm" for _, method, _ in fake.calls)
    else:
        result = adapter.create_sales_order(adapter_order())
        assert result.erp_order_id
        assert result.duplicate is existing


def test_stock_adjustment_idempotency_and_conflict(client, monkeypatch):
    adapter = MockErpAdapter()
    monkeypatch.setattr(adapters_module, "get_adapter", lambda: adapter)
    key = create_key("operator")
    values = {"sku": "BOOK", "qty_delta": 1, "reason": "count"}
    first = workflow_request(client, key, "adjust_stock", values, "adjustment-retry")
    replay = workflow_request(client, key, "adjust_stock", values, "adjustment-retry")
    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert first.headers["X-Request-Id"] == replay.headers["X-Request-Id"]
    changed = workflow_request(client, key, "adjust_stock", {**values, "qty_delta": 2}, "adjustment-retry")
    assert changed.status_code == 409
    assert changed.json()["error"] == "idempotency_key_conflict"
    assert row_count(Approval) == 1


def test_stock_adjustment_without_key_creates_two_approvals(client):
    key = create_key("operator")
    values = {"sku": "BOOK", "qty_delta": 1, "reason": "count"}
    first = workflow_request(client, key, "adjust_stock", values)
    second = workflow_request(client, key, "adjust_stock", values)
    assert first.status_code == second.status_code == 202
    assert first.json()["approval_id"] != second.json()["approval_id"]
    assert row_count(Approval) == 2


def test_thousands_digit_integer_is_stored_rejected(client):
    body = b'{"number":' + b"9" * 4301 + b"}"
    response = client.post("/orders", content=body, headers={"Idempotency-Key": "huge-integer"})
    assert_stored_rejection(response, "body")
    assert response.json()["reasons"][0]["reason"] == "Invalid JSON"


@pytest.mark.parametrize("workflow", ["cancel_order", "adjust_stock"])
def test_workflow_nul_reason_is_rejected(client, workflow):
    key = create_key("operator")
    values = {"order_id": str(uuid.uuid4()), "reason": "bad\x00reason"}
    if workflow == "adjust_stock":
        values.update(sku="BOOK", qty_delta=1)
    response = workflow_request(client, key, workflow, values)
    assert response.status_code == 422
    assert row_count(Approval) == 0


def test_proposal_nul_text_is_rejected(client):
    key = create_key("operator")
    response = client.post("/proposals", json={"text": "bad\x00text"}, headers={"X-API-Key": key})
    assert response.status_code == 422
    assert row_count(Proposal) == 0


@pytest.mark.parametrize("field", ["name", "phone", "email", "sku", "external_ref"])
def test_order_nul_text_is_stored_rejected(client, field):
    values = valid_payload()
    if field in ("name", "phone", "email"):
        values["customer"][field] = "bad\x00text"
    elif field == "sku":
        values["lines"][0][field] = "bad\x00text"
    else:
        values[field] = "bad\x00text"
    response = client.post("/orders", json=values, headers={"Idempotency-Key": "nul-order"})
    assert_stored_rejection(response, "body")


@pytest.mark.parametrize("path", ["/orders", "/webhooks/shopify/orders-create", "/shipments"])
def test_oversized_intake_body_stores_nothing(client, path):
    body = b" " * (MAX_REQUEST_BODY_BYTES + 1)
    response = client.post(path, content=body, headers={"Idempotency-Key": "oversized"})
    assert response.status_code == 413
    assert response.json()["error"] == "request_body_too_large"
    assert row_count(Order) == 0


@pytest.mark.parametrize("field,length,reason_field", [
    ("name", 201, "customer.name"), ("sku", 65, "lines.0.sku"), ("external_ref", 201, "external_ref"),
])
def test_order_text_limits_are_stored_rejected(client, field, length, reason_field):
    values = valid_payload()
    if field == "name":
        values["customer"][field] = "x" * length
    elif field == "sku":
        values["lines"][0][field] = "x" * length
    else:
        values[field] = "x" * length
    response = client.post("/orders", json=values, headers={"Idempotency-Key": "text-limit"})
    assert_stored_rejection(response, reason_field)


def test_shipment_201_lines_is_rejected(client, monkeypatch):
    fake = FakeOdoo()
    configure(monkeypatch, fake)
    response = send(client, body_for(uuid.uuid4(), [{"sku": f"SKU-{i}", "qty": 1} for i in range(201)]))
    assert response.status_code == 422
    assert "lines" in response.json()["detail"]
    assert not fake.calls


@pytest.mark.parametrize("raw_json", [b"[]", b"null"])
@pytest.mark.parametrize("adapter_name", ["mock", "odoo"])
def test_malformed_success_response_raises_retryable_value_error(raw_json, adapter_name):
    def respond(request):
        if adapter_name == "odoo" and request.url.path.endswith("/search_read"):
            return httpx.Response(200, json=[{"id": 77, "state": "sale"}], request=request)
        return httpx.Response(200, content=raw_json, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = (MockErpAdapter(client) if adapter_name == "mock"
               else OdooAdapter("http://odoo.test", "key", client=client))
    with pytest.raises(ValueError):
        adapter.create_sales_order(adapter_order())
    assert classify_failure(invalid_response=True) == (True, "invalid_response")
