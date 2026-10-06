# Order Gateway: Scope, Design & Build Guide

Owner: Ahsan Shahzad Hassan
Status: v1.0 (project charter, the single source of truth)
Working method: Claude writes specs and verifies. Codex / ChatGPT Plus generates code. The owner runs, reads and understands every module.

---

## 1. Purpose

Build one portfolio-grade system that combines:

- **Gateway** (integration engine): takes business orders from any channel and delivers them reliably to an ERP, exactly once, with retries and a full audit trail.
- **OpsPilot's best ideas** (governance): approved workflows, permissions, approvals for sensitive actions, and an AI front door that can only propose, never act directly.

Goals, in priority order:

1. **Learn:** APIs, OAuth, webhooks, idempotency, queues, retries, failure handling, governance. These are core integration-engineering skills.
2. **Earn:** each layer is useful on its own, so freelance gigs and applications can start after Layer 1.
3. **Job-ready:** a project that is easy to demo and explain in interviews for integration, business-systems, implementation-consulting and AI-automation roles.

**Principle: bottom-up.** If the project stops at any layer, what exists still works, still demos, and still sells.

---

## 2. Scope

### In scope

- Order intake API with validation and an idempotency guarantee
- Durable job queue, worker, retries with backoff, dead-letter handling
- A mock ERP with fault injection (for learning failure handling)
- Shopify webhook order source (HMAC-verified)
- One real ERP adapter
- Shipment/fulfillment event that adjusts inventory
- Append-only audit log and a simple operations view
- Workflow registry, role permissions, approval step (Layer 2)
- Natural-language "proposal" front door with human approval (Layer 3)
- Tests, Docker, README, one cloud deployment

### Out of scope (do not build, even if an AI suggests it)

- Multi-tenant SaaS, billing, user sign-up
- Fancy UI design (functional admin pages only)
- Kubernetes, microservices split, message brokers (Kafka/RabbitMQ) in v1
- Direct LLM access to ERP calls
- Payments, tax engines, accounting logic
- Supporting more than one real ERP before the first is finished

### Stretch (only after Layers 1-3 are done)

- NetSuite adapter (needs an account; confirm access first)
- SAP Integration Suite adapter (free trial exists but is limited and time-boxed)
- CI/CD pipeline and cloud deployment hardening

---

## 3. Architecture

```
 Order sources            GATEWAY (this project)                      ERP side
 -------------   +---------------------------------------+    +------------------+
 Web / Shopify   |  1. Intake API   (validate, idempotency)|    | Mock ERP         |
 WhatsApp        |  2. Postgres     (orders, jobs, audit)  | -> | (fault injection)|
 Manual / forms  |  3. Worker       (claims jobs)          |    +------------------+
 Control Center  |  4. Adapter      (canonical -> ERP API) | -> | Real ERP         |
                 |  5. Retry/DLQ    (backoff, dead letter) |    +------------------+
                 |  6. Governance   (Layer 2)              |
                 |  7. AI front door(Layer 3, proposals)   |
                 +---------------------------------------+
```

### Core design decisions

| Topic | Decision | Why |
|---|---|---|
| Language/framework | Python 3.12, FastAPI | Already familiar, fast to build |
| Database | PostgreSQL + SQLAlchemy 2 + Alembic | Reliable, migrations tracked |
| Queue | Postgres jobs table using `FOR UPDATE SKIP LOCKED` | No extra infrastructure; teaches how queues really work |
| Idempotency | `Idempotency-Key` header + request hash stored in DB | Prevents duplicate orders from retries |
| Money | `Decimal`, never float; currency required | Avoids rounding bugs |
| Time | UTC everywhere | Avoids timezone bugs |
| Audit | Append-only table; no update or delete code paths | Traceability |
| Config | Environment variables, `.env.example`, no secrets in code | Security basics |
| Testing | pytest against real Postgres in Docker | Tests prove behavior, not mocks |
| Packaging | Docker Compose | One command to run everything |

### Canonical order

- `order_id` (uuid, server-generated)
- `source`: web | whatsapp | shopify | manual
- `external_ref` (optional)
- `customer`: name (required), phone or email (at least one)
- `currency` (3-letter code)
- `lines` (1 or more): `sku`, `qty` (integer > 0), `unit_price` (decimal >= 0)
- `total`: computed by the server, never trusted from the client
- `status`, `created_at`

### State machine (names are fixed)

```
RECEIVED -> QUEUED -> PROCESSING -> CONFIRMED
                          |
                          +-> RETRYING -> PROCESSING (loop, max attempts)
                          +-> FAILED_DEAD (after max attempts; manual action only)
REJECTED  (invalid input; terminal)
PENDING_APPROVAL -> APPROVED -> QUEUED    (Layer 2 only)
PENDING_APPROVAL -> CANCELLED             (Layer 2 only)
```

Module 1 uses only RECEIVED and REJECTED. Do not invent other state names.

### Retry policy (default)

- Max 5 attempts, exponential backoff with jitter
- Retry on: timeouts, HTTP 429, HTTP 5xx
- Do not retry on: HTTP 4xx validation errors (send to FAILED_DEAD with the reason)
- Every attempt writes an audit event

### Repository layout

```
order-gateway/
  app/            api/  core/  db/  workers/  adapters/  governance/  ai/
  mock_erp/       separate small FastAPI service with fault injection
  migrations/
  tests/
  docs/           this guide, decision log, module notes
  docker-compose.yml
  README.md
```

---

## 4. Build plan: layers and modules

Each module ends with its acceptance tests passing and a verification review with Claude before the next one starts.

### Layer 1: Gateway engine (days 1-21)

**Module 1: Intake & storage**
- Goal: accept and store orders safely.
- Deliverables: POST /orders with idempotency, GET /orders/{id}, /health, orders / order_lines / idempotency_keys / audit_events tables, migration.
- Done when: all six test groups pass: valid order and correct total; same key + same body returns the original response; same key + different body returns 409; invalid inputs return 422 and are stored as REJECTED; two simultaneous identical requests create exactly one order; audit events exist for received and rejected.
- You learn: idempotency, transactions, race conditions.

**Module 2: Queue, worker, mock ERP**
- Goal: orders flow to a fake ERP asynchronously.
- Deliverables: jobs table, worker that claims jobs with SKIP LOCKED, mock ERP service with endpoints to create a sales order and to inject faults (fail rate, latency, 429, 500, timeout), status transitions to CONFIRMED.
- Done when: an order submitted to the API reaches CONFIRMED; two workers running together never process the same job twice; ERP external-id duplicates are rejected by the mock.
- You learn: queues, concurrency, why async matters.

**Module 3: Retries and dead letters**
- Goal: survive ERP failures.
- Deliverables: backoff with jitter, attempt counter, RETRYING and FAILED_DEAD states, a manual "retry dead job" endpoint, audit events per attempt.
- Done when: tests prove retry on 500/429/timeout, no retry on 4xx, dead-letter after max attempts, and recovery when the mock ERP comes back.
- You learn: failure handling, the heart of integration work.

**Module 3B: Shopify webhook intake (added after buyer-demand check)**
- Goal: accept real store orders, the most commonly requested integration on Upwork and Fiverr.
- Deliverables: webhook endpoint for Shopify order events, HMAC signature verification on the raw request body (check Shopify's current docs for the exact header and method), mapping from the Shopify order to the canonical order, and use of Shopify's unique webhook ID header as the idempotency key (confirm the header name in the docs).
- Done when: a valid signed webhook creates one order; the same webhook delivered twice creates one order; a webhook with a bad signature is rejected with 401 and nothing stored; tests use recorded sample payloads, plus one live test against a free Shopify development store.
- You learn: webhook security, at-least-once delivery, third-party payload mapping.

**Module 4: First real ERP adapter + shipment event**
- Goal: talk to a real system and adjust inventory on shipment.
- Choose one (decide here, not earlier): **ERPNext or Odoo** (free, can run locally in Docker, no rate-limit surprises) or **Zoho Inventory** (matches a sellable market niche, but needs account setup and has API limits). Verify current setup steps in the vendor docs before starting.
- Deliverables: adapter behind a common interface, auth handling, field mapping table, shipment webhook that creates a stock adjustment, adapter tests with recorded responses plus one live smoke test.
- Done when: an order and its shipment appear correctly in the real ERP, and re-sending either causes no duplicates.
- You learn: OAuth/API keys, mapping, real-world API quirks.

**Module 5: Operations view, packaging, README**
- Goal: make Layer 1 demoable and sellable.
- Deliverables: simple admin page (list orders, status, audit trail, retry button), Docker Compose with one command, README with architecture diagram, 3-minute demo script (send order, kill ERP, watch retries, recover, read the audit log).
- Done when: a stranger can run it from the README in 10 minutes.
- Milestone: after this, start applying for integration gigs and roles.

### Layer 2: Governance (days 22-35)

**Module 6: Workflow registry + permissions**
- Registry of approved workflows (create_order, adjust_stock, cancel_order) with input schema and risk level; roles (operator, approver, admin); every request is checked against permissions.

**Module 7: Approvals**
- Risky actions (for example stock write-off, or an order over a threshold) go to PENDING_APPROVAL; approver approves or rejects; both are audited with who and when.
- Done when: tests prove an operator cannot approve their own request and that unapproved actions never reach the ERP.

### Layer 3: AI front door (days 36-50)

**Module 8: Natural-language proposals**
- User types a request; an LLM converts it to a structured proposal for a registered workflow only (start with two workflows); the user sees the proposal, approves, and Layer 2 executes it.
- Rules: the model never calls the ERP; invalid or unknown workflows are rejected; every proposal and decision is audited; the model's output is validated against the registry schema.
- Done when: a test set of 20 phrased requests produces correct proposals, and malicious or ambiguous inputs are rejected.

### Wrap-up (days 51-60)

- CI (tests on each push), one cloud deployment, final README, a short case-study page, and updated Fiverr/Upwork/LinkedIn wording around the project.

### Stretch (after day 60)

- NetSuite adapter (SuiteScript governance units, concurrency limits, REST/OAuth 2.0) and SAP Integration Suite adapter (iFlow, JMS retry, Groovy mapping). Check access before committing time.

---

## 5. How the build runs (to stay on track)

For each module:

1. Claude produces the module spec: contracts, schema, acceptance tests, run command.
2. You paste the spec and the "AI Rules" block (section 6) into Codex or ChatGPT.
3. You run it yourself: `docker compose up -d && pytest`.
4. You read the code until you can explain each file in your own words.
5. You come back to Claude with the checklist (section 7). Claude says go or fix.
6. Only then start the next module.

The trial window for ChatGPT Pro ends Oct 25, 2026. Use it for the heaviest modules (1-3) first.

---

## 6. Rules for any AI coding assistant (paste at the start of every session)

```
You are helping build "order-gateway". Follow these rules strictly.

1. Work ONLY on the module I name. Do not add features, endpoints, tables,
   or dependencies that the spec does not list. If you think something is
   missing, stop and ask; do not add it.
2. Do not change the stack, API contracts, field names, status names, or
   schema defined in the spec. If a change seems necessary, propose it and
   wait for my approval.
3. Money uses Decimal, never float. All timestamps are UTC.
4. No secrets in code. Use environment variables and provide .env.example.
5. Never swallow exceptions silently. Log with order_id / job_id.
6. Tests run against real PostgreSQL via Docker Compose. Do not mock the DB.
7. Keep code simple, typed, with small functions and comments only where
   the logic is non-obvious.
8. At the end give: the file tree, exact run commands, the list of
   assumptions you made, and a 2-line explanation per file.
9. Do not claim that tests pass. I will run them and report the real output.
```

---

## 7. Verification checklist (bring this back to Claude after each module)

- File tree (paste the output)
- Full test output from `pytest` (real output, including failures)
- Main source files for the module (not everything, only what the spec names)
- Anything the AI changed that the spec did not ask for
- Anything you could not explain in your own words

Claude checks the code against the spec's acceptance tests, looks for race conditions, scope creep and missing failure paths, and answers go or fix.

---

## 8. Definition of done (whole project)

- [ ] Layer 1 modules 1-5 pass their acceptance tests
- [ ] Demo script works end to end, including the ERP failure and recovery
- [ ] README lets a stranger run it in 10 minutes
- [ ] Governance and approvals enforced and tested (Layer 2)
- [ ] AI front door can only propose registered workflows (Layer 3)
- [ ] You can explain the architecture, idempotency, retries and audit design without notes
- [ ] Portfolio page, LinkedIn and gig wording updated to match

---

## 9. Decision log

| Decision | Status |
|---|---|
| Stack: Python / FastAPI / PostgreSQL / Docker | Assumed, can be changed before Module 1 only |
| Queue: Postgres jobs table | Decided |
| Shopify webhook source (Module 3B) | Decided, added for buyer demand |
| First real ERP | Decide at Module 4 |
| Admin UI: minimal pages vs inside Control Center | Decide at Module 5 |
| NetSuite / SAP | Stretch, after access is confirmed |
