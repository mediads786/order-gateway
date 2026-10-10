# ADR-0008: Shopify webhook intake: HMAC over the raw body, webhook ID as idempotency key, specific rejection reason for unmappable orders

**Status:** Accepted

**Date:** 2026-10-07

## Context

Shopify was chosen as a commonly requested integration source, based on the owner's own review of marketplace listings (not independently verified). Delivery is at-least-once, so duplicate protection is required. Unmappable orders need a useful rejection reason rather than canonical-schema errors about the raw Shopify payload.

## Decision

Verify HMAC-SHA256 over raw request bytes before parsing JSON. Use shopify:<webhook ID> as the idempotency key and map orders/create into canonical orders. Store unmappable valid JSON as REJECTED with the mapper's specific reason; invalid JSON uses ordinary intake rejection. Acknowledge stored rejections with HTTP 200. Signature verification and webhook intake arrived on October 7; specific mapper-reason storage arrived on October 9.

## Consequences

Signed intake and webhook-key replay protect supported deliveries. Only orders/create is supported. Orders without a usable name or both email and phone cannot be mapped, and lines need SKUs. Different webhook IDs for one Shopify order are different idempotency keys. Shopify totals, taxes, and discounts are ignored.

## Alternatives considered

The design notes explain that re-serialized JSON cannot replace raw bytes for signature verification.

## Evidence in the code

`app/main.py`, `app/services/shopify_webhooks.py`, `app/services/orders.py`.
