import os
from decimal import Decimal
import uuid

import pytest

from app.adapters.base import AdapterLine, AdapterOrder, ShipmentLine
from app.adapters.odoo import OdooAdapter


@pytest.mark.live_odoo
@pytest.mark.skipif(os.getenv("ODOO_LIVE") != "1", reason="set ODOO_LIVE=1 to run against local Odoo")
def test_live_odoo_order_and_repeated_one_unit_shipment():
    adapter = OdooAdapter()
    order = AdapterOrder(
        uuid.uuid4(), uuid.uuid4(), f"live-{uuid.uuid4()}",
        {"name": "Gateway Live Smoke", "email": f"gateway-{uuid.uuid4()}@example.test", "phone": None},
        os.getenv("ODOO_EXPECTED_CURRENCY", "USD"), [AdapterLine("BOOK", 1, Decimal("1.00"))], Decimal("1.00"),
    )
    created = adapter.create_sales_order(order)
    replay = adapter.create_sales_order(order)
    assert not created.duplicate
    assert replay.duplicate and replay.erp_order_id == created.erp_order_id
    order_record = adapter._call("sale.order", "search_read", {
        "domain": [["id", "=", int(created.erp_order_id)]], "fields": ["id", "state"], "limit": 1,
    })
    assert order_record[0]["state"] in ("sale", "done")
    product = adapter._call("product.product", "search_read", {
        "domain": [["default_code", "=", "BOOK"]], "fields": ["id"], "limit": 1,
    })[0]
    warehouse = adapter._call("stock.warehouse", "search_read", {
        "domain": [["code", "=", adapter.warehouse_code]], "fields": ["lot_stock_id"], "limit": 1,
    })[0]
    location_id = warehouse["lot_stock_id"][0]

    def on_hand():
        rows = adapter._call("stock.quant", "search_read", {
            "domain": [["product_id", "=", product["id"]], ["location_id", "=", location_id]],
            "fields": ["quantity"], "limit": 1,
        })
        return Decimal(str(rows[0]["quantity"]))

    before = on_hand()
    shipment_id = f"LIVE-{uuid.uuid4()}"
    adapter.adjust_stock_for_shipment(shipment_id, [ShipmentLine("BOOK", 1)])
    after_first = on_hand()
    adapter.adjust_stock_for_shipment(shipment_id, [ShipmentLine("BOOK", 1)])
    after_replay = on_hand()
    assert after_first == before - 1
    assert after_replay == after_first
