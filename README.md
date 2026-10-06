# Order Gateway — Module 1: Intake and Storage

Accepts canonical orders, computes totals with `Decimal`, stores raw and canonical data, enforces idempotency, and records received/rejected audit events. This module has no queue or ERP integration.

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
$env:TEST_DATABASE_URL = "postgresql+psycopg://order_gateway:order_gateway@127.0.0.1:5433/order_gateway_test"
docker compose up -d --build
pytest
```

`docker compose up -d --build` starts PostgreSQL and the API at `http://localhost:8001`; the API applies the Alembic migration on startup. Tests require `TEST_DATABASE_URL` to point to a dedicated PostgreSQL database whose name ends in `_test`. The test setup creates that database if missing, forces `DATABASE_URL` to it before importing the app, and clears its tables before each test. It exits before importing the app if the database name does not end in `_test`.

API is available at `http://localhost:8000`; health check: `GET /health`.

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

The API reads `DATABASE_URL`. Tests use `TEST_DATABASE_URL`, shown in `.env.example`, and never connect to the `order_gateway` database.
