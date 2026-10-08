# Shopify live test (development store)

Date: 2026-10-08. Gateway version: Modules 1 to 9, `ERP_ADAPTER=mock`.

## Setup

- Shopify development store, webhook created in **Settings > Notifications > Webhooks**: event **Order creation**, format **JSON**, URL `<tunnel>/webhooks/shopify/orders-create`.
- Public URL from a `cloudflared` quick tunnel to `localhost:8002`. A quick tunnel gets a new URL every time it starts, so the webhook URL must be edited after each restart.
- The signing secret shown on that Webhooks page is stored in `.env` as `SHOPIFY_WEBHOOK_SECRET` (no quotes). Changing it needs `docker compose up -d --force-recreate api worker`.
- A product with SKU `ABC` and a real order created in the admin (**Orders > Create order**), customer attached, marked as paid. The "Send test notification" button was not used.

## What was proven

A real Shopify order travelled the full path and ended `CONFIRMED`:

Shopify > tunnel > `POST /webhooks/shopify/orders-create` (HMAC verified on the raw body) > canonical order > queued job > worker > mock ERP.

Observed on the order detail page:

- source `shopify`, external ref = the Shopify numeric order id
- customer name and email mapped from the Shopify customer
- line `ABC`, quantity 10, unit price 111.00; the total (1110.00) is computed by the gateway from the lines
- job `DONE` on attempt 1, audit trail `order.received`, `order.queued`, `order.processing`, `order.confirmed`

Signature behavior seen live:

| Situation | Gateway response |
|---|---|
| Unsigned or wrongly signed request | 401, nothing stored (log line "Rejected Shopify webhook with invalid signature") |
| Secret in `.env` empty | 503 |
| Valid signature | 200 |

## Real payload differences found

1. **An order without a customer is rejected.** Shopify does not require a customer on an order, and then sends `"customer": null`. The gateway requires a customer name plus an email or phone, so it stored the order as `REJECTED` (reason on the order detail page: customer is not a valid object). The webhook still answered 200, which stops Shopify from retrying the delivery. After attaching a customer with a name and an email to the order, the same flow was `CONFIRMED`. Fixing this means falling back to the shipping or billing address name and the order's top-level email or phone; not done yet.
2. The real payload carries many more fields than the gateway maps (prices, taxes, addresses, discounts, refunds, and so on). Only the fields listed above are used; everything else is ignored by the mapper.
3. A webhook created in the admin is signed with the one secret shown on the Webhooks page, not a per-webhook secret.

## Not covered by this live test

Delivery of the same webhook twice (covered by automated tests only), other Shopify topics, and a product without a SKU (rejected by design).
