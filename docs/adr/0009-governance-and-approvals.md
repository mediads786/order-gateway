# ADR-0009: Workflow registry, roles, and approvals before risky actions

**Status:** Accepted

**Date:** 2026-10-07

## Context

Stock adjustment, cancellation, and large orders are sensitive actions. The project needs explicit workflow permissions and approval decisions. Requesters must not approve their own requests, and decisions need an audit trail.

## Decision

Register create_order, adjust_stock, and cancel_order with schemas and role rules. Authenticate governed requests with hashed API keys for operator, approver, and admin roles. Hold governed orders above APPROVAL_THRESHOLD, and always require approval for stock adjustment and cancellation. Require a different authorized key to decide, with a database constraint preventing self-decision. Unapproved governed actions do not reach the ERP, and decisions are audited (decision events are written in a separate step, see [Known limitations](../../README.md#known-limitations)). The registry arrived October 7; approval execution arrived October 8.

## Consequences

Governed actions have explicit permissions and recorded decisions before execution. Pending approvals do not expire. Unknown ERP state can prevent cancellation, and a crash between ERP cancellation and the gateway update can require manual recovery. Original order routes bypass the governed approval path, even when LEGACY_AUTH requires a key.

## Alternatives considered

No alternative was formally evaluated.

## Evidence in the code

`app/governance/registry.py`, `app/governance/keys.py`, `app/governance/routes.py`, `app/governance/approvals.py`, `app/services/orders.py`, `migrations/versions/0007_approvals.py`, `app/main.py`.
