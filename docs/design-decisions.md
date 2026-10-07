# Design decisions

Why Order Gateway is built the way it is. Each section states the decision, the reason, and what it costs. For setup and usage see the [README](../README.md); for known gaps see its "Known limitations" section.

## 1. What problem it solves

Orders arrive from several channels (API, Shopify webhooks) and must reach an ERP exactly once, even when the ERP is slow, down, or the same request is sent twice. Operators must be able to see what happened to every order and recover failures without touching the database.

The gateway therefore does five things: accept an order safely, queue it durably, deliver it with retries, record every step, and expose a small operations view.

## 2. Architecture in one paragraph

A FastAPI service validates input and stores the order, its idempotency record, a job and an audit event in **one PostgreSQL transaction**. A separate worker claims jobs, calls an ERP adapter, and records the outcome. Adapters hide ERP differences behind one interface (a mock ERP with fault injection, and Odoo 19). Server-rendered admin pages read the same tables.

## 3. Decisions

| Topic | Decision | Why | Trade-off |
|---|---|---|---|
| Queue | A `jobs` table in PostgreSQL, claimed with `FOR UPDATE SKIP LOCKED` | No extra infrastructure; the order and its job are written atomically, so an order can never exist without a job. Several workers can run without processing the same job twice. | Lower throughput than a broker; fine for order volumes, not for streaming workloads. |
| Idempotency | Required `Idempotency-Key` header plus a hash of the request body, stored in the database; concurrent same-key requests are serialized with a PostgreSQL advisory transaction lock | Clients and webhook senders retry. The same key and body replays the original response; the same key with a different body is a `409`. | Keys are stored indefinitely in v1. |
| Money | `Decimal` everywhere; prices travel as JSON **strings**; the server computes the total; currency is required | Binary floats cause rounding errors. Numeric JSON prices are rejected by design. The client never decides the total. | Slightly less convenient for clients. Float appears only at the Odoo JSON boundary, which requires numbers. |
| Time | UTC everywhere | Avoids timezone bugs. | None worth noting. |
| Retries | Capped exponential backoff with jitter: at most 5 attempts, 2 s base, 60 s cap | Retrying immediately overloads a struggling ERP; jitter prevents synchronized retries. | A permanently failing order takes roughly 20 to 30 seconds to be declared dead with the defaults. |
| What is retried | Timeouts, transport errors, HTTP 429, HTTP 5xx, and a 2xx response without an ERP order id. Other 4xx responses are permanent. | A validation error will fail identically every time; retrying it only delays the operator seeing it. | A misclassified error either wastes attempts or dead-letters too early. Odoo errors are classified by the error name as well as the status, because Odoo returns some validation errors as HTTP 500. |
| Dead letters | After the last attempt (or a permanent failure) the order becomes `FAILED_DEAD`. Only a manual action requeues it. | Automatic infinite retry hides problems. A person decides when the ERP is healthy again. | Needs an operator. The admin page has a Retry button. |
| Stale jobs | A job stuck in `PROCESSING` past `STALE_JOB_SECONDS` is recovered or dead-lettered | A worker can crash after claiming a job. | The recovery threshold must exceed the longest legitimate ERP call. |
| Audit | Append-only `audit_events`: every state change and every attempt writes an event; there are no update or delete code paths | You can answer "what happened to order X?" from the database alone. | Table grows; archiving is a later concern. |
| State machine | Fixed names: `RECEIVED`, `QUEUED`, `PROCESSING`, `CONFIRMED`, `RETRYING`, `FAILED_DEAD`, `REJECTED` | A small, closed set is easy to test and reason about. | New states need a deliberate change. |
| Invalid input | Invalid orders are stored as `REJECTED` and answered with `422` | Rejections are visible in the audit trail, and replays of a rejected request return the same answer. | Garbage input is stored; the raw payload is kept for diagnosis. |
| Config and secrets | Environment variables, `.env.example`, no secrets in code | Standard twelve-factor practice. | Secrets must be managed by whoever deploys. |
| Testing | pytest against a real PostgreSQL in Docker; no mocked database | Queue locking, transactions and idempotency races only show up against a real database. The suite includes concurrency tests, such as two workers processing twenty orders once each and ten parallel requests with one key. | Tests need Docker. |
| Packaging | Docker Compose, one command; migrations run when the API starts | A newcomer can run it without installing anything else. | Compose is for local and demo use, not production orchestration. |

## 4. Order intake

1. The caller sends the canonical order (source, optional external reference, customer with at least a phone or email, currency, one or more lines of SKU, integer quantity and decimal unit price) with an `Idempotency-Key`.
2. Validation, the idempotency check, the order, its lines, the job and the audit events are written in one transaction. If any step fails, nothing is stored.
3. The response is stored with the key, so a replay returns exactly what the first call returned.

**Shopify** orders enter through a webhook. The HMAC-SHA256 signature is verified over the **raw request bytes** before the payload is parsed (re-serialized JSON would not match the signature). Shopify's webhook id header becomes the idempotency key, so Shopify's at-least-once delivery cannot create duplicates. Only customer and contact, currency, SKU, quantity and unit price are mapped; Shopify's totals, taxes and discounts are ignored and the gateway computes its own total.

## 5. Delivery to the ERP

The worker claims one due job, increments the attempt counter, and calls the adapter **outside** the claim transaction so a slow ERP does not hold database locks. The outcome is then written: success moves the order to `CONFIRMED`; a retryable failure schedules the next attempt; a permanent failure or the last attempt moves it to `FAILED_DEAD`.

Because a response can be lost after the ERP already created the order, delivery must be safe to repeat. The adapter always looks up its own external key before creating anything, and the mock ERP rejects duplicate external ids, so a retry after a lost response confirms the existing ERP order instead of creating a second one.

## 6. ERP adapters

All adapters implement one interface, so the worker does not know which ERP it talks to; `ERP_ADAPTER` selects one.

- **Mock ERP.** A small separate service that can be told to fail (HTTP 500, 429, 422), be slow, or time out. This is how failure handling is tested and demonstrated.
- **Odoo 19.** Uses the JSON-2 API with an API key (the older XML-RPC and JSON-RPC interfaces are deprecated). An order is matched by `sale.order.client_order_ref`, customers by email or name and phone, and products by `default_code`. Unknown or ambiguous SKUs are permanent failures. Request and response shapes were verified against a live instance and are recorded in [odoo-notes.md](odoo-notes.md).

**Shipments.** A signed `POST /shipments` request adjusts stock in Odoo. Each line is marked with a reference string stored on the stock move, so a repeated shipment is detected and applied once. Odoo's inventory-apply action is not a no-op when called twice, so the adapter always writes the counted quantity and applies it as one unit and then re-reads the on-hand quantity to verify.

## 7. Operations view

Server-rendered HTML with no JavaScript and no new dependencies: an order list with status counts, a detail page with lines, job state and the audit trail, and a Retry button for dead orders. Access uses one shared admin token; the browser receives a signed, `HttpOnly`, `SameSite=Strict` cookie, never the token itself. State-changing actions are POST only. If no token is configured the admin is disabled rather than open. Every database value is HTML-escaped, and responses carry a restrictive content security policy.

## 8. Deliberate scope limits

Not built, on purpose: multi-tenant SaaS, billing, a message broker, microservices, payments or accounting logic, direct language-model access to ERP calls, and more than one real ERP before the first was finished. The shared admin token and the unauthenticated API retry endpoint are known v1 limits; the planned next layer adds roles and approvals.

## 9. What is verified

- 123 automated tests pass against real PostgreSQL (1 more, the live Odoo test, is opt-in and passed locally).
- End to end with Odoo 19: an order created through the API reached `CONFIRMED` in Odoo, a signed shipment applied once, and re-sending the order or the shipment created no duplicates.
- The Shopify webhook is covered by recorded payloads and signature tests; a live test against a Shopify development store has not been done yet.

## 10. Roadmap

1. Workflow registry and role-based permissions: every request authenticated and checked against approved workflows.
2. Approvals: risky actions wait for a second person, and both decisions are audited.
3. Natural-language requests that can only propose registered workflows, never call the ERP directly.
