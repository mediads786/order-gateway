# ADR-0004: One canonical order format; Decimal money; UTC; server-computed totals

**Status:** Accepted

**Date:** 2026-10-06

## Context

API and Shopify orders need a common representation. Decimal avoids rounding bugs, and UTC avoids timezone bugs. The client must not determine the stored total.

## Decision

Validate intake against one canonical order schema with customer contact, currency, and order lines. Use Decimal for money and PostgreSQL numeric columns for storage. Compute totals on the server from quantity and unit price. Use UTC in application timestamps and serialized order dates. Map Shopify fields into this format before intake.

## Consequences

The core receives consistent input and calculates totals without binary float arithmetic. Strict validation rejects unsupported fields and floating-point unit prices. Shopify taxes, discounts, and supplied totals are ignored. The Odoo boundary converts prices to numbers, and Odoo's total can differ from the gateway total.

## Alternatives considered

The design notes contrast Decimal with binary floats, which cause rounding errors.

## Evidence in the code

`app/core/schemas.py`, `app/services/orders.py`, `app/services/shopify_webhooks.py`, `app/db/models.py`, `app/adapters/odoo.py`.
