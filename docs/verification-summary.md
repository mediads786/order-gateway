# Order Gateway: verified results and scope

Evidence reconciled on 17 October 2026 (Asia/Karachi). This public summary omits deployment addresses, customer details, account identifiers, credentials and raw screenshots. Original records remain private. Historical tests and the later read-only record review are distinguished below; this review did not submit orders or rerun integrations.

## Real Shopify development-store order to Odoo

On 15 October 2026 the owner performed a real development-store test. The original screenshots show Shopify order #1021, a Shopify-source gateway order in CONFIRMED with its job DONE on attempt 1, and confirmed Odoo sales order S00054. All three show BOOK quantity 3 at USD 15.00 and PEN quantity 3 at USD 5.00, totaling USD 60.00.

The gateway order and its confirmation audit were independently read on 17 October. The screenshots and owner's identification establish the demonstrated association; this reconciliation did not perform a fresh Odoo API readback. No retained output establishing an automated "20/20 checks" count was located, so that numerical claim is omitted.

This later owner-performed test supplements the earlier [synthetic signed-webhook proof](shopify-odoo-e2e-run.md); it does not rewrite that earlier run as a real Shopify delivery. Matching totals for this fixture do not establish general tax, discount, shipping, refund or settlement reconciliation.

## Intake, retries and recovery

- [Real Shopify intake on 8 October](shopify-live-test.md) reached the mock ERP through signed webhook intake.
- The 13 October mock recording shows one normal order confirming on attempt 1 and another confirming on attempt 4 after three controlled HTTP 500 failures. Exact-request replay returned the existing order.
- A separately retained 15 October audit records five failed attempts, terminal failure, an admin manual requeue and successful confirmation. Earlier events remained visible after recovery.
- The synthetic Odoo proof confirmed a sale order and then replayed the identical webhook without creating a second Odoo order in that sequential test.
- The 15–16 October AWS QA records document same-webhook-ID replay returning the same order, one associated job and one mock outcome; invalid signatures were rejected, and a changed payload with a valid signature but a reused webhook ID was refused.

The 17 October read-only review covered every one of the 56 stored local gateway orders and their 264 audit events. Twelve currently confirmed orders retained retry histories; one order remained dead after retry exhaustion. All currently confirmed orders had completed jobs and confirmation events. Stored line sums matched stored order totals wherever lines existed. These observations corroborate recorded examples; they are not a universal no-loss or exactly-once external-execution guarantee.

## Governance, shipments and proposals

The [local Odoo run record](odoo-e2e-run.md) documents worker delivery and confirmation, cancellation approved by a different key, approved stock adjustment, signed stock reduction, unchanged stock after identical shipment replay, conflicting-body rejection and insufficient-stock refusal.

The later local review found 16 approvals: 11 executed, three rejected and two pending. Every decided approval used a different deciding key from its requester. Four proposals were confirmed, two invalid and one still proposed. These demonstrate key-based roles, review and decisions; they do not establish per-person identity or tamper-proof compliance logging. Shipment history contains both successful application and retained insufficient-stock failures.

The default natural-language proposer is rule-based. The optional model-backed proposer is implemented but is not claimed as live-provider verified.

## AWS deployment and recovery checks

The 15–16 October deployment records describe a single EC2 instance running API, worker, PostgreSQL, mock ERP and Caddy through Docker Compose. Odoo was not the deployed adapter in those tests. Verified checks include:

- Trusted HTTPS, HTTP-to-HTTPS redirect and security headers; Caddy is the only container publishing application ports.
- Admin/API authentication and secure session-cookie flags, rejected invalid signatures and a signed sample order reaching CONFIRMED.
- Container health, restart policies, bounded logs, host firewall and IMDSv2 enforcement.
- Nightly backup scheduling, restricted backup permissions and gzip integrity.
- A same-host restore into a temporary database, with row counts matching all 11 copied tables, while the original database stayed online.
- Credential rotation followed by rejection of old credentials and a successful backup job with the replacement database credential. Credential values are excluded from this summary.

Off-server backup was not configured in the recorded deployment. Replacement-host recovery, sustained load, high availability and full real-Odoo outage recovery were not established. Health checks and restart policies are verified mechanisms, not a guarantee of recovery from every failure.

## Automated regression evidence

The recorded 15 October local run passed 284 tests with one optional live-Odoo test skipped and two deprecation warnings, against application revision `5d98720bfbaf9b49bea188e9665afd3ac305cc66`. The [11 October CI run](https://github.com/mediads786/order-gateway/actions/runs/38121841837) records the same passed/skipped counts. The [CI configuration](../.github/workflows/ci.yml) runs on pushes to master and pull requests. The 17 October reconciliation was read-only and did not produce a new pytest result.

## Scope boundaries

Deduplication is scoped to the documented request key/webhook identity. The Python worker's stale-result fencing and Odoo's non-atomic lookup/create remain implementation limits under overlapping or uncertain attempts. The shared admin login does not provide individual human attribution. Financial reconciliation and complete warehouse fulfilment are outside scope.

Gmail and WhatsApp notification evidence belongs to the separate [n8n implementation](https://github.com/mediads786/order-gateway-n8n/blob/main/docs/QA-TEST-REPORT.md). The Python implementation provides failure visibility and audit history; this summary does not attribute n8n notification features to it.
