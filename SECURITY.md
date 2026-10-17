# Publication safety

Public evidence: [verified results](docs/verification-summary.md) and [dated disclosure review](docs/publication-review-2026-10-17.md). These contain scoped findings, not a security certification.

Keep authentication material private. This includes passwords, signing keys, encryption keys, cookies, tokens, private keys and password hashes. Keep raw environment files, database backups, execution exports and customer data out of Git and public documents. Do not publish the live deployment hostname/IP, cloud account identifiers or private console screenshots. Public provider documentation links, reserved example addresses and loopback development endpoints are examples, not deployment addresses.

Before publishing:

1. Inspect `git status --short`, `git diff --cached --name-only` and the complete staged diff. Use an explicit file allowlist when staging.
2. Scan the complete proposed files and available Git history with a local secret scanner. Use fully redacted output; keep raw review material outside the public tree. For Gitleaks, use `gitleaks git --log-opts=--all --redact=100 --ignore-gitleaks-allow` and scan staged changes with `gitleaks git --pre-commit --staged --redact=100 --ignore-gitleaks-allow`.
3. Compare known private credentials and deployment endpoints in memory without printing them. A generic scanner will not know every password or private address. Review placeholders individually; never exclude all tests or example files from scanning.
4. Inspect screenshot pixels, every recording frame and embedded metadata. OCR supplements visual review. Never assume an image is safe because a text scanner passed.
5. Verify the committed file list and remote commit after pushing. Preserve dated test provenance and update the evidence summary when facts change.

The ignore rules can be bypassed and do not remove tracked files or old commits. If a real credential is exposed, revoke/rotate it first, then plan removal from current files and history with the owner. Deleting a current file alone does not revoke access or erase copies. Do not put the exposed value in an issue, report or chat.

Local development defaults are intentionally public examples. Use the documented production override, independent generated secrets and authenticated routes for deployment. This publication check does not replace dependency auditing, runtime hardening or penetration testing.
