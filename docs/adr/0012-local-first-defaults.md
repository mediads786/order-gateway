# ADR-0012: Local-first defaults and the LEGACY_AUTH switch

**Status:** Accepted

**Date:** 2026-10-06

## Context

The project is for local development and demonstration. Its original order routes began without API-key authentication. The admin later added a shared token, and LEGACY_AUTH later added optional key enforcement.

## Decision

Keep Docker Compose as the local startup path. Leave original order create, read, and retry routes open unless LEGACY_AUTH=key is set. In key mode, apply authentication and each route's role allowlist. Use one shared ADMIN_TOKEN for admin access, disabling pages when it is shorter than 16 characters. Local defaults date from October 6; admin authentication arrived October 7 and LEGACY_AUTH October 8.

## Consequences

Local demonstration can start without provisioning order API keys. Open original routes allow unauthenticated creation, reading, and manual retry by default. The admin has no per-user identity or login rate limiting. Key mode adds route permissions but does not impose governed approvals on original order intake. These defaults are limits of the local demonstration project.

## Alternatives considered

The repository documents role-based API keys through LEGACY_AUTH=key and identifies per-user admin identity as future work.

## Evidence in the code

`docker-compose.yml`, `app/main.py`, `app/governance/legacy.py`, `app/governance/keys.py`, `app/admin/auth.py`, `app/admin/routes.py`.
