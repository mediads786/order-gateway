# Case study: Order Gateway

## Problem

After more than 25 years in operations management, I wanted to build integration skills I can offer as a service. Order-to-ERP is where operations and systems meet. A shop or client system accepts an order, but delivery to the ERP can be delayed, rejected or uncertain. A client retry can create a duplicate. And someone has to be able to see what happened and recover it.

Order Gateway is my build of that problem. It is a learning project and a demonstration, not a production system, and this page says plainly what has and has not been verified.

## What I built

The gateway accepts an order over an API, validates it, calculates the total with exact decimal arithmetic, and stores the order, its delivery job and its audit events in one database transaction. A separate worker delivers jobs to an ERP through an adapter. There are two adapters: a mock ERP with fault injection, used to practise failure handling, and an Odoo 19 adapter.

Around that core are a signed Shopify webhook, a workflow registry with roles and approvals for risky actions (cancellations, stock adjustments, large orders), natural-language proposals that a person must confirm, and simple admin pages. The stack is Python, FastAPI, PostgreSQL and Docker Compose. GitHub Actions runs the full test suite on every push.

## Key decisions and why

**Queue in PostgreSQL.** The order and its job commit together, so an order can never be saved without a job. Workers claim jobs with row locks that skip rows already taken, so several workers can share the queue without processing a job twice. The cost is that it is not built for very high throughput, and it needs no extra broker to run.

**Idempotency keys.** Timeouts and webhook redeliveries repeat requests. The gateway stores each request's key and a hash of its body. The same key with the same body returns the original response, and the same key with a different body is refused. Shopify's webhook ID serves as its key.

**One strict order format, one mapper per source.** Every source is translated into the same canonical order, and every ERP has its own adapter. A new client changes the mapper or the adapter, not the core. Orders that do not fit are rejected with a short reason an operator can read.

**Append-only audit history.** Every state change writes an event, and the workflow event table rejects updates and deletes at the database level. Operators can read the history next to the current order state.

**AI proposes, people approve.** A proposal can only name a registered workflow with schema-checked input. It cannot call the ERP. A person confirms it, and the normal permission and approval rules then apply.

## How failure is handled

Temporary ERP failures (timeouts, HTTP 429, HTTP 5xx) are retried with exponential backoff, jitter, a cap and a maximum number of attempts. Permanent errors, or running out of attempts, move the order to a dead state, and an operator can queue it again. Jobs stuck in processing are recovered.

I demonstrate this with a script against the mock ERP. It submits an order, replays the same request and gets the same order back, then makes the ERP return HTTP 500 for a second order. That order goes to retrying, I clear the fault, and the next attempt confirms it. The audit trail shows every step. The ERP in that run is simulated, but the retry and recovery code is what a real ERP would exercise.

## What was verified

- The test suite runs against a real PostgreSQL database and passes in CI on every push.
- A real order from a Shopify development store, delivered by signed webhook, reached the confirmed state in the mock ERP.
- An end-to-end run against a local Odoo 19: an order submitted to the gateway was delivered by the worker and appears in Odoo as a confirmed sale order, and a governed cancellation (requested by one user, approved by another) turned it into a cancelled sale order in Odoo. A second run applied a governed stock adjustment and then signed shipments: Odoo stock moved by exactly the expected amounts, a replayed shipment changed nothing, a reused shipment id with a different body was refused, and a shipment larger than the available stock was refused. An earlier probe had already confirmed the individual Odoo API calls the adapter relies on.
- The failure-and-recovery demo above runs end to end.

## What is not done yet

- Cloud deployment.
- Failure handling against Odoo (for example Odoo going down mid-delivery) has not been exercised, and the runs used a local Odoo only.
- The optional AI proposer has not been recorded as tested against the live API.
- The admin pages use one shared token. Per-user identity is needed before real use.

## What I would do next

First, test failure handling against Odoo, for example by stopping it mid-delivery. Then deploy it, add per-user admin identity, and add alerting so a dead order notifies someone instead of waiting to be noticed.

For a real client, I would start with their side of the connection: what they send, in what format, how it is authenticated, which field is unique per order, and what the ERP's API allows. Those answers decide the mapper and the adapter, and the rest of the design stays the same.
