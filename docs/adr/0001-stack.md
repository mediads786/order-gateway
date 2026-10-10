# ADR-0001: Python, FastAPI, PostgreSQL, Docker Compose

**Status:** Accepted

**Date:** 2026-10-06

## Context

The gateway accepts orders and stores them for ERP delivery. Python and FastAPI were already familiar and fast to build with. Storage needs tracked migrations, and the local project needs a single command to start its services.

## Decision

Use Python and FastAPI for the API. Use PostgreSQL with SQLAlchemy 2 for storage and Alembic for migrations. Use Docker Compose to run the database, API, mock ERP, and worker together. The API container runs migrations before starting the server.

## Consequences

The stack supports transactional storage and reproducible local startup. Compose is intended for local development and demonstration, not production orchestration. Running the stack requires Docker, and secrets must be supplied through configuration.

## Alternatives considered

No alternative was formally evaluated.

## Evidence in the code

`app/main.py`, `app/db/session.py`, `requirements.txt`, `migrations/env.py`, `Dockerfile`, `docker-compose.yml`.
