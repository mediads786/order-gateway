# Shopify webhook to Odoo end-to-end proof

Date: 2026-10-15 (Asia/Karachi).
Application commit tested: `5d98720bfbaf9b49bea188e9665afd3ac305cc66`.
The proof script and this record are committed together after verification;
the resulting commit is identified by `git log -1` for these files. No application
code, tests, README, or other tracked documentation was changed.

## Actual result: PASS

Verified again on 2026-10-15 at 08:50:16 Asia/Karachi (03:50:16 UTC, synthetic
identifier timestamp). The existing local Compose stack was already running with
`ERP_ADAPTER=odoo`. The script checked the live worker configuration and credential
agreement without displaying credentials. No credentials were created or regenerated,
and no service configuration was changed during this verification. Mode B was not run.

Gateway order: `ada7ccbb-c85c-45cd-a814-9d50d0cc4063`.
Odoo sale order: id `17`, name `S00050`, state `sale`.
Odoo reference: `GW-ada7ccbb-c85c-45cd-a814-9d50d0cc4063`.
Synthetic order name, order number and webhook id:
`E2E-20261015T035016-800bebbdfaf1`.

Exact PowerShell sequence used for this recorded run against the already prepared
stack, using the existing virtual environment and credentials from `.env`/environment:

```powershell
Set-Location D:\order-gateway
.\.venv\Scripts\python.exe -B scripts\e2e_shopify_odoo.py
```

Docker and local service access was allowed outside the sandbox. The setup sequence
below is provided for reproduction; it was not rerun for this verification.

Full actual output (exit code **0**; no secret values appeared):

```text
Mode: synthetic
PASS: Compose services reachable
PASS: Required services running - api, worker, odoo, odoo-db, db
PASS: Gateway API reachable
PASS: Worker uses Odoo adapter - ERP_ADAPTER=odoo
PASS: Odoo API key configured and matches worker - credentials not displayed
PASS: Shopify signing secret configured and matches API
PASS: Gateway read authentication configured - ready
PASS: Worker Odoo URL configured
PASS: Odoo target is local - This proof requires the local Compose Odoo instance
PASS: Odoo reachable and authenticated
PASS: Product SKU BOOK uniquely mapped - matches=1
PASS: Product SKU PEN uniquely mapped - matches=1
Synthetic order name / webhook id: E2E-20261015T035016-800bebbdfaf1
PASS: Signed webhook accepted - status=RECEIVED
Gateway order id: ada7ccbb-c85c-45cd-a814-9d50d0cc4063
Gateway status: RECEIVED
Gateway status: PROCESSING
Gateway status: CONFIRMED
PASS: Gateway order CONFIRMED
PASS: Shopify customer, reference, currency and lines mapped correctly
PASS: Gateway source is Shopify
PASS: Exactly one Odoo sale order for gateway reference - count=1
PASS: Odoo sale order confirmed - state=sale
PASS: Odoo reference links to gateway order
PASS: Gateway confirmation audit links to Odoo id
PASS: Product SKU BOOK uniquely mapped - matches=1
PASS: Product SKU PEN uniquely mapped - matches=1
PASS: Odoo lines, quantities and unit prices match gateway
PASS: Odoo currency matches gateway
PASS: Odoo customer matches gateway customer
Odoo order id: 17; name: S00050
PASS: Identical replay returns same gateway order
PASS: Gateway stays CONFIRMED after replay
PASS: Replay creates no second Odoo sale order - count=1
Check | Result
Compose services reachable | PASS
Required services running | PASS
Gateway API reachable | PASS
Worker uses Odoo adapter | PASS
Odoo API key configured and matches worker | PASS
Shopify signing secret configured and matches API | PASS
Gateway read authentication configured | PASS
Worker Odoo URL configured | PASS
Odoo target is local | PASS
Odoo reachable and authenticated | PASS
Product SKU BOOK uniquely mapped | PASS
Product SKU PEN uniquely mapped | PASS
Signed webhook accepted | PASS
Gateway order CONFIRMED | PASS
Shopify customer, reference, currency and lines mapped correctly | PASS
Gateway source is Shopify | PASS
Exactly one Odoo sale order for gateway reference | PASS
Odoo sale order confirmed | PASS
Odoo reference links to gateway order | PASS
Gateway confirmation audit links to Odoo id | PASS
Product SKU BOOK uniquely mapped | PASS
Product SKU PEN uniquely mapped | PASS
Odoo lines, quantities and unit prices match gateway | PASS
Odoo currency matches gateway | PASS
Odoo customer matches gateway customer | PASS
Identical replay returns same gateway order | PASS
Gateway stays CONFIRMED after replay | PASS
Replay creates no second Odoo sale order | PASS
FINAL: PASS
```

| Check | Result |
| --- | --- |
| Compose services reachable | PASS |
| Required services running | PASS |
| Gateway API reachable | PASS |
| Worker uses Odoo adapter | PASS |
| Odoo API key configured and matches worker | PASS |
| Shopify signing secret configured and matches API | PASS |
| Gateway read authentication configured | PASS |
| Worker Odoo URL configured | PASS |
| Odoo target is local | PASS |
| Odoo reachable and authenticated | PASS |
| Product SKU BOOK uniquely mapped | PASS |
| Product SKU PEN uniquely mapped | PASS |
| Signed webhook accepted | PASS |
| Gateway order CONFIRMED | PASS |
| Shopify customer, reference, currency and lines mapped correctly | PASS |
| Gateway source is Shopify | PASS |
| Exactly one Odoo sale order for gateway reference | PASS |
| Odoo sale order confirmed | PASS |
| Odoo reference links to gateway order | PASS |
| Gateway confirmation audit links to Odoo id | PASS |
| Product SKU BOOK uniquely mapped | PASS |
| Product SKU PEN uniquely mapped | PASS |
| Odoo lines, quantities and unit prices match gateway | PASS |
| Odoo currency matches gateway | PASS |
| Odoo customer matches gateway customer | PASS |
| Identical replay returns same gateway order | PASS |
| Gateway stays CONFIRMED after replay | PASS |
| Replay creates no second Odoo sale order | PASS |
| Final result | PASS, exit 0 |

## Safety review

The script and this record were checked against configured credential values
from the local environment and `.env` files (including backups), and scanned for
recognizable API-key/token/private-key formats. Both files had **zero credential
value matches and zero credential-format matches**. Credential variable names
and header names are documentation, not exposed values. No secrets needed masking
in the actual passing output. A syntax check and the `E2E-` order/webhook prefix
checks also passed.

## Offline regression check

The normal suite was run on 2026-10-15 after unsetting the Odoo overrides in the
PowerShell process. `ODOO_LIVE` was also unset so the opt-in live test stayed skipped.
The existing fixtures select the dedicated `_test` database and mock adapter;
they may load Odoo connection settings from `.env` but do not enable the live test.
No existing application, test, README or other tracked documentation was edited.

```powershell
Remove-Item Env:ERP_ADAPTER -ErrorAction SilentlyContinue
Remove-Item Env:ODOO_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:ODOO_LIVE -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -B -m pytest -q
```

Actual result (exit code 0):

```text
284 passed, 1 skipped, 2 warnings in 193.53s (0:03:13)
```

The warnings concern Starlette/AnyIO deprecation and per-request test-client
cookies. This regression result does not add Mode B or live Odoo outage coverage.

## Read findings and script behavior

These details come from `app/main.py`, `app/services/shopify_webhooks.py`,
`app/workers/worker.py`, `app/adapters/odoo.py`, `scripts/odoo_seed.py`,
`docker-compose.yml`, and the existing Odoo, live Shopify, and demo notes.

- Route: `POST /webhooks/shopify/orders-create`. Signature header:
  `X-Shopify-Hmac-Sha256`, base64 HMAC-SHA256 over the exact raw request bytes,
  using `SHOPIFY_WEBHOOK_SECRET`. The API gets that setting through Compose.
- Delivery identity: `X-Shopify-Webhook-Id`, stored as the gateway idempotency key
  `shopify:<webhook id>`. Topic header: `X-Shopify-Topic: orders/create`.
- Gateway lookup: `GET /orders/{gateway_order_id}`. With `LEGACY_AUTH=key`, the
  script uses the existing `DEMO_API_KEY` convention for `X-API-Key`. It does not
  create API keys. An active operator, approver, or admin key can read an order.
- Products: exactly one active `product.product` per SKU in `default_code`
  (Odoo Internal Reference). Synthetic SKUs are `BOOK` and `PEN`, taken from
  the seed script's `SKUS`. Quantities are 2 and 3, prices 10.00 and 2.50.
  If a product is absent, the script calls the seed script's existing adapter
  helper `set_opening_stock(sku, 100)`. Existing products are left as they are;
  ambiguous products stop the run.
- Customers: the adapter looks up an email candidate with `ilike`, then compares
  the email case-insensitively for an exact match. It can also match exact name
  and phone. It creates a partner if neither matches. The synthetic customer is
  E2E-Shopify Customer, `E2E-shopify@example.com`.
- Existing orders: the worker passes the gateway UUID as `external_id`; the
  adapter searches `sale.order.client_order_ref = GW-<gateway UUID>`.
  The Shopify numeric id is separately stored as the gateway `external_ref`.
  Existing `sale`/`done` orders are reused; `draft`/`sent` orders are confirmed;
  cancelled orders cause a failure. The script checks the Odoo id against the
  gateway's `order.confirmed` audit event as well.
- Worker selection: `ERP_ADAPTER=odoo`, with `ODOO_BASE_URL` and `ODOO_API_KEY`
  required. Other existing settings are `ODOO_DB`, `ODOO_EXPECTED_CURRENCY`, and
  `ODOO_WAREHOUSE_CODE`. The script checks the running container, not merely
  the host's `.env`. It compares keys and signing secrets inside containers;
  only comparison booleans are returned, and secret values travel via stdin.
- Existing seed entry point: `python -m scripts.odoo_seed`. The script reuses
  its `.env` loading convention: host environment wins over `.env`.
- On Windows the script maps the worker URL `http://odoo:8069` to the profile's
  published `http://localhost:8069`. The worker continues to use Compose DNS.
  It uses the running worker's database, currency, and warehouse settings.
- Each synthetic run uses a unique `E2E-<UTC timestamp>-<random suffix>` order
  name, order number, and webhook id, with a separate numeric Shopify id.
  Real Shopify `order_number` is numeric; the synthetic order number is an
  intentionally prefixed test label. The gateway mapper consumes `id`.
- Synthetic customer names, email, address labels and line-item titles also use
  `E2E-`. Product SKUs stay `BOOK`/`PEN` to reuse the existing seed data. The script
  leaves the confirmed sample order in Odoo, together with its customer and any
  products/opening stock it had to seed. Existing products are reused. It performs
  no cleanup or cancellation; Odoo assigns its own `S...` order name and the
  adapter assigns `GW-<gateway UUID>` as its reference.
- Polling prints each observed status for up to 120 seconds. A rejection or
  delivery failure, including `RETRYING`, stops immediately with the gateway's
  recorded error. It never repairs or retries a failed delivery itself.
- Successful verification compares product ids, quantities and unit prices,
  currency, customer identity, confirmed Odoo state, and reference. It replays
  the identical bytes and headers, then reads both systems again. Exit 0 is
  reserved for all checks passing. Both modes require the local Compose stack.

## Exact PowerShell sequence to prepare and rerun

Prerequisites: Docker Desktop is running; the existing `.venv` has the project's
requirements; local Odoo 19 Community has database `gateway`, Sales and inventory
configured, a working existing `ODOO_API_KEY`, and the intended company currency.
The existing `.env` must contain the real `SHOPIFY_WEBHOOK_SECRET` and Odoo key.
Do not display `.env`, container environments, full Compose configuration, or
credentials. This sequence neither changes `.env` nor creates credentials.

Run in Windows PowerShell. These are preparation/rerun instructions, separate from
the exact verification commands above. The environment overrides switch the API and
worker to Odoo; recreating them makes those settings effective. Starting the
worker can process already queued orders, so use the intended local test database.

```powershell
Set-Location D:\order-gateway
$env:ERP_ADAPTER = "odoo"
$env:ODOO_BASE_URL = "http://odoo:8069"

docker compose --profile odoo up -d --build --force-recreate --wait --wait-timeout 120
if ($LASTEXITCODE -ne 0) { throw "Stack startup failed; stop here." }

docker compose --profile odoo exec -T api python -B -m scripts.odoo_seed
if ($LASTEXITCODE -ne 0) { throw "Odoo seed failed; stop here." }

.\.venv\Scripts\python.exe -B scripts\e2e_shopify_odoo.py
if ($LASTEXITCODE -ne 0) { throw "Shopify-to-Odoo proof failed; stop here." }
```

The existing container entry point applies database migrations. The seed command
seeds all existing seed SKUs with its idempotent `GW-SEED:<SKU>` markers. The new
script itself only ensures its two required products. Preserve the current Odoo
database and volumes; no reset is needed.

If gateway reads require a key and `DEMO_API_KEY` is not already available, use
this optional secure prompt **before running the proof**. Supply an existing
authorized key; never paste it into a command or print its value:

```powershell
$secureKey = Read-Host "Existing gateway read API key" -AsSecureString
$keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try { $env:DEMO_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer) }
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    $secureKey.Dispose()
}
```

An auth error, unknown or ambiguous SKU, currency mismatch, customer mapping
failure, or defect is a stop condition. Report the printed error; do not replace
credentials or change application code to make this proof pass.

## What this proves

A signed Shopify-format order reached a confirmed Odoo sale order once, and an identical replay created no second order, in a local test.

## What this does NOT prove

- A real Shopify store order into Odoo. Mode B has not been run here; synthetic input is
  locally generated and signed, without Shopify or a tunnel delivering it.
- Hosted Odoo: the verifier deliberately requires the local profile.
- Tax parity: the earlier Odoo run recorded a gateway total of 20.00 and an
  Odoo total of 23.00, a known Odoo tax difference. This check compares lines,
  quantities, prices and currency, not tax or grand totals.
- Odoo outage recovery, concurrent replay races, shipments or payment settlement.
- Mode B alone cannot establish the origin of an order: the operator must also
  confirm the matching Shopify dev-store order and webhook delivery.

## Manual live dev-store checklist

1. Complete the setup above and obtain a Mode A PASS. Keep the API and worker
   configured for Odoo. Ensure a valid `SHOPIFY_WEBHOOK_SECRET` from Shopify's
   Webhooks settings is already configured. After an intentional secret change,
   recreate API and worker; do not print or regenerate the secret for this test.
2. In a second PowerShell window start the existing tunnel approach:

   ```powershell
   cloudflared tunnel --url http://localhost:8002
   ```

3. In the Shopify development store, open **Settings > Notifications > Webhooks**.
   Configure **Order creation**, **JSON**, URL
   `https://<current-tunnel-host>/webhooks/shopify/orders-create`. A quick tunnel
   gets a new public hostname after restart; update the webhook URL accordingly.
4. Use products whose Shopify variant SKUs exactly match Odoo Internal References
   `BOOK` and `PEN`. Place a development-store order with quantities 2 and 3, a
   customer name and email attached, and the currency matching Odoo. The earlier
   live test used **Orders > Create order**, customer attached, marked as paid.
   Place an actual order; Shopify's test-notification button is not this proof.
5. Verify Shopify's delivery succeeded. In the gateway admin order detail, find
   the new `source=shopify` order whose external reference is the numeric Shopify
   order id. Copy its **gateway UUID**, not the Shopify id or Odoo id. Wait for
   `CONFIRMED`; Mode B verifies current state without polling or submitting.
6. In the original PowerShell window, run:

   ```powershell
   $gatewayOrderId = Read-Host "Gateway UUID for the real Shopify order"
   .\.venv\Scripts\python.exe -B scripts\e2e_shopify_odoo.py --check-order $gatewayOrderId
   if ($LASTEXITCODE -ne 0) { throw "Live order check failed; stop here." }
   ```

7. Save the Mode B output alongside the operator's Shopify order/delivery
   evidence, without secrets. Mode B checks Shopify source, gateway CONFIRMED,
   exactly one matching confirmed Odoo order, customer, lines, prices, currency,
   reference and audit id. It does not send or replay the real webhook. Stop at
   the first failure and report its exact error.
