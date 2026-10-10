import json
from decimal import Decimal
from pathlib import Path
import uuid

import httpx
import pytest

from app.adapters.base import AdapterLine, AdapterOrder, NonRetryableAdapterError, ShipmentLine
from app.adapters.factory import get_adapter
from app.adapters.odoo import OdooAdapter
from fakes.fake_odoo import FakeOdoo

FIXTURES = Path(__file__).parent / "fixtures" / "odoo"


def adapter_order(lines=None, currency="USD"):
    lines = lines or [AdapterLine("BOOK", 1, Decimal("19.99"))]
    return AdapterOrder(uuid.uuid4(), uuid.uuid4(), "gateway-order", {
        "name": "Ada", "email": "ada@example.com", "phone": "+1000",
    }, currency, lines, sum((line.unit_price * line.qty for line in lines), Decimal("0")))


def recorded(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_auth_headers_golden_mapping_and_decimal_boundary(caplog):
    calls = []

    def respond(request):
        args = json.loads(request.content)
        model, method = request.url.path.rsplit("/", 2)[-2:]
        calls.append((request, model, method, args))
        responses = {
            ("sale.order", "search_read"): [],
            ("product.product", "search_read"): [
                {"id": 1, "default_code": "A"}, {"id": 2, "default_code": "B"},
                {"id": 3, "default_code": "C"},
            ],
            ("res.partner", "search_read"): [],
            ("res.partner", "create"): [4],
            ("sale.order", "create"): [5],
            ("sale.order", "read"): [{"id": 5, "currency_id": [1, "USD"]}],
            ("sale.order", "action_confirm"): True,
        }
        return httpx.Response(200, json=responses[(model, method)], request=request)

    secret = "test-api-key-never-log"
    client = httpx.Client(transport=httpx.MockTransport(respond))
    adapter = OdooAdapter("http://odoo.test", secret, "gateway", client=client)
    order = adapter_order([
        AdapterLine("A", 1, Decimal("19.99")),
        AdapterLine("B", 1, Decimal("0.10")),
        AdapterLine("C", 1, Decimal("1234.50")),
    ])
    result = adapter.create_sales_order(order)
    assert result.erp_order_id == "5" and not result.duplicate
    assert len(calls) == 7
    for request, *_ in calls:
        assert request.headers["authorization"] == f"bearer {secret}"
        assert request.headers["x-odoo-database"] == "gateway"
        assert request.headers["content-type"] == "application/json"
    sale_create = next(args for _, model, method, args in calls if (model, method) == ("sale.order", "create"))
    price_units = [line[2]["price_unit"] for line in sale_create["vals_list"][0]["order_line"]]
    assert [Decimal(str(value)) for value in price_units] == [Decimal("19.99"), Decimal("0.1"), Decimal("1234.5")]
    assert sale_create["vals_list"][0] == {
        "partner_id": 4,
        "client_order_ref": "GW-gateway-order",
        "order_line": [
            [0, 0, {"product_id": 1, "product_uom_qty": 1, "price_unit": 19.99}],
            [0, 0, {"product_id": 2, "product_uom_qty": 1, "price_unit": 0.1}],
            [0, 0, {"product_id": 3, "product_uom_qty": 1, "price_unit": 1234.5}],
        ],
    }
    assert secret not in caplog.text


def test_existing_sale_order_response_uses_recorded_shape():
    fixture = recorded("order_found.json")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=([{"currency_id": [1, "USD"]}] if request.url.path.endswith("/read")
                       else fixture["response"]), request=request,
        )
    ))
    result = OdooAdapter("http://odoo.test", "key", client=client).create_sales_order(adapter_order())
    assert result.erp_order_id == str(fixture["response"][0]["id"])
    assert result.duplicate is True


def test_recorded_missing_order_then_missing_sku_is_non_retryable():
    fixture = recorded("order_missing.json")
    seen = []

    def respond(request):
        seen.append(request.url.path)
        model = request.url.path.rsplit("/", 2)[-2]
        payload = fixture["response"] if model == "sale.order" else recorded("product_missing.json")["response"]
        return httpx.Response(200, json=payload, request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(NonRetryableAdapterError, match="unknown_sku:BOOK"):
        OdooAdapter("http://odoo.test", "key", client=client).create_sales_order(adapter_order())
    assert len(seen) == 2


def test_recorded_create_response_shape_is_parsed():
    fixture = recorded("create_result.json")
    assert OdooAdapter._created_id(fixture["response"], "sale.order/create") == 1


def test_recorded_order_product_and_partner_search_responses_parse():
    for model, fixture_name in [
        ("sale.order", "order_found.json"),
        ("sale.order", "order_missing.json"),
        ("product.product", "product_found.json"),
        ("product.product", "product_missing.json"),
        ("res.partner", "partner_found.json"),
        ("res.partner", "partner_missing.json"),
    ]:
        fixture = recorded(fixture_name)
        client = httpx.Client(transport=httpx.MockTransport(
            lambda request, result=fixture["response"]: httpx.Response(200, json=result, request=request)
        ))
        adapter = OdooAdapter("http://odoo.test", "key", client=client)
        assert adapter._call(model, "search_read", {}) == fixture["response"]


@pytest.mark.parametrize("fixture_name,reason", [("unauthorized.json", "odoo_auth"), ("unknown_model.json", "odoo_not_found")])
def test_recorded_odoo_errors_are_non_retryable(fixture_name, reason):
    fixture = recorded(fixture_name)

    def respond(request):
        return httpx.Response(fixture["status"], json=fixture["response"], request=request)

    client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(NonRetryableAdapterError) as caught:
        OdooAdapter("http://odoo.test", "secret-key", client=client).create_sales_order(adapter_order())
    assert caught.value.reason == reason
    assert "secret-key" not in str(caught.value)


def test_recorded_validation_message_is_sanitized_and_retained():
    fixture = recorded("validation_error.json")
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(fixture["status"], json=fixture["response"], request=request)
    ))
    with pytest.raises(NonRetryableAdapterError, match="two users with the same login"):
        OdooAdapter("http://odoo.test", "key", client=client)._call("res.users", "create", {})


def test_fake_odoo_confirms_existing_draft_and_rejects_cancelled_order():
    fake = FakeOdoo()
    order = adapter_order()
    fake.orders["GW-gateway-order"] = {"id": 77, "state": "draft"}
    adapter = OdooAdapter("http://odoo.test", "key", client=httpx.Client(transport=fake.transport()))
    result = adapter.create_sales_order(order)
    assert result == type(result)("77", True)
    assert fake.orders["GW-gateway-order"]["state"] == "sale"
    fake.orders["GW-gateway-order"]["state"] = "cancel"
    with pytest.raises(NonRetryableAdapterError, match="odoo_order_cancelled"):
        adapter.create_sales_order(order)


def test_factory_defaults_and_rejects_invalid_or_incomplete_config(monkeypatch):
    monkeypatch.delenv("ERP_ADAPTER", raising=False)
    assert type(get_adapter()).__name__ == "MockErpAdapter"
    monkeypatch.setenv("ERP_ADAPTER", "invalid")
    with pytest.raises(RuntimeError, match="Unsupported ERP_ADAPTER"):
        get_adapter()
    monkeypatch.setenv("ERP_ADAPTER", "odoo")
    for base_url, api_key in [("", "key"), ("http://odoo.test", "")]:
        monkeypatch.setenv("ODOO_BASE_URL", base_url)
        monkeypatch.setenv("ODOO_API_KEY", api_key)
        with pytest.raises(RuntimeError, match="requires ODOO_BASE_URL and ODOO_API_KEY"):
            get_adapter()


def test_fake_odoo_order_and_shipment_inventory_marker():
    fake = FakeOdoo()
    fake.set_stock("BOOK", 5)
    adapter = OdooAdapter("http://odoo.test", "key", client=httpx.Client(transport=fake.transport()))
    result = adapter.create_sales_order(adapter_order())
    assert fake.orders["GW-gateway-order"]["state"] == "sale"
    assert not result.duplicate
    reference = adapter.adjust_stock_for_shipment("SHP-1", [ShipmentLine("BOOK", 2)])
    assert reference == "GW-SHIP:SHP-1"
    assert fake.quants[(1, 5)]["quantity"] == 3
