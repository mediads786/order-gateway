# ADR-0007: One mapper per order source and one adapter per ERP behind a common interface

**Status:** Accepted

**Date:** 2026-10-07

## Context

Order sources and ERPs use different payloads and APIs. The core needs a common order representation and delivery interface. The recorded rationale is that new sources and ERPs change a mapper or adapter rather than the core.

## Decision

Translate source-specific input into the canonical order format. Shopify has an explicit mapper, while the original order API accepts canonical input directly. Put ERP operations behind the ErpAdapter protocol. Select the mock or Odoo implementation through ERP_ADAPTER. The common ERP interface first appears with the Odoo adapter commit.

## Consequences

The worker uses the shared interface without selecting ERP-specific request shapes. Mapping and adapter code isolate existing integration differences. Current source values and adapter choices are closed lists in code, so extensions also require registration changes. Unsupported input is rejected, and an unknown adapter setting fails configuration.

## Alternatives considered

No alternative was formally evaluated.

## Evidence in the code

`app/core/schemas.py`, `app/services/shopify_webhooks.py`, `app/adapters/base.py`, `app/adapters/factory.py`, `app/adapters/mock.py`, `app/adapters/odoo.py`, `app/workers/worker.py`.
