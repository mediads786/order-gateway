# ADR-0003: Idempotency-Key header with a stored request hash

**Status:** Accepted

**Date:** 2026-10-06

## Context

Clients can retry when a response is delayed or lost. Repeated intake must not create duplicate orders. Reusing a key for a different request must be distinguishable from replaying the original request.

## Decision

Require Idempotency-Key on order intake and store a request hash with the response. Hash parsed JSON using sorted keys and compact serialization; invalid JSON uses its raw bytes. Serialize concurrent requests for a key with a PostgreSQL advisory transaction lock. The same key and hash replays the saved body; a different hash returns HTTP 409. Successful ordinary intake replays with HTTP 200, while saved 202 and 422 statuses are retained.

## Consequences

Client retries reuse the saved order or rejection. The replay is the saved response rather than a fresh view of delivery status. Keys remain stored indefinitely in version 1. A different key does not identify a duplicate business order.

## Alternatives considered

No alternative was formally evaluated.

## Evidence in the code

`app/main.py`, `app/services/orders.py`, `app/db/models.py`, `migrations/versions/0001_intake_storage.py`, `migrations/versions/0002_status_and_replay_code.py`.
