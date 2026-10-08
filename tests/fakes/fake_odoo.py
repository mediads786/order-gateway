import json
from collections import defaultdict
from typing import Any

import httpx


class FakeOdoo:
    def __init__(self):
        self.products: dict[str, list[dict]] = defaultdict(list)
        self.partners: list[dict] = []
        self.orders: dict[str, dict] = {}
        self.quants: dict[tuple[int, int], dict] = {}
        self.moves: dict[str, dict] = {}
        self.deliveries: list[dict] = []
        self.cancel_without_effect = False
        self.calls: list[tuple[str, str, dict]] = []
        self.fail_next: tuple[int, dict] | None = None
        self.timeout_next = False
        self.timeout_after_order_create = False
        self._ids = 100
        self._seed_id = 1
        self.add_product("BOOK", product_id=1)
        self.add_product("PEN", product_id=2)
        self.add_product("TSHIRT-BLK-M", product_id=3)
        self.add_product("MUG-WHT", product_id=4)
        self.add_product("NOTEBOOK-A5", product_id=5)
        self.add_product("TEA", product_id=6)

    def add_product(self, sku: str, product_id: int | None = None) -> int:
        product_id = product_id or self._next_id()
        self.products[sku].append({"id": product_id, "default_code": sku, "active": True,
                                   "type": "consu", "is_storable": True})
        return product_id

    def set_stock(self, sku: str, quantity: int) -> None:
        product_id = self.products[sku][0]["id"]
        self.quants[(product_id, 5)] = {"id": self._next_id(), "product_id": product_id,
                                        "location_id": 5, "quantity": quantity,
                                        "inventory_quantity": 0}

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        model, method = request.url.path.rsplit("/", 2)[-2:]
        args = json.loads(request.content or b"{}")
        self.calls.append((model, method, args))
        if self.timeout_next:
            self.timeout_next = False
            raise httpx.ReadTimeout("fake Odoo timeout", request=request)
        if self.fail_next is not None:
            status, body = self.fail_next
            self.fail_next = None
            return httpx.Response(status, json=body, request=request)
        result = self._dispatch(model, method, args)
        if model == "sale.order" and method == "create" and self.timeout_after_order_create:
            self.timeout_after_order_create = False
            raise httpx.ReadTimeout("response timed out after create", request=request)
        if (model, method) == ("stock.quant", "action_apply_inventory"):
            return httpx.Response(200, content=b"null", headers={"Content-Type": "application/json"}, request=request)
        if (model, method) in (("stock.quant", "write"), ("sale.order", "action_confirm")):
            return httpx.Response(200, content=b"true", headers={"Content-Type": "application/json"}, request=request)
        if method == "create":
            created_ids = result if isinstance(result, list) else [result]
            return httpx.Response(
                200,
                content=json.dumps(created_ids).encode(),
                headers={"Content-Type": "application/json"},
                request=request,
            )
        return httpx.Response(200, json=result, request=request)

    def _dispatch(self, model: str, method: str, args: dict[str, Any]) -> Any:
        if (model, method) == ("sale.order", "search_read"):
            ref = self._domain_value(args["domain"], "client_order_ref")
            if ref is None:
                order_id = self._domain_value(args["domain"], "id")
                order = next((row for row in self.orders.values() if row["id"] == order_id), None)
            else:
                order = self.orders.get(ref)
            return [{"id": order["id"], "state": order["state"]}] if order else []
        if (model, method) == ("stock.picking", "search_read"):
            order_id = self._domain_value(args["domain"], "sale_id")
            return [dict(row) for row in self.deliveries if row.get("sale_id") == order_id]
        if (model, method) == ("product.product", "search_read"):
            sku = self._domain_value(args["domain"], "default_code")
            skus = sku if isinstance(sku, list) else [sku]
            return [dict(row) for item in skus for row in self.products.get(item, []) if row["active"]]
        if (model, method) == ("res.partner", "search_read"):
            return [dict(row) for row in self.partners]
        if (model, method) == ("res.partner", "create"):
            vals = args["vals_list"][0]
            row = {"id": self._next_id(), **vals}
            self.partners.append(row)
            return [row["id"]]
        if (model, method) == ("product.product", "create"):
            vals = args["vals_list"][0]
            return [self.add_product(vals["default_code"])]
        if (model, method) == ("sale.order", "create"):
            vals = args["vals_list"][0]
            order_id = self._next_id()
            self.orders[vals["client_order_ref"]] = {"id": order_id, "state": "draft", **vals}
            return [order_id]
        if (model, method) == ("sale.order", "action_confirm"):
            for order in self.orders.values():
                if order["id"] in args["ids"]:
                    order["state"] = "sale"
            return True
        if (model, method) == ("sale.order", "action_cancel"):
            if not self.cancel_without_effect:
                for order in self.orders.values():
                    if order["id"] in args["ids"]:
                        order["state"] = "cancel"
            return True
        if (model, method) == ("stock.warehouse", "search_read"):
            return [{"id": 1, "lot_stock_id": [5, "WH/Stock"]}]
        if (model, method) == ("stock.move", "search_read"):
            marker = self._domain_value(args["domain"], "reference")
            return [dict(self.moves[marker])] if marker in self.moves else []
        if (model, method) == ("stock.quant", "search_read"):
            domain = args["domain"]
            row = None
            quant_id = self._domain_value(domain, "id")
            if quant_id is not None:
                row = next((q for q in self.quants.values() if q["id"] == quant_id), None)
            else:
                product_id = self._domain_value(domain, "product_id")
                location_id = self._domain_value(domain, "location_id")
                row = self.quants.get((product_id, location_id))
            return [dict(row)] if row else []
        if (model, method) == ("stock.quant", "create"):
            vals = args["vals_list"][0]
            row = {"id": self._next_id(), "quantity": 0, **vals}
            self.quants[(row["product_id"], row["location_id"])] = row
            return [row["id"]]
        if (model, method) == ("stock.quant", "write"):
            row = next(q for q in self.quants.values() if q["id"] == args["ids"][0])
            row.update(args["vals"])
            return True
        if (model, method) == ("stock.quant", "action_apply_inventory"):
            row = next(q for q in self.quants.values() if q["id"] == args["ids"][0])
            row["quantity"] = row["inventory_quantity"]
            row["inventory_quantity"] = 0
            marker = args["context"]["inventory_name"]
            self.moves[marker] = {"id": self._next_id(), "reference": marker}
            return None
        raise AssertionError(f"Unexpected fake Odoo call {model}/{method} {args}")

    def _next_id(self) -> int:
        self._ids += 1
        return self._ids

    @staticmethod
    def _domain_value(domain: list, field: str):
        for clause in domain:
            if isinstance(clause, list) and clause[0] == field:
                return clause[2]
        return None
