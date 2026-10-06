# Order Gateway — Modules 1–3: Intake, Queue, Worker, Retries, and Mock ERP

Accepts canonical orders, computes totals with `Decimal`, stores raw and canonical data, enforces idempotency, and records audit events. Valid orders atomically receive a queued job; a separate worker sends them to the in-memory mock ERP.

## Requirements

- Python 3.12
- Docker Compose

## Run

From the project root, create a virtual environment and install the pinned dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
docker compose up -d --build
.\.venv\Scripts\python.exe -m pytest -v
```

Compose starts PostgreSQL, the API, mock ERP, and the worker. The API is available at `http://127.0.0.1:8002`; the mock ERP is at `http://127.0.0.1:9001`. The API applies Alembic migrations at startup, and Compose waits for the API migration and mock ERP before starting the worker. Tests read `TEST_DATABASE_URL` from the environment or `.env`; the test setup creates that database if missing, forces `DATABASE_URL` to it before importing the app, and clears its tables before each test. It exits before importing the app if the database name does not end in `_test`.

Set `ERP_BASE_URL`, `ERP_TIMEOUT_SECONDS`, `WORKER_POLL_INTERVAL_SECONDS`, `RETRY_BASE_SECONDS`, `RETRY_MAX_SECONDS`, `MAX_ATTEMPTS`, and `STALE_JOB_SECONDS` in `.env` for the worker. Retry delays use capped exponential backoff with jitter. Temporary failures (5xx, 429, timeouts, and connection errors) retry until `MAX_ATTEMPTS`; other 4xx responses fail permanently. Orders awaiting a retry have status `RETRYING`. Stale worker claims are recovered after `STALE_JOB_SECONDS`.

## Request example

```json
{
  "source": "web",
  "external_ref": "web-123",
  "customer": {"name": "Ada", "email": "ada@example.com"},
  "currency": "USD",
  "lines": [{"sku": "BOOK", "qty": 2, "unit_price": "12.50"}]
}
```

Prices must be JSON strings, such as `"12.50"`. JSON numeric prices are rejected by design so the API never converts a binary float into money.

Send it to `POST /orders` with an `Idempotency-Key` header. The server returns `201` for a new valid order, `200` for a repeated identical request, `409` for reuse with a different body, `422` for a stored rejected order, and `400` when the key is missing. `GET /orders/{order_id}` returns the order with its audit events.

The API and worker read `DATABASE_URL`. Tests use `TEST_DATABASE_URL`, shown in `.env.example`, and never connect to the `order_gateway` database.

## Mock ERP

- `POST /sales-orders` creates an ERP order once per `external_id`; duplicate requests return `409` with the existing `erp_order_id`.
- `GET /sales-orders` lists records; `GET /sales-orders/{erp_order_id}` returns one record.
- `POST /admin/faults` accepts `none`, `error_500`, `rate_limit_429`, `error_422`, `timeout`, or `slow`, plus optional `fail_rate` (0–1) and `latency_ms`; `POST /admin/reset` clears its in-memory records and all fault settings.
- `POST /orders/{order_id}/retry` requeues an order in `FAILED_DEAD` and returns it to job state `QUEUED`.

## Stale job recovery

The worker recovers jobs left in `PROCESSING` past `STALE_JOB_SECONDS`; recovered jobs retry unless they have reached `MAX_ATTEMPTS`.
