# Repository publication review — 17 October 2026

Purpose: check both GitHub repositories for accidentally published credentials, private infrastructure addresses and sensitive evidence. This is a disclosure review, not a claim that the applications have no vulnerabilities.

## Audited revisions and coverage

Fresh GitHub mirrors were used, rather than assuming local branches were current.

| Repository | Audited revision | Current tracked files | Unique file blobs across available history | Commits |
| --- | --- | ---: | ---: | ---: |
| order-gateway | `116e8799f138502bc97dbf87aef49674853ce739` | 147 | 303 | 41 |
| order-gateway-n8n | `2d30104a0272e0067d47ada5282e607b0daf5cf9` | 48 | 51 | 2 |

Every reachable file blob was read, including older versions; all available mirrored branches/tags were included. Gitleaks 8.30.1 scanned both histories with full redaction and decoding enabled. The official downloaded scanner archive matched its published SHA-256 checksum. Additional checks compared five available local secret values and two private infrastructure address strings in memory and inspected token, key, address, cloud-identifier, email and hostname candidates. No private values are reproduced here.

The current API demo GIF (84 frames), its older Git version (175 frames), and the n8n proof image were inspected. All 259 GIF frames were extracted locally, reviewed in contact sheets and processed with local Windows OCR; image metadata was checked. The n8n image shows only a cropped order/status toolbar. The recordings show local demo operations and example customer data. No visible credential or live deployment endpoint was identified. OCR and contact-sheet inspection have limits and are not a mathematical guarantee.

## Findings

- No confirmed credential, private-key or private deployment address exposure was found in the audited repository files/history.
- Gitleaks reported one API history candidate: a blank `ODOO_API_KEY` in `.env.example`, followed by the next configuration variable. Scanning complete current files also flagged the blank `ADMIN_TOKEN` followed by its next variable. Inspection confirmed multiline false positives, not credentials. n8n had no Gitleaks findings. No broad allowlist was added to hide these candidates.
- The address review found provider/documentation links, reserved examples and local development configuration, rather than a published live deployment endpoint. Development passwords and test fixtures are public examples; they must never be reused as deployment secrets.
- Private AWS QA originals and console images remain outside the published repositories. Local environment files/backups, private reconciliation records and temporary review tools are excluded from ordinary staging. These files were not uploaded by this review.
- The original n8n checkout was behind the published documentation revision. It was clean and fast-forwarded to the audited revision without changing workflow exports, credentials, database state or running services.

## Durable evidence and future checks

Use [verified results and limits](verification-summary.md) for the API and [notification verification](https://github.com/mediads786/order-gateway-n8n/blob/main/docs/NOTIFICATION-VERIFICATION.md) for n8n. Preserve the owner's real Shopify-to-Odoo test and post-template-approval WhatsApp test with their stated provenance. An inactive service today is not evidence that an earlier test never happened.

Follow [publication safety](../SECURITY.md) for each subsequent upload. New files or changed commits require a new publication check; established historical test evidence does not need to be rediscovered unless it is contradicted or the relevant behavior changes.

The publication adds these safety instructions and this report, plus broader API ignore rules. Before committing, the complete proposed indexes (150 API files and 50 n8n files) were exported for scanning, checked against the available private values, and the exact six-file staged allowlist was reviewed. n8n's static release verifier passed: nine workflow exports, 24 Code nodes, 53 expressions and 60 local documentation links. No application or workflow behavior was changed.

## Limits

The comparison includes only credentials available locally; server-only, unknown or already deleted credentials were not inventoried. Reachable Git history does not include inaccessible deleted refs, forks, caches, GitHub artifacts, releases, issues or external backups. This review did not audit dependency advisories, the live server, all runtime data or third-party accounts. No orders were sent or retried and no notifications were sent. No Google Drive backup or document synchronization is established by this Git/local-disk review.
