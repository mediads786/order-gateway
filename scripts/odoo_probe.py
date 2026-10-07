"""Throwaway probe. Prints how THIS Odoo instance answers the JSON-2 calls Module 4 needs.
Creates a few records named GW-PROBE (one product, +5 stock, one adjustment). Never prints the API key.
Run from D:\\order-gateway with the key in the environment (see instructions)."""
import json
import os
import sys

import httpx

BASE = os.environ.get("ODOO_BASE_URL", "http://localhost:8069").rstrip("/")
DB = os.environ.get("ODOO_DB", "gateway")
KEY = os.environ.get("ODOO_API_KEY", "")
if not KEY:
    sys.exit("Set ODOO_API_KEY first")


def call(model, method, body=None, key=None, label=""):
    used = KEY if key is None else key
    headers = {
        "Authorization": f"bearer {used}",
        "X-Odoo-Database": DB,
        "Content-Type": "application/json; charset=utf-8",
    }
    body = body or {}
    print(f"\n### {label or model + '/' + method}")
    print(f"POST /json/2/{model}/{method}")
    print(f"request body: {json.dumps(body)[:400]}")
    try:
        r = httpx.post(f"{BASE}/json/2/{model}/{method}", headers=headers, json=body, timeout=30)
    except Exception as exc:  # report and continue
        print(f"EXCEPTION {type(exc).__name__}: {exc}")
        return None
    text = r.text.replace(KEY, "<key>")
    print(f"HTTP {r.status_code}")
    print(text[:1200])
    try:
        return r.json()
    except ValueError:
        return None


print(f"BASE={BASE} DB={DB}")

# A. Error shapes
call("res.partner", "search_read", {"domain": [], "fields": ["name"], "limit": 1},
     key="bad-key-000", label="A1 bad key")
call("nope.model", "search_read", {"domain": []}, label="A2 unknown model")
call("res.partner", "nope_method", {"ids": []}, label="A3 unknown method")
call("res.partner", "create", {"vals_list": [{"nonexistent_field": 1}]}, label="A4 create with bad field")
call("sale.order", "create", {"vals_list": [{"partner_id": 999999999}]}, label="A5 create with bad partner id")

# B. Basic read
call("res.partner", "search_read", {"domain": [], "fields": ["name", "email"], "limit": 2}, label="B search_read ok")

# C. Field discovery
call("product.product", "fields_get",
     {"allfields": ["type", "is_storable", "default_code"], "attributes": ["string", "type", "selection"]},
     label="C1 product fields")
call("stock.quant", "fields_get",
     {"allfields": ["quantity", "inventory_quantity", "inventory_diff_quantity", "inventory_quantity_set", "location_id"],
      "attributes": ["string", "type"]}, label="C2 quant fields")
call("stock.move", "fields_get", {"allfields": ["name", "reference", "origin"], "attributes": ["string", "type"]},
     label="C3 move fields")

# D. Warehouse stock location
wh = call("stock.warehouse", "search_read",
          {"domain": [["code", "=", "WH"]], "fields": ["code", "lot_stock_id"], "limit": 1}, label="D warehouse")
loc = None
if isinstance(wh, list) and wh:
    lot = wh[0].get("lot_stock_id")
    loc = lot[0] if isinstance(lot, list) else lot
print(f"\nstock location id = {loc}")

# E. Create a storable product (try the 19.0 style, then the old style)
prod = call("product.product", "create",
            {"vals_list": [{"name": "GW-PROBE", "default_code": "GW-PROBE", "type": "consu", "is_storable": True}]},
            label="E1 create product (consu + is_storable)")
if not (isinstance(prod, list) and prod):
    prod = call("product.product", "create",
                {"vals_list": [{"name": "GW-PROBE", "default_code": "GW-PROBE", "type": "product"}]},
                label="E2 create product (type=product)")
pid = prod[0] if isinstance(prod, list) and prod else None
print(f"\nproduct id = {pid}")

# F. Stock adjustment flow + inventory_name marker
marker = "GW-SHIP:PROBE:GW-PROBE"
if pid and loc:
    quant = call("stock.quant", "create",
                 {"vals_list": [{"product_id": pid, "location_id": loc, "inventory_quantity": 5}],
                  "context": {"inventory_mode": True}}, label="F1 create quant with counted qty 5")
    qid = quant[0] if isinstance(quant, list) and quant else None
    if qid:
        call("stock.quant", "action_apply_inventory",
             {"ids": [qid], "context": {"inventory_mode": True, "inventory_name": marker}},
             label="F2 apply inventory with inventory_name")
        call("stock.quant", "search_read",
             {"domain": [["product_id", "=", pid], ["location_id", "=", loc]],
              "fields": ["quantity", "inventory_quantity"]}, label="F3 on-hand after apply")
        call("stock.move", "search_read",
             {"domain": [["name", "=", marker]], "fields": ["name", "reference", "product_id", "state", "quantity"]},
             label="F4 find move by inventory_name (the idempotency marker)")
        call("stock.move", "search_read",
             {"domain": [["product_id", "=", pid]], "fields": ["name", "reference", "state"]},
             label="F5 all moves for probe product (to see what name looks like)")

# G. action_confirm exists?
call("sale.order", "action_confirm", {"ids": []}, label="G action_confirm with empty ids")

print("\nDONE")
