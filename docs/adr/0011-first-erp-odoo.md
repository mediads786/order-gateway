# ADR-0011: Odoo 19 as the first real ERP, through its JSON-2 API

**Status:** Accepted

**Date:** 2026-10-07

## Context

The project needs a first real ERP alongside the mock adapter. The recorded choice compared ERPNext or Odoo, free and locally runnable in Docker without rate-limit surprises, with Zoho Inventory. Zoho offered a sellable niche but required account setup and had API limits. The reason for choosing Odoo over ERPNext is not recorded.

## Decision

Choose Odoo 19 and use its JSON-2 API. Send bearer credentials and the configured database header to model/method endpoints. Match orders using client_order_ref and products using default_code. Create and confirm sale orders through the adapter, with cancellation and stock operations using the same transport.

## Consequences

The adapter provides a local real-ERP path with external-reference lookup before order creation. Unknown or ambiguous SKUs and currency mismatch are permanent failures. Stock operations use one configured warehouse, and counted-quantity updates can race with other stock changes. Shipments lack a cumulative shipped-versus-ordered check. Notes leave failure handling during delivery and non-local deployments uncovered.

## Alternatives considered

ERPNext and Zoho Inventory were named options. NetSuite and SAP remain stretch goals pending access.

## Evidence in the code

`app/adapters/odoo.py`, `app/adapters/base.py`, `app/adapters/factory.py`, `app/services/shipments.py`.
