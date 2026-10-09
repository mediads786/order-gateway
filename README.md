# Order Gateway

![CI](https://github.com/mediads786/order-gateway/actions/workflows/ci.yml/badge.svg)


- An idempotent order intake and delivery service that stores orders in PostgreSQL, retries ERP delivery, and provides an operations view.

![Demo: ERP failure, retries, recovery](docs/demo.gif)

## What it does

- Delivers orders reliably to an ERP through a worker.
- Accepts idempotent canonical orders and Shopify `orders/create` webhooks.
- Retries temporary ERP failures and records exhausted jobs as dead letters.
- Keeps an append-only audit trail for orders and shipments.
- Supports a mock ERP by default, an Odoo 19 adapter, and signed shipment adjustments.
- Registers governed workflows with hashed API keys, role checks, request IDs, and an append-only audit trail.

- Supports a mock ERP by default, an Odoo 19 adapter, and signed shipment adjustments.

Design rationale: [docs/design-decisions.md](docs/design-decisions.md)

## Architecture

```mermaid
flowchart LR
  subgraph Sources
    W[Web / manual<br/>POST /orders]
    S[Shopify webhook<br/>HMAC verified]
    SH[Shipment event<br/>POST /shipments, HMAC]
  end
  subgraph Gateway
    API[FastAPI intake<br/>validate + idempotency]
    GOV[Workflow registry<br/>API-key governance]
    PG[(PostgreSQL<br/>orders, jobs, audit)]
    WK[Worker<br/>SKIP LOCKED, retries, dead letters]
    AD[Adapter interface]
    UI[Admin pages<br/>/admin]
  end
  subgraph ERP side
    M[Mock ERP<br/>fault injection]
    O[Odoo 19 JSON-2]
  end
  W --> API
  S --> API
  SH --> API
  GOV --> PG
  GOV --> API
  API --> PG
  PG --> WK
  WK --> AD
  AD --> M
  AD --> O
  SH -. stock adjustment .-> O
  UI --> PG
```

The API validates and stores orders and idempotency records in one PostgreSQL transaction. Valid orders create a queued job and audit event atomically. A separate worker claims jobs with `FOR UPDATE SKIP LOCKED`, calls the configured ERP adapter, and records retry or completion events. Shopify orders are authenticated from their original request bytes before mapping. Shipment requests are signed and applied synchronously through the Odoo adapter. Workflow requests use named registry entries and database-backed API-key roles; every authenticated request gets a request ID and append-only workflow audit events. The server-rendered admin pages read the same order, job, shipment, and workflow audit tables.

```mermaid
stateDiagram-v2
  [*] --> RECEIVED: valid intake
  [*] --> REJECTED: invalid intake
  RECEIVED --> QUEUED: job created
  RECEIVED --> PENDING_APPROVAL: governed total over threshold
  PENDING_APPROVAL --> APPROVED: approver decision
  APPROVED --> QUEUED: approval commits job
  PENDING_APPROVAL --> CANCELLED: rejected approval
  QUEUED --> PROCESSING: worker claim
  PROCESSING --> CONFIRMED: ERP success
  PROCESSING --> RETRYING: temporary failure
  RETRYING --> PROCESSING: retry due
  PROCESSING --> FAILED_DEAD: permanent failure or attempts exhausted
  FAILED_DEAD --> QUEUED: manual retry
```

## Quickstart (10 minutes)

Prerequisites: Docker Desktop, Git, PowerShell, and about 2 GB of RAM for the default demo.

From a fresh clone, run these commands in the repository root. Create a 32-character token using the shown PowerShell expression, then paste it into `.env` as `ADMIN_TOKEN` before starting Compose.

```powershell
Copy-Item .env.example .env
notepad .env
docker compose up -d --build
```

Token generator:

```powershell
-join ((48..57) + (97..122) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
```

The API is at `http://localhost:8002`; open the admin page at `http://localhost:8002/admin`, API docs at `http://localhost:8002/docs`, and health at `http://localhost:8002/health`. The API container runs `alembic upgrade head` before Uvicorn starts; the worker waits for the API healthcheck. Sign in with the `ADMIN_TOKEN` value.

Send a first order from PowerShell:

```powershell
$body = '{"source":"manual","external_ref":"quickstart-1","customer":{"name":"Ada","email":"ada@example.com"},"currency":"USD","lines":[{"sku":"BOOK","qty":2,"unit_price":"19.99"}]}'
$key = [guid]::NewGuid().ToString()
Invoke-RestMethod -Method Post -Uri http://localhost:8002/orders -Headers @{"Idempotency-Key"=$key} -ContentType "application/json" -Body $body
```

Prices must be JSON strings. Numeric prices are rejected by design to avoid converting binary floats into money.

To run the real-PostgreSQL tests from the host, create the local environment and install the pinned test dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then run the tests after Compose is up:

```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

Test result placeholder: **232 tests passed, 1 skipped** (replace `N` with your observed output).

The test fixture reads `TEST_DATABASE_URL` from the environment or `.env`, creates the named test database if missing, checks that its name ends in `_test`, and sets `DATABASE_URL` to it before importing the app.

## Demo

Follow the [3-minute operations demo](docs/demo.md), the [about-60-second governance demo](docs/demo.md#governance-demo-about-60-seconds), the [about-90-second approval demo](docs/demo.md#approval-demo-about-90-seconds), or the [about-60-second cancellation demo](docs/demo.md#cancellation-demo-about-60-seconds).

## Admin pages

`/admin` provides a status-filtered order list with counts, a detail page with lines, job state, shipments and the oldest-first audit trail, and a retry button for `FAILED_DEAD` orders. The shared `ADMIN_TOKEN` is compared with a constant-time check; the browser receives a signed `gw_admin` cookie, never the token itself. Admin responses disable caching and framing and use a restrictive content security policy. State changes use POST forms, and `SameSite=Strict` cookies provide the v1 CSRF protection. `ADMIN_TOKEN` must contain at least 16 characters; otherwise all admin routes return `503 admin_disabled`.

The admin has one shared identity, so an admin retry audit event cannot identify an individual, and there is no login rate limiting. The original order endpoints are open by default; set `LEGACY_AUTH=key` to require API keys with role checks. The admin retry button remains protected by the shared admin cookie. Cookies are marked `Secure` only when served over HTTPS; plain local HTTP does not set that attribute.

## Workflow governance

Migration `0006_workflow_governance` adds database-backed API keys and append-only `workflow_events`. API keys are stored as SHA-256 hashes; the raw key is printed only at creation. `operator`, `approver`, and `admin` are the supported roles. Send the key in `X-API-Key`; callers cannot select their own role. `GET /workflows` returns the registered schemas. `POST /workflows/{name}/requests` accepts `{"input": {...}}`, and executable order intake also requires `Idempotency-Key`. Responses include a `request_id` in both the JSON body and `X-Request-Id` header. Unauthorized calls do not create workflow events; authenticated denials and outcomes are audited without storing raw request bodies or API keys.

The registry contains `create_order` (low risk), `adjust_stock` (high risk, always approved), and `cancel_order` (medium risk, always approved). Operators and admins may request these workflows. Only approvers and admins may decide, and a key cannot decide its own request.

### Approvals

Governed `create_order` requests whose computed `Decimal` total is strictly greater than `APPROVAL_THRESHOLD` are stored as `PENDING_APPROVAL` without a job. The default threshold is `1000.00`; equality does not require approval, and currency is ignored by the comparison. A different approver or admin can approve, which queues the order, or reject with a reason, which sets it to `CANCELLED`. Every `adjust_stock` request waits for approval before the adapter is called. A failed stock adjustment is recorded as `EXECUTION_FAILED` and is not retried automatically.

`cancel_order` takes `{"order_id": "<uuid>", "reason": "..."}` and always creates a pending approval; it never calls the ERP at request time. Eligibility is checked when requested and checked again under row locks after approval:

| Condition | Result |
|---|---|
| Order not found | `404 order_not_found` |
| `CANCELLED` or `REJECTED` | `409 order_not_cancellable` |
| `PENDING_APPROVAL` / `APPROVED` / `PROCESSING` | `409 order_pending_approval` / `order_in_progress` |
| `CONFIRMED` with ERP ID; or fresh `RECEIVED`/`QUEUED` with a queued job, zero attempts, and no ERP ID | Eligible; ERP or local cancellation respectively |
| Retrying/dead job with attempts and no ERP ID; all other states | `409 erp_state_unknown` / `order_not_cancellable` |

The gateway refuses cancellation when ERP state is unknown because a timed-out create may already have made an ERP record. Cancellation events are `workflow.requested`, `workflow.approval_requested`, `workflow.approved`, then `order.cancelled` and `workflow.executed` (or `workflow.failed`). Rejection leaves the order untouched. The admin approvals summary shows the target order and reason.

The API endpoints are `GET /approvals?status=PENDING&page=1`, `GET /approvals/{approval_id}`, and `POST /approvals/{approval_id}/decision`. Approvers and admins can see all approvals; operators can see only requests they made. The admin approval page is read-only; decisions require an API key so requester identity and the self-approval rule remain enforceable.

```powershell
$ApproverKey = Read-Host "Approver API key"
$Pending = Invoke-RestMethod -Uri "http://localhost:8002/approvals?status=PENDING" -Headers @{ "X-API-Key" = $ApproverKey }
$ApprovalId = $Pending[0].approval_id
$Decision = '{"decision":"approve","reason":"Reviewed"}'
Invoke-RestMethod -Method Post -Uri "http://localhost:8002/approvals/$ApprovalId/decision" -Headers @{ "X-API-Key" = $ApproverKey } -ContentType "application/json" -Body $Decision
```

New order audit events are `order.pending_approval`, `order.approved`, and `order.cancelled`. Workflow audit events include `workflow.approval_requested`, `workflow.approved`, and `workflow.approval_rejected`; existing `workflow.executed`, `workflow.denied`, and `workflow.failed` events record the result or refusal.

### Natural-language proposals

The proposal safety model is:

1. A proposal is a draft, not a workflow request.
2. The proposer can select only executable workflows the caller may request.
3. The selected input is validated by that workflow's schema.
4. Confirmation uses the normal governed request path with the requester's own key.
5. Role checks, approval thresholds, and self-approval rules still apply.

The default rule proposer recognizes only these patterns (case-insensitive):

- `add|remove|adjust <int> (of|to|for|from)? <SKU> ... reason: <text>`; the reason is everything after `reason:` and `add`/`remove` determine the sign.
- `order <qty> <SKU> at <price> for <name>, phone <digits>`; currency comes from `DEFAULT_CURRENCY` when set, otherwise `PKR`.
- `cancel order <uuid> reason: <text>`; confirmation creates the same governed cancellation approval as a direct request.

`POST /proposals` creates a draft. Use `GET /proposals` or `GET /proposals/{id}` to review it, then `POST /proposals/{id}/confirm` or `/discard`. Only the creating key can confirm or discard; approvers and admins can read all proposals. Proposal audit events are `workflow.proposed`, `workflow.proposal_invalid`, `workflow.proposal_confirmed`, and `workflow.proposal_discarded`.

```powershell
$OperatorKey = Read-Host "Operator API key"
$Body = '{"text":"order 2 BOOK at 19.99 for Ada, phone 03001234567"}'
$Proposal = Invoke-RestMethod -Method Post -Uri http://localhost:8002/proposals -Headers @{ "X-API-Key" = $OperatorKey } -ContentType "application/json" -Body $Body
Invoke-RestMethod -Uri "http://localhost:8002/proposals/$($Proposal.proposal_id)" -Headers @{ "X-API-Key" = $OperatorKey }
Invoke-RestMethod -Method Post -Uri "http://localhost:8002/proposals/$($Proposal.proposal_id)/confirm" -Headers @{ "X-API-Key" = $OperatorKey }
```

The proposer only converts text into a schema-checked draft; it has no credentials or execution tools. The normal gateway performs all writes after the person confirms.

Create, list, or deactivate keys from the repository root after migrations have run:

```powershell
python -m scripts.api_keys create --name warehouse-operator --role operator
python -m scripts.api_keys list
python -m scripts.api_keys deactivate --name warehouse-operator
```

Copy the created key securely; it cannot be retrieved later. Example requests:

```powershell
$Key = Read-Host "API key"
Invoke-RestMethod -Uri http://localhost:8002/workflows -Headers @{ "X-API-Key" = $Key }
$Body = '{"input":{"source":"manual","external_ref":"workflow-demo","customer":{"name":"Ada","email":"ada@example.com"},"currency":"USD","lines":[{"sku":"BOOK","qty":1,"unit_price":"19.99"}]}}'
Invoke-RestMethod -Method Post -Uri http://localhost:8002/workflows/create_order/requests -Headers @{ "X-API-Key" = $Key; "Idempotency-Key" = "workflow-demo-1" } -ContentType "application/json" -Body $Body
```

The admin navigation includes `/admin/workflow-events`, a read-only, newest-first view with event-type filtering. It escapes event details before rendering. Workflow audit events are append-only at the database level; `TRUNCATE` remains available to the test fixture.

## How it works

### Idempotency and retries

`POST /orders` requires `Idempotency-Key`. The gateway hashes the request body and serializes concurrent same-key requests with a PostgreSQL advisory transaction lock. The same key and body replays the saved response; a different body returns `409`.

The worker claims one due job using `FOR UPDATE SKIP LOCKED`, increments attempts, and calls the ERP outside the claim transaction. Retryable failures use capped exponential backoff with jitter. The defaults are at most 5 attempts, 2 seconds base, and 60 seconds cap. HTTP 429, 5xx, timeouts and transport errors retry; permanent HTTP/business errors do not. An exhausted or permanent failure marks the order `FAILED_DEAD`. Stale `PROCESSING` jobs are recovered after `STALE_JOB_SECONDS`.

The audit trail includes `order.received`, `order.rejected`, `order.queued`, `order.processing`, `order.retrying`, `order.confirmed`, `order.failed`, `order.recovered_stale`, and `order.requeued`, with attempt and error details as applicable. Shipment events are `shipment.received`, `shipment.applied`, and `shipment.failed`.

### Shopify webhook

`POST /webhooks/shopify/orders-create` verifies the base64 HMAC-SHA256 signature over the raw request body, then maps the `orders/create` webhook to canonical order fields. The Shopify webhook ID is the gateway idempotency key. Only the mapped customer/contact, currency, line SKU, quantity and string unit price are forwarded; Shopify totals, taxes, discounts and shipping are ignored. Missing SKU or contact rejects the order.

### Odoo adapter and shipments

Odoo uses the JSON-2 API with an API key bearer token. XML-RPC/JSON-RPC are not used because they were deprecated in Odoo 19 and parts are removed in Odoo 20. Order creation searches `sale.order.client_order_ref` before create so a retry after a lost response reuses the same ERP order.

| Gateway field | Odoo field | Mapping |
| --- | --- | --- |
| order external key | `sale.order.client_order_ref` | `GW-` plus the worker's gateway UUID external ID |
| customer | `res.partner` | Search case-insensitive email; otherwise exact name and phone; create when missing |
| currency | — | Must equal `ODOO_EXPECTED_CURRENCY` |
| `lines[].sku` | `product.product.default_code` | Exact active match; unknown or ambiguous SKU is permanent failure |
| `lines[].qty` | `order_line.product_uom_qty` | Integer quantity |
| `lines[].unit_price` | `order_line.price_unit` | `Decimal` converted to JSON number only at the Odoo boundary |
| lines | `order_line` | Odoo ORM command list `[[0, 0, {...}], ...]` |

`POST /shipments` requires an HMAC-SHA256 signature in `X-Gateway-Signature`. The shipment row and `shipment.received` event are stored before the synchronous stock adjustment. Odoo inventory markers make each line idempotent; a repeated applied shipment replays the stored response. This PowerShell example reads the secret from `.env` without printing it:

```powershell
$OrderId = Read-Host "Confirmed gateway order UUID"
$ShipmentId = "SHP-$([guid]::NewGuid())"
$ShipmentSecret = (Get-Content .env | Where-Object { $_ -match '^SHIPMENT_WEBHOOK_SECRET=' } | Select-Object -First 1) -replace '^SHIPMENT_WEBHOOK_SECRET=', ''
$ShipmentBody = @{ shipment_id = $ShipmentId; order_id = $OrderId; lines = @(@{ sku = "BOOK"; qty = 1 }) } | ConvertTo-Json -Compress -Depth 5
$Hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($ShipmentSecret))
$Signature = [Convert]::ToBase64String($Hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($ShipmentBody)))
Invoke-RestMethod -Method Post -Uri http://localhost:8002/shipments -ContentType "application/json" -Headers @{ "X-Gateway-Signature" = $Signature } -Body $ShipmentBody
```

Reuse this signing example for [the optional Odoo demo](docs/demo.md#optional-odoo-demo).

To start the optional Odoo profile, initialize its own database and then run the Odoo service:

```powershell
docker compose --profile odoo up -d odoo-db
docker compose --profile odoo run --rm odoo odoo -d gateway -i base,sale_management,stock --without-demo=all --stop-after-init
docker compose --profile odoo up -d odoo
```

Wait for `http://localhost:8069`, create an API key under Preferences → Account Security, and put it in `.env` as `ODOO_API_KEY`. Set `ERP_ADAPTER=odoo`, `ODOO_BASE_URL=http://odoo:8069`, and `ODOO_DB=gateway` in `.env`, then run `docker compose --profile odoo up -d --build`. Seed the fixture SKUs and opening inventory with:

```powershell
$env:ODOO_BASE_URL = "http://localhost:8069"
.\.venv\Scripts\python.exe scripts\odoo_seed.py
```

The [Odoo API notes](docs/odoo-notes.md) contain the verified local request/response observations.

For the opt-in live smoke test, seed a SKU and set the host-reachable Odoo URL:

```powershell
$env:ODOO_BASE_URL = "http://localhost:8069"
$env:ODOO_LIVE = "1"
.\.venv\Scripts\python.exe -m pytest -m live_odoo -v
```

## Configuration

| Variable | Default | Meaning | Secret? |
| --- | --- | --- | --- |
| `DATABASE_URL` | Compose PostgreSQL URL | Application database | No (local dev credentials) |
| `TEST_DATABASE_URL` | `order_gateway_test` on port 5433 | Isolated pytest database; name must end `_test` | No (local dev credentials) |
| `ERP_ADAPTER` | `mock` | `mock` or `odoo` | No |
| `ERP_BASE_URL` | `http://mock_erp:9001` | Mock ERP endpoint | No |
| `ERP_TIMEOUT_SECONDS` | `5` | ERP request timeout | No |
| `WORKER_POLL_INTERVAL_SECONDS` | `1` | Worker idle polling interval | No |
| `RETRY_BASE_SECONDS` | `2` | Exponential retry base | No |
| `RETRY_MAX_SECONDS` | `60` | Retry delay cap | No |
| `MAX_ATTEMPTS` | `5` | Maximum worker attempts | No |
| `STALE_JOB_SECONDS` | `120` | Stale claim recovery threshold | No |
| `SHOPIFY_WEBHOOK_SECRET` | empty | Shopify webhook HMAC secret | Yes |
| `SHIPMENT_WEBHOOK_SECRET` | empty | Shipment HMAC secret | Yes |
| `ADMIN_TOKEN` | empty | Admin login token; minimum 16 characters | Yes |
| `APPROVAL_THRESHOLD` | `1000.00` | Governed order total above which approval is required | No |
| `LEGACY_AUTH` | `off` | Set to `key` to protect original order endpoints with API-key roles | No |
| `PROPOSER` | `rule` | `rule` or optional `anthropic` proposer | No |
| `DEFAULT_CURRENCY` | `PKR` | Currency used by the rule-based order proposal when set | No |
| `ANTHROPIC_API_KEY` | empty | Anthropic Messages API key when `PROPOSER=anthropic` | Yes |
| `PROPOSER_MODEL` | `claude-sonnet-5-5` | Anthropic model name | No |
| `PROPOSER_TIMEOUT_SECONDS` | `20` | One model-call timeout; no retries | No |
| `PROPOSAL_DAILY_LIMIT` | `50` | Maximum proposals per key per UTC day | No |
| `PROPOSAL_TEXT_MAX_CHARS` | `1000` | Maximum submitted sentence length | No |
| `ODOO_BASE_URL` | empty | Odoo JSON-2 base URL | No |
| `ODOO_DB` | `gateway` | Odoo database header | No |
| `ODOO_API_KEY` | empty | Odoo bearer API key | Yes |
| `ODOO_EXPECTED_CURRENCY` | `USD` | Accepted Odoo order currency | No |
| `ODOO_WAREHOUSE_CODE` | `WH` | Odoo stock warehouse | No |
| `ODOO_IMAGE` | `odoo:19.0` | Pinned Odoo image tag | No |
| `ODOO_PG_USER` | `odoo` | Odoo-only PostgreSQL user | No |
| `ODOO_PG_PASSWORD` | placeholder in `.env.example` | Odoo-only PostgreSQL password | Yes |
| `ODOO_LIVE` | unset | Set to `1` to run the opt-in live Odoo smoke test | No |

## What is verified and what is not

- Automated test count: `232 tests passed, 1 skipped`.
- The opt-in Odoo smoke test requires a running seeded Odoo instance and `ODOO_LIVE=1`.
- Shopify live-store test: not yet done; mapping fixtures and signature behavior are tested locally.
- Odoo API calls are documented in [docs/odoo-notes.md](docs/odoo-notes.md); the sale-order create flow should also be checked with the opt-in live smoke test for the Odoo instance in use.

## Known limitations

- Shopify orders with no usable customer name or no email or phone (for example an order created without a customer or address) cannot be mapped and are stored as REJECTED. Proven in a live test; see docs/shopify-live-test.md.
- Shopify `total_price` is ignored; the gateway computes the total from lines.
- A missing Shopify SKU rejects the order.
- The same Shopify order under a different webhook ID creates a second order.
- Only one Shopify topic is supported.
- Two simultaneous first orders for one new Odoo customer can create duplicate partner records.
- Money converts to float only at the Odoo JSON boundary.
- Counted-quantity stock adjustments can race with other Odoo stock changes.
- There is no cumulative shipped-versus-ordered quantity check.
- Shipments are synchronous and use one warehouse.
- Order currency must match `ODOO_EXPECTED_CURRENCY`.
- Odoo Online plans may restrict external API access.
- `POST /orders`, `GET /orders/{id}`, and `POST /orders/{id}/retry` are open by default; set `LEGACY_AUTH=key` to require API keys with role checks. Shopify and shipment webhooks remain HMAC-authenticated and are not role-based; admin pages still use the single shared `ADMIN_TOKEN`. The original order routes do not use governed approvals.
- The threshold ignores currency, pending approvals do not expire, and decisions are available through the API only.
- Failed `adjust_stock` approval executions are not retried automatically; a requester must submit a new request.
- Rule-based natural-language matching supports only the two documented patterns; the optional Anthropic proposer is not tested against the live API here.
- Confirmed proposals are not retried; submit a new proposal after a governed failure. Proposal text is stored in `proposals` and retention is not managed. Daily proposal limits are per API key, not per IP.
- Retrying or dead orders without an ERP ID are refused as `erp_state_unknown` and require manual handling. A crash after a successful ERP cancellation but before the gateway update leaves the approval `APPROVED`; the idempotent adapter call can be rerun manually. Odoo cancellation is verified with fixtures only (the cancellation calls are marked UNVERIFIED LIVE in `docs/odoo-notes.md`); partial cancellations are unsupported.
- Approval state commits and workflow audit events use separate transactions, leaving a small crash window where the state is committed before its corresponding workflow event.
- Confirm holds a proposal row lock and a database connection while it runs the governed flow, which needs a second connection; with many simultaneous confirms the connection pool (default 5 + 10 overflow) is the limit.
- Admin uses one shared token, has no per-user identity and no login rate limiting.
- The API retry endpoint has no authentication in v1.
- The admin cookie is not marked `Secure` over plain HTTP.

## Roadmap

- Layer 2: workflow registry, roles, approvals, and governed stock adjustment are implemented.
- Layer 2: governed order cancellation is implemented.
- Layer 3, part 1: natural-language proposals that can only propose changes is implemented.

## Project layout

```text
app/                 FastAPI app, adapters, admin pages, services and worker
migrations/          Alembic schema history
mock_erp/            In-memory mock ERP and fault injection
tests/               PostgreSQL-backed tests and recorded Odoo fixtures
docs/                Odoo notes, demo script and portfolio checklist
docker-compose.yml   Local services and optional Odoo profile
```
