"""Probe 2. Reuses the GW-PROBE product/quant from probe 1 (product id 1, location id 5 are looked up, not assumed).
Goal: find which stock.move / stock.move.line field carries the inventory_name, and see one validation-error shape.
Never prints the API key. Debug tracebacks are cut to keep the output short."""
import json
import os
import sys

import httpx

BASE = os.environ.get("ODOO_BASE_URL", "http://localhost:8069").rstrip("/")
DB = os.environ.get("ODOO_DB", "gateway")
KEY = os.environ.get("ODOO_API_KEY", "")
if not KEY:
    sys.exit("Set ODOO_API_KEY first")


def call(model, method, body=None, label=""):
    headers = {"Authorization": f"bearer {KEY}", "X-Odoo-Database": DB,
               "Content-Type": "application/json; charset=utf-8"}
    body = body or {}
    print(f"\n### {label or model + '/' + method}")
    print(f"POST /json/2/{model}/{method}  body: {json.dumps(body)[:300]}")
    try:
        r = httpx.post(f"{BASE}/json/2/{model}/{method}", headers=headers, json=body, timeout=30)
    except Exception as exc:
        print(f"EXCEPTION {type(exc).__name__}: {exc}")
        return None
    print(f"HTTP {r.status_code}")
    try:
        data = r.json()
    except ValueError:
        print(r.text[:500].replace(KEY, "<key>"))
        return None
    if isinstance(data, dict) and "debug" in data:
        data = {k: v for k, v in data.items() if k != "debug"}  # drop traceback
    print(json.dumps(data)[:1500])
    return data


print(f"BASE={BASE} DB={DB}")

prod = call("product.product", "search_read",
            {"domain": [["default_code", "=", "GW-PROBE"]], "fields": ["id", "default_code"], "limit": 1},
            label="1 find probe product")
pid = prod[0]["id"] if isinstance(prod, list) and prod else None
wh = call("stock.warehouse", "search_read",
          {"domain": [["code", "=", "WH"]], "fields": ["lot_stock_id"], "limit": 1}, label="2 warehouse")
lot = wh[0]["lot_stock_id"] if isinstance(wh, list) and wh else None
loc = lot[0] if isinstance(lot, list) else lot
print(f"\nproduct id={pid} location id={loc}")
if not (pid and loc):
    sys.exit("probe product or warehouse not found")

# 3. What do the moves from probe 1 look like? (fields that exist in 19.0)
call("stock.move", "search_read",
     {"domain": [["product_id", "=", pid]], "fields": ["reference", "origin", "state", "quantity", "location_id"]},
     label="3 moves for probe product (reference/origin)")
call("stock.move.line", "search_read",
     {"domain": [["product_id", "=", pid]], "fields": ["reference", "state", "quantity"]},
     label="4 move lines for probe product")

# 5. Second adjustment (5 -> 4) with a new marker, using the quant's counted quantity via write
marker = "GW-SHIP:PROBE2:GW-PROBE"
quant = call("stock.quant", "search_read",
             {"domain": [["product_id", "=", pid], ["location_id", "=", loc]], "fields": ["quantity"], "limit": 1},
             label="5a current quant")
if isinstance(quant, list) and quant:
    qid, on_hand = quant[0]["id"], quant[0]["quantity"]
    call("stock.quant", "write",
         {"ids": [qid], "vals": {"inventory_quantity": on_hand - 1}, "context": {"inventory_mode": True}},
         label="5b set counted qty = on_hand - 1")
    call("stock.quant", "action_apply_inventory",
         {"ids": [qid], "context": {"inventory_mode": True, "inventory_name": marker}},
         label="5c apply with marker")
    call("stock.quant", "search_read", {"domain": [["id", "=", qid]], "fields": ["quantity"]},
         label="5d on-hand after (expect on_hand - 1)")
    call("stock.move", "search_read",
         {"domain": [["reference", "=", marker]], "fields": ["reference", "origin", "state", "quantity"]},
         label="5e search stock.move by reference == marker")
    call("stock.move", "search_read",
         {"domain": [["origin", "=", marker]], "fields": ["reference", "origin", "state", "quantity"]},
         label="5f search stock.move by origin == marker")
    call("stock.move.line", "search_read",
         {"domain": [["reference", "=", marker]], "fields": ["reference", "state", "quantity"]},
         label="5g search stock.move.line by reference == marker")
    # 6. Applying again with nothing changed must not create another move
    call("stock.quant", "action_apply_inventory",
         {"ids": [qid], "context": {"inventory_mode": True, "inventory_name": marker + ":again"}},
         label="6a apply again, no change")
    call("stock.move", "search_read",
         {"domain": [["product_id", "=", pid]], "fields": ["reference", "state", "quantity"]},
         label="6b all moves now (expect no new ':again' move)")

# 7. One validation-style error (duplicate login), to see status code and exception name
call("res.users", "create", {"vals_list": [{"name": "GW Dup", "login": "admin"}]},
     label="7 create user with duplicate login")

print("\nDONE")
