# ADR-0002: Job queue in a PostgreSQL table using FOR UPDATE SKIP LOCKED

**Status:** Accepted

**Date:** 2026-10-06

## Context

Accepted orders need durable delivery jobs. The project aims to avoid extra infrastructure and learn how queues work. Message brokers and a microservice split were explicitly outside version 1's scope.

## Decision

Store delivery jobs in PostgreSQL alongside orders. Ordinary valid intake writes the order and job in one transaction; governed orders awaiting approval have no job yet. The worker claims due rows with FOR UPDATE SKIP LOCKED. It commits the claim before calling the ERP through an adapter.

## Consequences

Order intake and queue insertion are atomic, and competing workers can skip claimed rows. Slow ERP calls do not retain claim-transaction locks. Queue operation depends on PostgreSQL and worker polling. The design notes record lower throughput than a broker and exclude streaming workloads.

## Alternatives considered

Kafka, RabbitMQ, and a microservice split were explicitly out of scope for version 1.

## Evidence in the code

`app/services/orders.py`, `app/workers/worker.py`, `app/db/models.py`, `migrations/versions/0003_jobs_and_erp_order_id.py`.
