import os

import httpx

from app.adapters.base import AdapterOrder, ErpOrderResult, ShipmentLine


class MockErpAdapter:
    def __init__(self, client: httpx.Client | None = None):
        self.base_url = os.getenv("ERP_BASE_URL", "http://mock_erp:9001").rstrip("/")
        self.timeout = float(os.getenv("ERP_TIMEOUT_SECONDS", "5"))
        self.client = client or httpx.Client(timeout=self.timeout)

    def create_sales_order(self, order: AdapterOrder) -> ErpOrderResult:
        payload = {
            "external_id": order.external_id,
            "customer": order.customer,
            "currency": order.currency,
            "lines": [
                {"sku": line.sku, "qty": line.qty, "unit_price": str(line.unit_price)}
                for line in order.lines
            ],
            "total": str(order.total),
        }
        response = self.client.post(f"{self.base_url}/sales-orders", json=payload, timeout=self.timeout)
        if response.status_code in (200, 201, 409):
            erp_order_id = response.json().get("erp_order_id")
            if isinstance(erp_order_id, str) and erp_order_id:
                return ErpOrderResult(erp_order_id, response.status_code == 409)
            raise ValueError("ERP response did not include a valid erp_order_id")
        response.raise_for_status()
        raise ValueError(f"Unexpected ERP status {response.status_code}")

    def adjust_stock_for_shipment(self, shipment_id: str, lines: list[ShipmentLine]) -> str:
        raise NotImplementedError("Mock ERP does not support shipments")

    def adjust_stock(self, reference: str, sku: str, qty_delta: int, reason: str) -> dict:
        response = self.client.post(
            f"{self.base_url}/stock-adjustments",
            json={"reference": reference, "sku": sku, "qty_delta": qty_delta, "reason": reason},
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if (not isinstance(result, dict) or not isinstance(result.get("adjustment_id"), str)
                or type(result.get("applied")) is not bool):
            raise ValueError("Mock ERP response did not include a valid stock adjustment result")
        return result

    def cancel_order(self, reference: str, erp_order_id: str, reason: str) -> dict:
        response = self.client.post(
            f"{self.base_url}/sales-orders/{erp_order_id}/cancel",
            json={"reference": reference, "reason": reason}, timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()
        if (not isinstance(result, dict) or not isinstance(result.get("cancel_id"), str)
                or type(result.get("applied")) is not bool):
            raise ValueError("Mock ERP response did not include a valid cancellation result")
        return result
