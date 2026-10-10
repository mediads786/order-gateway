# Architecture decision records

An architecture decision record (ADR) captures a design choice, its context, and its consequences so that later readers can understand the project's structure and limits.

| Number | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-stack.md) | Python, FastAPI, PostgreSQL, Docker Compose | Accepted | 2026-10-06 |
| [0002](0002-postgres-queue.md) | Job queue in a PostgreSQL table using FOR UPDATE SKIP LOCKED | Accepted | 2026-10-06 |
| [0003](0003-idempotency.md) | Idempotency-Key header with a stored request hash | Accepted | 2026-10-06 |
| [0004](0004-canonical-order-and-money.md) | One canonical order format; Decimal money; UTC; server-computed totals | Accepted | 2026-10-06 |
| [0005](0005-retry-policy.md) | Retries with exponential backoff and jitter, dead-letter state, stale-job recovery | Accepted | 2026-10-07 |
| [0006](0006-append-only-audit.md) | Append-only audit history | Accepted | 2026-10-06 |
| [0007](0007-mappers-and-adapters.md) | One mapper per order source and one adapter per ERP behind a common interface | Accepted | 2026-10-07 |
| [0008](0008-shopify-webhook.md) | Shopify webhook intake: HMAC over the raw body, webhook ID as idempotency key, specific rejection reason for unmappable orders | Accepted | 2026-10-07 |
| [0009](0009-governance-and-approvals.md) | Workflow registry, roles, and approvals before risky actions | Accepted | 2026-10-07 |
| [0010](0010-ai-proposes-only.md) | The AI proposer can only propose; people confirm | Accepted | 2026-10-08 |
| [0011](0011-first-erp-odoo.md) | Odoo 19 as the first real ERP, through its JSON-2 API | Accepted | 2026-10-07 |
| [0012](0012-local-first-defaults.md) | Local-first defaults and the LEGACY_AUTH switch | Accepted | 2026-10-06 |

These records were written from the code and the project's recorded design notes, with dates taken from git history.
