# ADR-0006: Append-only audit history

**Status:** Accepted

**Date:** 2026-10-06

## Context

Operators need traceability for orders and delivery attempts. Current state alone does not explain the sequence of changes. Governed workflows and proposals also need a record of requests and decisions.

## Decision

Append order events to audit_events as state changes and attempts occur. Application audit flows have no update or delete code paths. Record workflow requests, decisions, and proposal activity in workflow_events. A later governance migration adds a database trigger rejecting updates and deletes on workflow_events.

## Consequences

Operators can inspect history alongside current state. The workflow trigger protects rows against mutation at the database level. Order audit_events do not have the equivalent trigger. Event tables grow, and the design notes leave archiving for later.

## Alternatives considered

No alternative was formally evaluated.

## Evidence in the code

`app/services/orders.py`, `app/workers/worker.py`, `app/governance/audit.py`, `app/proposals/service.py`, `migrations/versions/0001_intake_storage.py`, `migrations/versions/0006_workflow_governance.py`.
