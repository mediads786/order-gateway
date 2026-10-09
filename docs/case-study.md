# Case study: Order Gateway

## Problem

After more than 25 years in operations management, I wanted to deepen my integration engineering skills and build work I can use when offering integration services. I chose an order-to-ERP problem because an order may be accepted by one system while delivery to another is delayed, rejected, or uncertain. A client retry can create a duplicate. Operators need to see what happened and understand how to recover.

I built Order Gateway to learn and create a portfolio example, not to claim that every production requirement is solved. Its core path accepts a canonical order, persists it with a delivery job, calls an ERP through an adapter, and records events. Shopify webhooks, governed actions, proposals, and Odoo exercise related concerns in the same application.

## What I built

The FastAPI application validates orders, calculates totals with `Decimal`, and writes order, lines, job, idempotency record, and initial audit events in a transaction. A separate worker delivers due jobs to the in-memory mock ERP or configured Odoo adapter. Admin pages show order and job state, audit history, approvals, workflow events, and proposals. (`app/main.py`, `app/services/orders.py`, `app/workers/worker.py`, `docker-compose.yml`.)

Shopify orders enter through a signed webhook and map supported customer and line fields; unmappable orders are rejected with a reason. Signed shipments use a synchronous Odoo path. A workflow registry covers order creation, stock adjustment, and cancellation. Proposals create drafts that a person must confirm through the governed path. (`app/services/shopify_webhooks.py`, `app/services/shipments.py`, `app/governance/registry.py`, `app/proposals/routes.py`.)

## Key decisions and why

I used PostgreSQL for the queue so an order and its job can commit together. The worker claims due jobs with row locks and `SKIP LOCKED`, keeping job state durable without adding a broker. (`app/services/orders.py`, `app/workers/worker.py`.)

I added idempotency because timeouts and webhook delivery can repeat requests. A PostgreSQL advisory transaction lock serializes concurrent uses of a key. The request hash and response are stored: the same key and body replay the response, while a different body conflicts. Shopify's webhook ID supplies its key. (`app/services/orders.py`, `app/main.py`.)

I kept audit history in PostgreSQL. Order and shipment changes create `audit_events`; workflow and proposal activity uses `workflow_events`, whose database trigger rejects updates and deletes. Operators can inspect the event history alongside current order state. (`app/db/models.py`, `migrations/versions/0006_workflow_governance.py`, `app/governance/audit.py`.)

For natural-language proposals, a proposer can suggest only an allowlisted workflow and schema-checked input; it cannot call an ERP. A person confirms a stored proposal, after which normal authorization and approval rules apply. Model output is a draft, not permission to change business data. (`app/proposals/proposers.py`, `app/proposals/routes.py`, `app/governance/routes.py`.)

## How failure is handled

The worker retries temporary transport, HTTP 429, and HTTP 5xx failures using exponential backoff with jitter, a cap, and a maximum attempt count. A permanent failure or exhausted retries sets the order to `FAILED_DEAD`; an operator can queue it again. Stale `PROCESSING` jobs are recovered or failed based on attempts. (`app/workers/worker.py`, `app/services/retry.py`.)

The demo script illustrates the sequence with the mock ERP: submit and replay an order, inject HTTP 500 for a second order, wait for `RETRYING`, clear the fault, wait for `CONFIRMED`, and print its audit trail. This is the script's scenario, not a live result. (`scripts/demo_e2e.py`, `mock_erp/main.py`.)

## What was verified live

The Shopify notes record a real development-store order reaching `CONFIRMED` in the mock ERP through the signed webhook and worker path. The Odoo notes record a local Odoo 19 probe of sale-order create, confirm, shipment lookup, and cancel calls. The probe is not an end-to-end gateway order and shipment run against Odoo. (`docs/shopify-live-test.md`, `docs/odoo-notes.md`.)

## What is not done yet

I have not deployed the project to the cloud or completed an end-to-end gateway run against Odoo that includes shipment application. The local probe does not substitute for that integration run. Live Anthropic proposer behavior is not recorded as verified. (`docs/odoo-notes.md`, `app/proposals/proposers.py`.)

## What I would do next

I would next run the full Odoo path in a development database: configure the adapter, deliver an order, confirm its ERP state, apply a signed shipment, and inspect the stock change. I would record setup and failures so adapter probes remain distinct from gateway evidence. I would then address deployment and secret management, shared admin identity, and current shipment and cancellation boundaries. The result should show how I approach integration work, what I verified, and what remains.
