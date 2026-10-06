import os
from pathlib import Path

import pytest
import psycopg
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from sqlalchemy.engine import make_url
from sqlalchemy import text


test_database_url = os.environ.get("TEST_DATABASE_URL")
if not test_database_url:
    env_file = Path(".env")
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "TEST_DATABASE_URL":
                test_database_url = value.strip().strip("\"'")
                break
if not test_database_url:
    pytest.exit("TEST_DATABASE_URL must point to a dedicated database ending in '_test'", returncode=4)

try:
    test_url = make_url(test_database_url)
except Exception as exc:
    pytest.exit(f"TEST_DATABASE_URL is invalid: {exc}", returncode=4)

if not test_url.database or not test_url.database.endswith("_test"):
    pytest.exit("TEST_DATABASE_URL database name must end in '_test'", returncode=4)

os.environ["DATABASE_URL"] = test_database_url
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
    command.upgrade(Config("alembic.ini"), "head")


@pytest.fixture(autouse=True)
def clear_database(migrate_test_database):
    with SessionLocal.begin() as db:
        db.execute(text("TRUNCATE audit_events, idempotency_keys, order_lines, orders RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
