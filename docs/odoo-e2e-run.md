# Odoo end-to-end run (gateway to Odoo 19)

This record documents one run of the gateway against a local Odoo 19 instance (Compose `odoo` profile). It is separate from the earlier API probe in [odoo-notes.md](odoo-notes.md), which tested individual Odoo calls without the gateway.

## Setup used

- `ERP_ADAPTER=odoo`, `ODOO_BASE_URL=http://odoo:8069` (the address of the Odoo container inside the Compose network), `ODOO_DB=gateway`, `ODOO_EXPECTED_CURRENCY=USD`, an Odoo API key in `ODOO_API_KEY`.
- A test product with internal reference `GWDEMO1`, created in Odoo through its JSON-2 API.
- Temporary operator and approver API keys, created for the run and deactivated afterwards.
- The Compose stack was recreated after changing the adapter. With `ODOO_BASE_URL` empty the worker refuses to start with the message "ERP_ADAPTER=odoo requires ODOO_BASE_URL and ODOO_API_KEY", and queued orders wait until it is fixed.

## What was run and what was observed

| Step | Result |
| --- | --- |
| `POST /orders` with an idempotency key (one line: `GWDEMO1`, quantity 2, unit price 10.00, currency USD) | HTTP 201, order accepted and queued |
| Worker delivery | Order reached `CONFIRMED`; the audit trail records the ERP order id |
| Direct read from Odoo | Sale order `S00044` exists with state `sale` |
| Governed cancel: operator requested `cancel_order` | HTTP 202, approval created |
| Approval by a different key with the approver role | HTTP 200, approval `EXECUTED`, cancellation mode `erp`, gateway order `CANCELLED` |
| Direct read from Odoo after the cancel | Sale order `S00044` has state `cancel` |

## Observations

- Odoo reported `amount_total` 23.0 for an order whose gateway total is 20.00. The difference is likely Odoo-side tax configuration; this was not investigated.
- The first attempt failed only because `ODOO_BASE_URL` was empty (see Setup).

## Run 2: governed stock adjustment and signed shipments

Same setup as run 1, plus a temporary `SHIPMENT_WEBHOOK_SECRET` (shipments are signed with base64 HMAC-SHA256 over the raw request body, header `X-Gateway-Signature`). Odoo stock for `GWDEMO1` was read directly from Odoo before and after each step.

| Step | Result |
| --- | --- |
| Governed `adjust_stock` of +10, requested by an operator key and approved by a different approver key | HTTP 202 then 200, approval `EXECUTED`; Odoo on-hand 0 to 10 |
| Order for 4 units delivered through the worker | `CONFIRMED`, Odoo sale order created and confirmed |
| Signed shipment of 3 units | HTTP 200, `APPLIED`, reference `GW-SHIP:<shipment id>`; Odoo on-hand 10 to 7 |
| Identical shipment sent again | HTTP 200, same reference, Odoo on-hand stays 7 |
| Same shipment id with a different body | HTTP 409 `shipment_id_conflict`, stock unchanged |
| Shipment of 1000 units (more than Odoo stock) | HTTP 422 `insufficient_stock`, stock unchanged |
| Shipment with a wrong signature | HTTP 401 |
| Order audit trail | one `shipment.applied`; the refused oversized shipment is recorded as `shipment.failed` with `retryable: false` |

## Not covered by these runs

- Failure handling against Odoo (for example Odoo unavailable mid-delivery).
- A hosted or non-local Odoo.
- Cancelling an order whose delivery is already done in Odoo (the adapter refuses it; not verified live).