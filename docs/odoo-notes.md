# Odoo JSON-2 notes (verified on the live instance)

Source: two probe runs against local Odoo 19.0-20260926 (Docker, Community), database `gateway`. Real requests and responses below, API key and tracebacks removed. This replaces the need to read `/doc`. Treat it as ground truth; the raw outputs are in `docs/odoo-probe/`.

## Transport

`POST http://localhost:8069/json/2/<model>/<method>`
Headers: `Authorization: bearer <API key>`, `X-Odoo-Database: gateway`, `Content-Type: application/json`.
Body: a JSON object whose keys are the method's parameter names, plus optional `ids` (record methods) and `context`.

## Calls that worked

| Call | Request body | Response |
|---|---|---|
| `res.partner/search_read` | `{"domain": [], "fields": ["name","email"], "limit": 2}` | `[{"id": 3, "name": "Administrator", "email": false}, {"id": 1, "name": "My Company", "email": false}]` (HTTP 200). Empty fields come back as `false`. |
| `product.product/fields_get` | `{"allfields": ["type","is_storable","default_code"], "attributes": ["string","type","selection"]}` | `type` selection: `consu` (Goods), `service`, `combo`; `is_storable` boolean ("Track Inventory"); `default_code` char ("Internal Reference") |
| `stock.warehouse/search_read` | `{"domain": [["code","=","WH"]], "fields": ["code","lot_stock_id"], "limit": 1}` | `[{"id": 1, "code": "WH", "lot_stock_id": [5, "WH/Stock"]}]` (many2one = `[id, name]`) |
| `product.product/create` | `{"vals_list": [{"name": "GW-PROBE", "default_code": "GW-PROBE", "type": "consu", "is_storable": true}]}` | `[1]` (list of new ids) |
| `stock.quant/create` | `{"vals_list": [{"product_id": 1, "location_id": 5, "inventory_quantity": 5}], "context": {"inventory_mode": true}}` | `[1]` |
| `stock.quant/action_apply_inventory` | `{"ids": [1], "context": {"inventory_mode": true, "inventory_name": "GW-SHIP:PROBE:GW-PROBE"}}` | `null` |
| `stock.quant/search_read` | `{"domain": [["product_id","=",1],["location_id","=",5]], "fields": ["quantity","inventory_quantity"]}` | `[{"id": 1, "quantity": 5.0, "inventory_quantity": 0.0}]` (counted quantity resets to 0 after apply) |
| `stock.quant/write` | `{"ids": [1], "vals": {"inventory_quantity": 4.0}, "context": {"inventory_mode": true}}` | `true` |
| `stock.move/search_read` | `{"domain": [["reference","=","GW-SHIP:PROBE2:GW-PROBE"]], "fields": ["reference","origin","state","quantity"]}` | `[{"id": 34, "reference": "GW-SHIP:PROBE2:GW-PROBE", "origin": false, "state": "done", "quantity": 1.0}]` |
| `stock.move.line/search_read` | same, `reference` domain | `[{"id": 34, "reference": "GW-SHIP:PROBE2:GW-PROBE", "state": "done", "quantity": 1.0}]` |
| `sale.order/action_confirm` | `{"ids": []}` | `true` (method exists; not yet run on a real order) |

## Idempotency marker

`inventory_name` in the apply context is stored in `stock.move.reference` and `stock.move.line.reference`, exact string. `stock.move.origin` stays `false`. `stock.move` has **no `name` field** in 19.0 (reading or filtering it gives a 500 ValueError).

## Hazard

`stock.quant/action_apply_inventory` is not a no-op. Calling it again without writing a new `inventory_quantity` created a move of quantity 4.0 (reference `...:again`), because it applied the reset counted value 0. Always `write` `inventory_quantity` and apply as one unit, then re-read on-hand to verify.

## Errors

Body shape: `{"name": ..., "message": ..., "arguments": [...], "context": {}, "debug": "<traceback>"}`. Never store or log `debug`. The 422 case had no `debug`.

| Case | HTTP | `name` | `message` |
|---|---|---|---|
| Bad API key | 401 | `werkzeug.exceptions.Unauthorized` | `Invalid apikey` |
| Unknown model | 404 | `werkzeug.exceptions.NotFound` | `the model 'nope.model' does not exist` |
| Unknown method | 404 | `werkzeug.exceptions.NotFound` | `The method 'res.partner.nope_method' does not exist` |
| Missing record (`sale.order/create` with bad `partner_id`) | 404 | `odoo.exceptions.MissingError` | `Record does not exist or has been deleted. (Record: res.partner(999999999,), User: 2)` |
| Invalid field name in `create` | **500** | `builtins.ValueError` | `Invalid field 'nonexistent_field' in 'res.partner'` |
| Invalid field in domain or fields | **500** | `builtins.ValueError` | `Invalid field 'name' on 'stock.move'` |
| Validation (`res.users/create`, duplicate login) | **422** | `odoo.exceptions.ValidationError` | `The operation cannot be completed: You can not have two users with the same login!` |

Status codes alone are not reliable; classify by `name` as well (see spec section 7).

## Not verified yet

`sale.order/create` with `order_line` command lists, and `sale.order` state values, are not probed. Implement from standard Odoo fields (spec section 6) and let the opt-in live smoke test confirm them.

## Cancellation calls - verified live (2026-10-09, Odoo 19.0, database gateway)

Probe run f3d18b (raw report: `docs/odoo-probe/cancel-probe.txt`). All calls use the standard JSON-2 shape.

| Call | Request body | Response |
|---|---|---|
| `sale.order/create` | `{"vals_list": [{"partner_id": 12, "client_order_ref": "GW-PROBE-A-f3d18b", "order_line": [[0, 0, {"product_id": 1, "product_uom_qty": 1, "price_unit": 2.0}]]}]}` | `[6]` (state `draft`) |
| `sale.order/action_confirm` | `{"ids": [6]}` | `true` (state becomes `sale`) |
| `stock.picking/search_read` | `{"domain": [["sale_id", "=", 6]], "fields": ["id", "state"], "limit": 100}` | `[{"id": 6, "state": "confirmed"}]` (the `sale_id` field exists, so `sale_stock` is installed) |
| `sale.order/action_cancel` | `{"ids": [6]}` | `true`; the order state becomes `cancel` and its delivery state becomes `cancel` |
| `sale.order/action_cancel` again on a cancelled order | `{"ids": [6]}` | `true` (no error) |

`action_cancel` did not open a confirmation wizard in this setup: the plain call cancelled the order, and adding `{"context": {"disable_cancel_warning": true}}` gave the same result, so that context key is not needed here. The adapter's read-back of the order state after the call stays as the safety check. Not verified: cancelling an order whose delivery is already `done` (the adapter refuses that as `already_delivered` before calling `action_cancel`).
