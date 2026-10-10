# ADR-0005: Retries with exponential backoff and jitter, dead-letter state, stale-job recovery

**Status:** Accepted

**Date:** 2026-10-07

## Context

ERP delivery can fail temporarily or permanently. Immediate retries can overload a struggling ERP, and synchronized retries need jitter. A worker can also crash after claiming a job.

## Decision

Retry timeouts, transport errors, HTTP 429, HTTP 5xx, and invalid adapter responses, including malformed successful JSON shapes reported as ValueError. Treat other HTTP 4xx and permanent adapter errors as non-retryable; Odoo also classifies business errors by name. Use capped exponential backoff with jitter and five attempts by default. Move permanent or exhausted failures to FAILED_DEAD and require manual requeueing. Recover stale PROCESSING jobs or fail them when attempts are exhausted. Write audit events for each attempt and its outcome.

## Consequences

Temporary failures receive spaced retries, while permanent failures become visible for operator action. Defaults use a two-second base, 60-second cap, and 120-second stale threshold. Error misclassification can waste attempts or stop delivery too early. The stale threshold must exceed legitimate ERP call duration, and dead jobs require an operator.

## Alternatives considered

The design notes reject immediate retries and automatic infinite retries.

## Evidence in the code

`app/workers/worker.py`, `app/services/retry.py`, `app/adapters/odoo.py`, `migrations/versions/0004_job_retries.py`.
