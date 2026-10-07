from decimal import Decimal
import logging
import os
from typing import Any

import httpx

from app.adapters.base import (
    AdapterOrder,
    ErpOrderResult,
    NonRetryableAdapterError,
    ShipmentLine,
)

logger = logging.getLogger(__name__)


class OdooAdapter:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        database: str | None = None,
        expected_currency: str | None = None,
        warehouse_code: str | None = None,
        client: httpx.Client | None = None,
    ):
        self.base_url = (base_url or os.getenv("ODOO_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.getenv("ODOO_API_KEY", "")
        self.database = database or os.getenv("ODOO_DB", "gateway")
        self.expected_currency = expected_currency or os.getenv("ODOO_EXPECTED_CURRENCY", "USD")
        self.warehouse_code = warehouse_code or os.getenv("ODOO_WAREHOUSE_CODE", "WH")
        self.timeout = float(os.getenv("ERP_TIMEOUT_SECONDS", "5"))
        self.client = client or httpx.Client(
            timeout=self.timeout,
        )
        self.client.headers.update({
            "Authorization": f"bearer {self.api_key}",
            "X-Odoo-Database": self.database,
            "Content-Type": "application/json",
        })

    def _call(
        self,
        model: str,
        method: str,
        arguments: dict[str, Any],
        *,
        order_id: str | None = None,
        job_id: str | None = None,
        shipment_id: str | None = None,
        approval_id: str | None = None,
    ) -> Any:
        logger.info(
            "Odoo call order_id=%s job_id=%s shipment_id=%s approval_id=%s model=%s method=%s",
            order_id, job_id, shipment_id, approval_id, model, method,
        )
        response = self.client.post(
            f"{self.base_url}/json/2/{model}/{method}", json=arguments, timeout=self.timeout,
        )
        if response.is_error:
            try:
                error_body = response.json()
            except ValueError:
                error_body = {}
            name = str(error_body.get("name", "")) if isinstance(error_body, dict) else ""
            message = str(error_body.get("message", "")) if isinstance(error_body, dict) else ""
            message = (message or f"Odoo returned HTTP {response.status_code}")[:500]
            logger.error(
                "Odoo error order_id=%s job_id=%s shipment_id=%s approval_id=%s model=%s method=%s status=%s name=%s message=%s",
                order_id, job_id, shipment_id, approval_id, model, method, response.status_code, name, message,
            )
            if response.status_code in (401, 403):
                raise NonRetryableAdapterError("odoo_auth", f"odoo_auth: {message}")
            if response.status_code == 404:
                raise NonRetryableAdapterError("odoo_not_found", message)
            non_retryable_names = {
                "builtins.ValueError",
                "builtins.TypeError",
                "builtins.KeyError",
                "builtins.AttributeError",
            }
            if response.status_code == 422 or name.startswith("odoo.exceptions.") or name in non_retryable_names:
                raise NonRetryableAdapterError("odoo_business_error", message)
            response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise ValueError(f"Odoo {model}/{method} returned invalid JSON") from exc

    def _identity(self, order: AdapterOrder) -> dict[str, str]:
        return {"order_id": str(order.order_id), "job_id": str(order.job_id)}

    def create_sales_order(self, order: AdapterOrder) -> ErpOrderResult:
        identity = self._identity(order)
        external_ref = f"GW-{order.external_id}"
        existing = self._call(
            "sale.order", "search_read",
            {"domain": [["client_order_ref", "=", external_ref]], "fields": ["id", "state"], "limit": 2},
            **identity,
        )
        if not isinstance(existing, list) or any(not isinstance(row, dict) for row in existing):
            raise ValueError("Odoo sale.order/search_read returned an invalid response")
        if existing:
            record = existing[0]
            odoo_id, state = record.get("id"), record.get("state")
            if type(odoo_id) is not int:
                raise ValueError("Odoo sale.order/search_read omitted id")
            if state in ("sale", "done"):
                return ErpOrderResult(str(odoo_id), True)
            if state in ("draft", "sent"):
                self._confirm_order(odoo_id, identity)
                return ErpOrderResult(str(odoo_id), True)
            if state == "cancel":
                raise NonRetryableAdapterError("odoo_order_cancelled", "odoo_order_cancelled")
            raise ValueError(f"Odoo sale.order has unsupported state: {state!r}")

        if order.currency != self.expected_currency:
            raise NonRetryableAdapterError(
                f"currency_mismatch:{order.currency}",
                f"currency_mismatch:{order.currency} (expected {self.expected_currency})",
            )
        products = self._resolve_products(order, identity)
        partner_id = self._resolve_partner(order, identity)
        order_lines = [
            [0, 0, {
                "product_id": products[line.sku],
                "product_uom_qty": line.qty,
                "price_unit": float(line.unit_price),
            }]
            for line in order.lines
        ]
        created = self._call(
            "sale.order", "create",
            {"vals_list": [{
                "partner_id": partner_id,
                "client_order_ref": external_ref,
                "order_line": order_lines,
            }]},
            **identity,
        )
        odoo_id = self._created_id(created, "sale.order/create")
        self._confirm_order(odoo_id, identity)
        return ErpOrderResult(str(odoo_id), False)

    def _confirm_order(self, order_id: int, identity: dict[str, str]) -> None:
        result = self._call("sale.order", "action_confirm", {"ids": [order_id]}, **identity)
        if result is not True:
            raise ValueError("Odoo sale.order/action_confirm returned an invalid response")

    def _resolve_products(self, order: AdapterOrder, identity: dict[str, str]) -> dict[str, int]:
        skus = list(dict.fromkeys(line.sku for line in order.lines))
        records = self._call(
            "product.product", "search_read",
            {"domain": [["default_code", "in", skus], ["active", "=", True]],
             "fields": ["id", "default_code"], "limit": len(skus) * 2},
            **identity,
        )
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise ValueError("Odoo product.product/search_read returned an invalid response")
        result: dict[str, int] = {}
        for sku in skus:
            matches = [row for row in records if row.get("default_code") == sku]
            if not matches:
                raise NonRetryableAdapterError(f"unknown_sku:{sku}", f"unknown_sku:{sku}")
            if len(matches) > 1:
                raise NonRetryableAdapterError(f"ambiguous_sku:{sku}", f"ambiguous_sku:{sku}")
            product_id = matches[0].get("id")
            if type(product_id) is not int:
                raise ValueError(f"Odoo product lookup omitted id for {sku}")
            result[sku] = product_id
        return result

    def _resolve_partner(self, order: AdapterOrder, identity: dict[str, str]) -> int:
        name = order.customer["name"]
        email = order.customer.get("email")
        phone = order.customer.get("phone")
        domain: list[Any]
        if email and phone:
            domain = ["|", ["email", "ilike", email], "&", ["name", "=", name], ["phone", "=", phone]]
        elif email:
            domain = [["email", "ilike", email]]
        else:
            domain = [["name", "=", name], ["phone", "=", phone or ""]]
        partners = self._call(
            "res.partner", "search_read",
            {"domain": domain, "fields": ["id", "name", "email", "phone"], "limit": 100},
            **identity,
        )
        if not isinstance(partners, list) or any(not isinstance(row, dict) for row in partners):
            raise ValueError("Odoo res.partner/search_read returned an invalid response")
        partner_id = None
        if email:
            partner_id = next((row.get("id") for row in partners
                               if isinstance(row.get("email"), str)
                               and row["email"].casefold() == email.casefold()), None)
        if partner_id is None and phone:
            partner_id = next((row.get("id") for row in partners
                               if row.get("name") == name and row.get("phone") == phone), None)
        if type(partner_id) is int:
            return partner_id
        created = self._call(
            "res.partner", "create",
            {"vals_list": [{"name": name, "email": email or False, "phone": phone or False}]},
            **identity,
        )
        return self._created_id(created, "res.partner/create")

    @staticmethod
    def _created_id(result: Any, call: str) -> int:
        if isinstance(result, list) and result and type(result[0]) is int:
            return result[0]
        raise ValueError(f"Odoo {call} did not return a usable id")

    def adjust_stock_for_shipment(self, shipment_id: str, lines: list[ShipmentLine]) -> str:
        warehouse = self._call(
            "stock.warehouse", "search_read",
            {"domain": [["code", "=", self.warehouse_code]], "fields": ["id", "lot_stock_id"], "limit": 1},
            shipment_id=shipment_id,
        )
        if not isinstance(warehouse, list) or not warehouse or not isinstance(warehouse[0], dict):
            raise NonRetryableAdapterError("warehouse_not_found", f"unknown_warehouse:{self.warehouse_code}")
        location_ref = warehouse[0].get("lot_stock_id")
        if not isinstance(location_ref, list) or not location_ref or not isinstance(location_ref[0], int):
            raise ValueError("Odoo warehouse response omitted its stock location")
        location_id = location_ref[0]

        for line in lines:
            products = self._call(
                "product.product", "search_read",
                {"domain": [["default_code", "=", line.sku], ["active", "=", True]],
                 "fields": ["id", "default_code"], "limit": 2},
                shipment_id=shipment_id,
            )
            if not isinstance(products, list) or any(not isinstance(row, dict) for row in products):
                raise ValueError("Odoo product.product/search_read returned an invalid response")
            if not products:
                raise NonRetryableAdapterError(f"unknown_sku:{line.sku}", f"unknown_sku:{line.sku}")
            if len(products) > 1:
                raise NonRetryableAdapterError(f"ambiguous_sku:{line.sku}", f"ambiguous_sku:{line.sku}")
            product_id = products[0].get("id")
            if type(product_id) is not int:
                raise ValueError(f"Odoo product lookup omitted id for {line.sku}")
            marker = f"GW-SHIP:{shipment_id}:{line.sku}"
            prior_moves = self._call(
                "stock.move", "search_read",
                {"domain": [["reference", "=", marker]], "fields": ["id", "reference"], "limit": 1},
                shipment_id=shipment_id,
            )
            if not isinstance(prior_moves, list) or any(not isinstance(row, dict) for row in prior_moves):
                raise ValueError("Odoo stock.move/search_read returned an invalid response")
            if isinstance(prior_moves, list) and prior_moves:
                continue
            quants = self._call(
                "stock.quant", "search_read",
                {"domain": [["product_id", "=", product_id], ["location_id", "=", location_id]],
                 "fields": ["id", "quantity"], "limit": 2},
                shipment_id=shipment_id,
            )
            if not isinstance(quants, list) or any(not isinstance(row, dict) for row in quants):
                raise ValueError("Odoo stock.quant/search_read returned an invalid response")
            if not quants:
                raise NonRetryableAdapterError(f"insufficient_stock:{line.sku}", f"insufficient_stock:{line.sku}")
            if len(quants) > 1:
                raise NonRetryableAdapterError(f"ambiguous_quant:{line.sku}", f"multiple stock quants for {line.sku}")
            quant_id = quants[0].get("id")
            on_hand = Decimal(str(quants[0].get("quantity", 0)))
            if type(quant_id) is not int or on_hand < line.qty:
                raise NonRetryableAdapterError(f"insufficient_stock:{line.sku}", f"insufficient_stock:{line.sku}")
            self._apply_counted_quantity(quant_id, float(on_hand - line.qty), marker, shipment_id=shipment_id)
        return f"GW-SHIP:{shipment_id}"

    def adjust_stock(self, reference: str, sku: str, qty_delta: int, reason: str) -> dict:
        approval_id = reference.split(":", 2)[1] if reference.startswith("GW-ADJ:") else reference
        warehouse = self._call(
            "stock.warehouse", "search_read",
            {"domain": [["code", "=", self.warehouse_code]], "fields": ["id", "lot_stock_id"], "limit": 1},
            approval_id=approval_id,
        )
        if not isinstance(warehouse, list) or not warehouse or not isinstance(warehouse[0], dict):
            raise NonRetryableAdapterError("warehouse_not_found", f"unknown_warehouse:{self.warehouse_code}")
        location_ref = warehouse[0].get("lot_stock_id")
        if not isinstance(location_ref, list) or not location_ref or type(location_ref[0]) is not int:
            raise ValueError("Odoo warehouse response omitted its stock location")
        location_id = location_ref[0]
        products = self._call(
            "product.product", "search_read",
            {"domain": [["default_code", "=", sku], ["active", "=", True]],
             "fields": ["id", "default_code"], "limit": 2},
            approval_id=approval_id,
        )
        if not isinstance(products, list) or any(not isinstance(row, dict) for row in products):
            raise ValueError("Odoo product.product/search_read returned an invalid response")
        if not products:
            raise NonRetryableAdapterError(f"unknown_sku:{sku}", f"unknown_sku:{sku}")
        if len(products) > 1:
            raise NonRetryableAdapterError(f"ambiguous_sku:{sku}", f"ambiguous_sku:{sku}")
        product_id = products[0].get("id")
        if type(product_id) is not int:
            raise ValueError(f"Odoo product lookup omitted id for {sku}")

        prior_moves = self._call(
            "stock.move", "search_read",
            {"domain": [["reference", "=", reference]], "fields": ["id", "reference"], "limit": 1},
            approval_id=approval_id,
        )
        if not isinstance(prior_moves, list) or any(not isinstance(row, dict) for row in prior_moves):
            raise ValueError("Odoo stock.move/search_read returned an invalid response")
        if prior_moves:
            return {"adjustment_id": reference, "applied": False}

        quants = self._call(
            "stock.quant", "search_read",
            {"domain": [["product_id", "=", product_id], ["location_id", "=", location_id]],
             "fields": ["id", "quantity"], "limit": 2},
            approval_id=approval_id,
        )
        if not isinstance(quants, list) or any(not isinstance(row, dict) for row in quants):
            raise ValueError("Odoo stock.quant/search_read returned an invalid response")
        if len(quants) > 1:
            raise NonRetryableAdapterError(f"ambiguous_quant:{sku}", f"multiple stock quants for {sku}")
        quant_id = quants[0].get("id") if quants else None
        on_hand = Decimal(str(quants[0].get("quantity", 0))) if quants else Decimal("0")
        if quant_id is not None and type(quant_id) is not int:
            raise ValueError(f"Odoo stock quant lookup omitted id for {sku}")
        new_quantity = on_hand + Decimal(qty_delta)
        if new_quantity < 0:
            raise NonRetryableAdapterError("insufficient_stock", f"insufficient_stock:{sku}")

        self._apply_counted_quantity(
            quant_id,
            float(new_quantity),
            reference,
            product_id=product_id if quant_id is None else None,
            location_id=location_id if quant_id is None else None,
            approval_id=approval_id,
        )
        return {"adjustment_id": reference, "applied": True}

    def set_opening_stock(self, sku: str, quantity: int = 100) -> None:
        warehouse = self._call(
            "stock.warehouse", "search_read",
            {"domain": [["code", "=", self.warehouse_code]], "fields": ["id", "lot_stock_id"], "limit": 1},
        )
        if not isinstance(warehouse, list) or not warehouse or not isinstance(warehouse[0], dict):
            raise NonRetryableAdapterError("warehouse_not_found", f"unknown_warehouse:{self.warehouse_code}")
        location = warehouse[0].get("lot_stock_id")
        products = self._call(
            "product.product", "search_read",
            {"domain": [["default_code", "=", sku], ["active", "=", True]],
             "fields": ["id", "default_code", "type", "is_storable"], "limit": 2},
        )
        if not isinstance(products, list) or any(not isinstance(row, dict) for row in products):
            raise ValueError(f"Odoo product lookup returned an invalid response for {sku}")
        if not products:
            created_product = self._call("product.product", "create", {"vals_list": [{
                "name": sku, "default_code": sku, "type": "consu", "is_storable": True,
            }]})
            product_id = self._created_id(created_product, "product.product/create")
        elif len(products) > 1:
            raise NonRetryableAdapterError(f"ambiguous_sku:{sku}", f"ambiguous_sku:{sku}")
        else:
            product_id = products[0].get("id")
            if products[0].get("type") != "consu" or products[0].get("is_storable") is not True:
                raise NonRetryableAdapterError(f"not_storable:{sku}", f"product is not storable: {sku}")
        if not isinstance(location, list) or not location or not isinstance(location[0], int):
            raise ValueError("Odoo warehouse response omitted its stock location")
        if type(product_id) is not int:
            raise ValueError(f"Odoo product lookup omitted id for {sku}")
        marker = f"GW-SEED:{sku}"
        existing_moves = self._call(
            "stock.move", "search_read",
            {"domain": [["reference", "=", marker]], "fields": ["id", "reference"], "limit": 1},
        )
        if not isinstance(existing_moves, list) or any(not isinstance(row, dict) for row in existing_moves):
            raise ValueError("Odoo stock.move/search_read returned an invalid response")
        if existing_moves:
            return
        quants = self._call(
            "stock.quant", "search_read",
            {"domain": [["product_id", "=", product_id], ["location_id", "=", location[0]]],
             "fields": ["id"], "limit": 2},
        )
        if not isinstance(quants, list) or len(quants) > 1 or any(not isinstance(row, dict) for row in quants):
            raise ValueError(f"Expected at most one stock quant for {sku}")
        if quants:
            quant_id = quants[0].get("id")
            if type(quant_id) is not int:
                raise ValueError(f"Odoo stock quant lookup omitted id for {sku}")
            self._apply_counted_quantity(quant_id, quantity, marker)
        else:
            self._apply_counted_quantity(None, quantity, marker, product_id=product_id, location_id=location[0])

    def _apply_counted_quantity(
        self,
        quant_id: int | None,
        quantity: int | float,
        marker: str,
        *,
        product_id: int | None = None,
        location_id: int | None = None,
        shipment_id: str | None = None,
        approval_id: str | None = None,
    ) -> None:
        if quant_id is None:
            created = self._call(
                "stock.quant", "create",
                {"vals_list": [{"product_id": product_id, "location_id": location_id,
                                "inventory_quantity": quantity}],
                 "context": {"inventory_mode": True}},
                shipment_id=shipment_id,
                approval_id=approval_id,
            )
            quant_id = self._created_id(created, "stock.quant/create")
        else:
            written = self._call(
                "stock.quant", "write",
                {"ids": [quant_id], "vals": {"inventory_quantity": quantity},
                 "context": {"inventory_mode": True}},
                shipment_id=shipment_id,
                approval_id=approval_id,
            )
            if written is not True:
                raise ValueError("Odoo stock.quant/write returned an invalid response")
        self._call(
            "stock.quant", "action_apply_inventory",
            {"ids": [quant_id], "context": {"inventory_mode": True, "inventory_name": marker}},
            shipment_id=shipment_id,
            approval_id=approval_id,
        )
        updated = self._call(
            "stock.quant", "search_read",
            {"domain": [["id", "=", quant_id]], "fields": ["quantity"], "limit": 1},
            shipment_id=shipment_id,
            approval_id=approval_id,
        )
        if not isinstance(updated, list) or not updated or not isinstance(updated[0], dict):
            raise ValueError("Odoo stock.quant/search_read returned an invalid response after adjustment")
        if Decimal(str(updated[0].get("quantity", -1))) != Decimal(str(quantity)):
            raise ValueError(f"Odoo inventory adjustment verification failed for quant_id={quant_id}")
