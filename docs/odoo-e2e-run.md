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

## Not covered by this run

- Signed shipment application against Odoo.
- A governed stock adjustment against Odoo.
- Failure handling against Odoo (for example Odoo unavailable mid-delivery).
- A hosted or non-local Odoo.