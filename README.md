# Order Gateway — Modules 1–3B: Intake, Queue, Worker, Retries, Shopify, and Mock ERP

Accepts canonical orders, computes totals with `Decimal`, stores raw and canonical data, enforces idempotency, and records audit events. Valid orders atomically receive a queued job; a separate worker sends them to the in-memory mock ERP.

## Requirements

- Python 3.12
- Docker Compose

## Run

From the project root, create a virtual environment and install the pinned dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
$env:SHOPIFY_WEBHOOK_SECRET = "replace-with-shopify-webhook-secret"
docker compose up -d --build
.\.venv\Scripts\python.exe -m pytest -v
```

Compose starts PostgreSQL, the API, mock ERP, and the worker. The API is available at `http://127.0.0.1:8002`; the mock ERP is at `http://127.0.0.1:9001`. The API applies Alembic migrations at startup, and Compose waits for the API migration and mock ERP before starting the worker. Tests read `TEST_DATABASE_URL` from the environment or `.env`; the test setup creates that database if missing, forces `DATABASE_URL` to it before importing the app, and clears its tables before each test. It exits before importing the app if the database name does not end in `_test`.

Set `ERP_BASE_URL`, `ERP_TIMEOUT_SECONDS`, `WORKER_POLL_INTERVAL_SECONDS`, `RETRY_BASE_SECONDS`, `RETRY_MAX_SECONDS`, `MAX_ATTEMPTS`, and `STALE_JOB_SECONDS` in `.env` for the worker. Retry delays use capped exponential backoff with jitter. Temporary failures (5xx, 429, timeouts, and connection errors) retry until `MAX_ATTEMPTS`; other 4xx responses fail permanently. Orders awaiting a retry have status `RETRYING`. Stale worker claims are recovered after `STALE_JOB_SECONDS`.

## Request example

```json
{
  "source": "web",
  "external_ref": "web-123",
  "customer": {"name": "Ada", "email": "ada@example.com"},
  "currency": "USD",
  "lines": [{"sku": "BOOK", "qty": 2, "unit_price": "12.50"}]
}
```

Prices must be JSON strings, such as `"12.50"`. JSON numeric prices are rejected by design so the API never converts a binary float into money.

Send it to `POST /orders` with an `Idempotency-Key` header. The server returns `201` for a new valid order, `200` for a repeated identical request, `409` for reuse with a different body, `422` for a stored rejected order, and `400` when the key is missing. `GET /orders/{order_id}` returns the order with its audit events.

The API and worker read `DATABASE_URL`. Tests use `TEST_DATABASE_URL`, shown in `.env.example`, and never connect to the `order_gateway` database.

## Mock ERP

- `POST /sales-orders` creates an ERP order once per `external_id`; duplicate requests return `409` with the existing `erp_order_id`.
- `GET /sales-orders` lists records; `GET /sales-orders/{erp_order_id}` returns one record.
- `POST /admin/faults` accepts `none`, `error_500`, `rate_limit_429`, `error_422`, `timeout`, or `slow`, plus optional `fail_rate` (0–1) and `latency_ms`; `POST /admin/reset` clears its in-memory records and all fault settings.
- `POST /orders/{order_id}/retry` requeues an order in `FAILED_DEAD` and returns it to job state `QUEUED`.

## Stale job recovery

The worker recovers jobs left in `PROCESSING` past `STALE_JOB_SECONDS`; recovered jobs retry unless they have reached `MAX_ATTEMPTS`.

## Shopify `orders/create` webhook

1. The endpoint reads the raw request bytes once; Shopify's HMAC-SHA256 signature is verified against those exact bytes before parsing or storing anything.
2. An empty or unset `SHOPIFY_WEBHOOK_SECRET` returns `503`; a missing or invalid signature returns `401`, and neither case stores data.
3. A missing webhook ID returns `400`; a present topic other than `orders/create` is acknowledged with `200 {"status":"ignored"}` and is not stored.
4. A valid `orders/create` body maps to only the canonical fields below, is serialized deterministically, and goes through the existing `submit_order` path and queue.
5. New, replayed, and rejected orders return `200` with the order ID and status; bad payloads return `200` after storage as `REJECTED` so Shopify does not retry permanent input errors forever.

Configure `SHOPIFY_WEBHOOK_SECRET` with the secret for this subscription. The endpoint accepts the legacy `X-Shopify-*` webhook headers only; newer Events delivery header names without `X-` are unsupported.

### Shopify mapping

| Canonical field | Shopify path (first usable value wins) | Rule |
| --- | --- | --- |
| `source` | Constant | `shopify` |
| `external_ref` | `id` | Convert to string |
| `customer.name` | `customer.first_name` + `customer.last_name`; then `shipping_address.name`; then `billing_address.name` | Join non-empty name parts with one space, trim, and reject if no usable value |
| `customer.email` | `email`; then `customer.email` | Trim; omit if neither is usable |
| `customer.phone` | `phone`; then `customer.phone`; then `shipping_address.phone` | Trim; omit if none is usable |
| Contact requirement | `customer.email` or `customer.phone` | Reject if both are absent or empty |
| `currency` | `currency` | Keep as supplied; canonical validation requires a three-letter code |
| `lines[].sku` | `line_items[].sku` | Trim; required and non-empty; never invent a value |
| `lines[].qty` | `line_items[].quantity` | Integer |
| `lines[].unit_price` | `line_items[].price` | Preserve the string exactly; never convert through float |
| `lines` | `line_items` | Must be a non-empty list |

Null, missing, and empty strings are not usable mapping values. `total_price`, `subtotal_price`, tax, discounts, shipping, gift cards, `name`, `order_number`, and all other Shopify fields are ignored. The gateway computes the total from line items, so Shopify's totals do not affect the stored order. SKU is required. This module supports one store and only the `orders/create` topic.

### Live test (manual)

- Create a free Shopify development store.
- Create a webhook subscription for `orders/create` pointing to a public tunnel URL ending in `/webhooks/shopify/orders-create`.
- Confirm in current Shopify docs which signing secret applies to the method used to create the webhook, then set that secret as `SHOPIFY_WEBHOOK_SECRET`.
- Place a test order and confirm one gateway order reaches `CONFIRMED`.
- Resend the same webhook from Shopify and confirm the gateway still has one order for that webhook ID.

## Module 4: Odoo adapter and shipments

The Odoo adapter sends confirmed gateway orders to Odoo and applies signed shipment stock adjustments.
It uses Odoo 19 JSON-2 with an API key as a bearer token.
It does not use XML-RPC or JSON-RPC; those APIs were deprecated in Odoo 19 and parts are removed in Odoo 20.
`ERP_ADAPTER=mock` remains the default and preserves the existing mock ERP behavior.
Set `ERP_ADAPTER=odoo` to send orders to Odoo and enable `POST /shipments`.

### Start the Odoo profile

The `odoo` Compose profile is isolated from plain `docker compose up -d` and uses its own PostgreSQL and filestore volumes. From the repository root:

```powershell
docker compose --profile odoo up -d odoo-db
docker compose --profile odoo run --rm odoo odoo -d gateway -i base,sale_management,stock --without-demo=all --stop-after-init
docker compose --profile odoo up -d odoo
```

Wait for Odoo at `http://localhost:8069`, then create an API key in Preferences → Account Security. Put it in `.env` as `ODOO_API_KEY`; do not commit or log it. For Compose, set `ERP_ADAPTER=odoo`, `ODOO_BASE_URL=http://odoo:8069`, and `ODOO_DB=gateway`. Set `ODOO_PG_USER` and `ODOO_PG_PASSWORD` for the Odoo-only database, then seed products and opening inventory:

```powershell
docker compose --profile odoo up -d --build
$env:ODOO_BASE_URL = "http://localhost:8069"
.\.venv\Scripts\python.exe scripts\odoo_seed.py
```

`ODOO_IMAGE` defaults to `odoo:19.0`. To return to the unchanged mock ERP behavior, set `ERP_ADAPTER=mock` and run `docker compose up -d --build`.

The seed script ensures these fixture/test SKUs exist as active storable products and seeds 100 units when no stock quant exists: `BOOK`, `PEN`, `X`, `TSHIRT-BLK-M`, `MUG-WHT`, `NOTEBOOK-A5`, and `TEA`. It leaves existing on-hand quantities unchanged when run again.

### Order mapping and idempotency

| Gateway field | Odoo field | Mapping |
| --- | --- | --- |
| order external key | `sale.order.client_order_ref` | `GW-` + the existing worker `external_id` (gateway order UUID) |
| customer | `res.partner` | Search case-insensitive email first, else exact name and phone; create with name, email, and phone if missing |
| currency | — | Must equal `ODOO_EXPECTED_CURRENCY`; otherwise fail permanently with `currency_mismatch` |
| `lines[].sku` | `product.product.default_code` | Exact active match; unknown or duplicate SKU fails permanently |
| `lines[].qty` | `sale.order.order_line[].product_uom_qty` | Preserve integer quantity |
| `lines[].unit_price` | `sale.order.order_line[].price_unit` | Convert `Decimal` to a JSON number at the Odoo boundary only |
| lines | `sale.order.order_line` | `[[0, 0, {values}], ...]` ORM commands |

Before creating a sale order, the adapter searches `client_order_ref`. A found `sale` or `done` order is returned as a duplicate; `draft` or `sent` is confirmed and returned as a duplicate. This makes a retry after a lost create response reuse the same Odoo order. The `order.confirmed` audit details add `duplicate: true` for duplicate results from Odoo; the mock ERP audit detail shape is unchanged.

### Signed shipments

Shipments are synchronous and require `ERP_ADAPTER=odoo`. The endpoint verifies `X-Gateway-Signature` as base64 HMAC-SHA256 over the raw request bytes using `SHIPMENT_WEBHOOK_SECRET`. Example using Python and the already installed `httpx` package:

```python
import base64, hashlib, hmac, json, httpx

secret = "read-from-your-environment"
body = json.dumps({
    "shipment_id": "SHP-1001",
    "order_id": "<confirmed-gateway-order-uuid>",
    "lines": [{"sku": "BOOK", "qty": 2}],
}, separators=(",", ":")).encode()
signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
response = httpx.post(
    "http://127.0.0.1:8002/shipments",
    content=body,
    headers={"X-Gateway-Signature": signature, "Content-Type": "application/json"},
)
print(response.status_code, response.json())
```

The gateway stores the first event as `PENDING`, applies each line through `stock.quant`, then marks it `APPLIED`. Retries search `stock.move.reference` for `GW-SHIP:<shipment_id>:<sku>` before adjusting that line, so a partial Odoo success resumes without double-decrementing. Reusing a shipment ID with another canonical body returns `409`.

### Odoo limits and verified API notes

- A simultaneous first order for the same new customer can create duplicate partner records.
- Money stays `Decimal` except the final `price_unit` conversion required by Odoo's JSON number API.
- Counted-quantity adjustments can race with other stock changes in Odoo; the gateway does not enforce cumulative shipped quantity against ordered quantity.
- Shipments are synchronous, use one warehouse (`ODOO_WAREHOUSE_CODE`), and support no lots or serial numbers.
- The order currency must match `ODOO_EXPECTED_CURRENCY`.
- Odoo Online plans may restrict external API access; this setup targets the self-hosted Community image.
- The live probe confirmed `search_read` uses `domain`, `fields`, and `limit`; `create` uses `vals_list`; `action_confirm` uses `ids`; warehouse `lot_stock_id` is `[id, name]`; and storable products use `type="consu"` plus `is_storable=true`.
- The probe confirmed the inventory flow uses `stock.quant` `write`/`create` with `inventory_mode`, then `action_apply_inventory` with `inventory_name`. The marker is searchable on `stock.move.reference`; `stock.move.name` does not exist.
- Odoo error bodies include `name`, `message`, `arguments`, `context`, and sometimes `debug`. The adapter logs only the message, truncated to 500 characters, and never logs the key or traceback body.
- Recorded requests and responses are summarized in [docs/odoo-notes.md](docs/odoo-notes.md). The sale order create command and `state` values remain to be confirmed by the opt-in live smoke test.

### Odoo smoke test

Seed `BOOK`, use a host-reachable URL for local pytest, and set the live flag:

```powershell
$env:ODOO_BASE_URL = "http://localhost:8069"
$env:ODOO_LIVE = "1"
.\.venv\Scripts\python.exe -m pytest -m live_odoo -v
```
