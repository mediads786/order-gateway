import os
from pathlib import Path
import sys
import time

import httpx
import pytest
import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy import text

ERP_TEST_URL = "http://127.0.0.1:9001"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

test_database_url = os.environ.get("TEST_DATABASE_URL")
if not test_database_url:
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "TEST_DATABASE_URL":
                test_database_url = value.strip().strip("\"'")
                break
if not test_database_url:
    pytest.exit("TEST_DATABASE_URL must point to a dedicated database ending in '_test'", returncode=4)

env_file = PROJECT_ROOT / ".env"
if env_file.is_file():
    env_names = {
        "ODOO_BASE_URL", "ODOO_DB", "ODOO_API_KEY", "ODOO_EXPECTED_CURRENCY", "ODOO_WAREHOUSE_CODE",
    }
    for line in env_file.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() in env_names:
            os.environ.setdefault(name.strip(), value.strip().strip("\"'"))

try:
    test_url = make_url(test_database_url)
except Exception as exc:
    pytest.exit(f"TEST_DATABASE_URL is invalid: {exc}", returncode=4)

if not test_url.database or not test_url.database.endswith("_test"):
    pytest.exit("TEST_DATABASE_URL database name must end in '_test'", returncode=4)

os.environ["DATABASE_URL"] = test_database_url
os.environ["ERP_BASE_URL"] = ERP_TEST_URL
os.environ["ERP_ADAPTER"] = "mock"
admin_url = test_url.set(drivername="postgresql", database="postgres")
try:
    with psycopg.connect(admin_url.render_as_string(hide_password=False), autocommit=True) as connection:
        exists = connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (test_url.database,)).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_url.database)))
except Exception as exc:
    pytest.exit(f"Could not prepare TEST_DATABASE_URL database: {exc}", returncode=4)

from app.db.session import SessionLocal
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def migrate_test_database():
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")


@pytest.fixture(scope="session", autouse=True)
def mock_erp_service():
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{ERP_TEST_URL}/sales-orders", timeout=1)
            response.raise_for_status()
            return
        except httpx.HTTPError:
            time.sleep(0.2)
    pytest.exit("Mock ERP is unavailable at 127.0.0.1:9001; start it with docker compose up -d", returncode=4)


@pytest.fixture(autouse=True)
def clear_database(migrate_test_database, mock_erp_service):
    reset_response = httpx.post(f"{ERP_TEST_URL}/admin/reset", timeout=3)
    reset_response.raise_for_status()
    with SessionLocal.begin() as db:
        db.execute(text("TRUNCATE workflow_events, proposals, approvals, api_keys, audit_events, shipments, idempotency_keys, jobs, order_lines, orders RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
