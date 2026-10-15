"""Repeatable signed Shopify webhook -> Compose worker -> Odoo proof.

Run with the project's .venv Python. No credentials or payloads are logged.
"""

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Reuse the seed script's existing .env loader and SKU list, without running main.
from scripts.odoo_seed import SKUS
from app.adapters.odoo import OdooAdapter
from app.services.shopify_webhooks import map_shopify_order
import httpx

# The adapter and httpx must not emit credentials, bodies or exception tracebacks.
logging.disable(logging.CRITICAL)
SECRET_VALUES = [
    value for name, value in os.environ.items()
    if value and any(word in name.upper() for word in ("SECRET", "TOKEN", "KEY", "PASSWORD"))
]
CHECKS: list[tuple[str, str]] = []


def safe(value: object) -> str:
    message = str(value)
    for secret in sorted(SECRET_VALUES, key=len, reverse=True):
        message = message.replace(secret, "[MASKED]")
    message = re.sub(r"gw_[A-Za-z0-9_-]+", "[MASKED]", message)
    message = re.sub(r"(?i)bearer\s+\S+", "bearer [MASKED]", message)
    return message.encode("ascii", "backslashreplace").decode("ascii")


def say(message: object) -> None:
    print(safe(message), flush=True)


def check(condition: bool, label: str, detail: str = "") -> None:
    result = "PASS" if condition else "FAIL"
    CHECKS.append((label, result))
    say(f"{result}: {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        raise RuntimeError(detail or label)


def compose(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["docker", "compose", "--profile", "odoo", *args], cwd=ROOT,
        input=input_text, capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        # Never run config, inspect, env, or logs: their output can contain secrets.
        raise RuntimeError((result.stderr or result.stdout).strip() or f"Docker exited {result.returncode}")
    return result.stdout


def runtime_settings(service: str, names: list[str], secrets: list[str]) -> dict:
    # Compare secrets inside the container. Only booleans leave the process;
    # expected values go through stdin, never command arguments or files.
    code = (
        "import json,os,sys; p=json.load(sys.stdin); "
        "print(json.dumps({'settings':{n:os.getenv(n,'') for n in p['names']},"
        "'matches':{n:bool(os.getenv(n,'')) and os.getenv(n,'')==v "
        "for n,v in p['secrets'].items()}}))"
    )
    return json.loads(compose(
        "exec", "-T", service, "python", "-c", code,
        input_text=json.dumps({"names": names, "secrets": {n: os.getenv(n, "") for n in secrets}}),
    ))


def http_json(response: httpx.Response, label: str) -> dict:
    if response.status_code != 200:
        try:
            body = response.json()
        except ValueError:
            body = {}
        # Exclude Odoo debug and arbitrary raw response bodies.
        detail = {k: body[k] for k in ("error", "detail", "name", "message") if k in body} if isinstance(body, dict) else {}
        raise RuntimeError(f"{label}: HTTP {response.status_code}: {json.dumps(detail)}")
    return response.json()


def prechecks(client: httpx.Client, synthetic: bool) -> OdooAdapter:
    try:
        output = compose("ps", "--format", "json")
        # Compose versions return either JSON lines or an array.
        services = json.loads(output) if output.lstrip().startswith("[") else [json.loads(row) for row in output.splitlines() if row.strip()]
    except Exception as exc:
        check(False, "Compose services reachable", f"API, Odoo and worker cannot be verified: {exc}")
    check(True, "Compose services reachable")
    running = {row["Service"] for row in services if row.get("State") == "running"}
    missing = {"api", "worker", "odoo", "odoo-db", "db"} - running
    check(not missing, "Required services running",
          "missing: " + ", ".join(sorted(missing)) if missing else "api, worker, odoo, odoo-db, db")
    health = http_json(client.get(f"{GATEWAY}/health"), "GET /health")
    check(health.get("status") == "ok", "Gateway API reachable")
    worker = runtime_settings("worker", ["ERP_ADAPTER", "ODOO_BASE_URL", "ODOO_DB", "ODOO_EXPECTED_CURRENCY", "ODOO_WAREHOUSE_CODE"], ["ODOO_API_KEY"])
    settings = worker["settings"]
    check(settings["ERP_ADAPTER"].strip().lower() == "odoo", "Worker uses Odoo adapter",
          f"ERP_ADAPTER={settings['ERP_ADAPTER'] or 'unset'}")
    check(worker["matches"]["ODOO_API_KEY"], "Odoo API key configured and matches worker",
          "ODOO_API_KEY is missing or differs from the running worker" if not worker["matches"]["ODOO_API_KEY"] else "credentials not displayed")
    if synthetic:
        api = runtime_settings("api", ["LEGACY_AUTH"], ["SHOPIFY_WEBHOOK_SECRET"])
        check(api["matches"]["SHOPIFY_WEBHOOK_SECRET"], "Shopify signing secret configured and matches API")
    else:
        api = runtime_settings("api", ["LEGACY_AUTH"], [])
    check(api["settings"]["LEGACY_AUTH"].strip().lower() == "off" or bool(os.getenv("DEMO_API_KEY")),
          "Gateway read authentication configured", "LEGACY_AUTH requires DEMO_API_KEY" if api["settings"]["LEGACY_AUTH"].strip().lower() != "off" and not os.getenv("DEMO_API_KEY") else "ready")
    base = settings["ODOO_BASE_URL"].rstrip("/")
    check(bool(base), "Worker Odoo URL configured")
    # Compose's service DNS name is not resolvable from Windows. Use its published port.
    if base == "http://odoo:8069":
        base = "http://localhost:8069"
    check(urlsplit(base).hostname in {"localhost", "127.0.0.1", "::1"},
          "Odoo target is local", "This proof requires the local Compose Odoo instance")
    adapter = OdooAdapter(base_url=base, database=settings["ODOO_DB"] or "gateway",
                          expected_currency=settings["ODOO_EXPECTED_CURRENCY"] or "USD",
                          warehouse_code=settings["ODOO_WAREHOUSE_CODE"] or "WH")
    adapter._call("res.partner", "search_read", {"domain": [], "fields": ["id"], "limit": 1})
    check(True, "Odoo reachable and authenticated")
    return adapter


def products(adapter: OdooAdapter, skus: list[str], seed: bool = False) -> dict[str, int]:
    result = {}
    for sku in dict.fromkeys(skus):
        rows = adapter._call("product.product", "search_read", {
            "domain": [["default_code", "=", sku], ["active", "=", True]],
            "fields": ["id", "default_code"], "limit": 2,
        })
        if not rows and seed:
            adapter.set_opening_stock(sku, 100)
            rows = adapter._call("product.product", "search_read", {
                "domain": [["default_code", "=", sku], ["active", "=", True]],
                "fields": ["id", "default_code"], "limit": 2,
            })
        check(len(rows) == 1, f"Product SKU {sku} uniquely mapped", f"matches={len(rows)}")
        result[sku] = rows[0]["id"]
    return result


def read_order(client: httpx.Client, order_id: str) -> dict:
    headers = {"X-API-Key": os.environ["DEMO_API_KEY"]} if os.getenv("DEMO_API_KEY") else {}
    return http_json(client.get(f"{GATEWAY}/orders/{order_id}", headers=headers), "GET /orders/{id}")


def wait_confirmed(client: httpx.Client, order_id: str) -> dict:
    deadline = time.monotonic() + 120
    while True:
        order = read_order(client, order_id)
        say(f"Gateway status: {order.get('status')}")
        if order.get("status") == "CONFIRMED":
            check(True, "Gateway order CONFIRMED")
            return order
        # Stop on the first delivery failure rather than hiding a retry/outage.
        if order.get("status") in {"REJECTED", "FAILED_DEAD", "RETRYING", "CANCELLED", "PENDING_APPROVAL"}:
            failures = [e.get("details", {}) for e in order.get("audit_events", [])
                        if e.get("event_type") in {"order.failed", "order.retrying", "order.rejected"}]
            check(False, "Gateway order CONFIRMED", f"status={order.get('status')}; errors={json.dumps(failures)}")
        if time.monotonic() >= deadline:
            check(False, "Confirmation deadline", "120 seconds elapsed")
        time.sleep(min(1, max(0, deadline - time.monotonic())))


def sale_orders(adapter: OdooAdapter, order_id: str) -> list[dict]:
    return adapter._call("sale.order", "search_read", {
        "domain": [["client_order_ref", "=", f"GW-{order_id}"]],
        "fields": ["id", "name", "state", "client_order_ref", "partner_id", "currency_id"],
        "limit": 0,
    })


def verify_odoo(adapter: OdooAdapter, order: dict) -> dict:
    check(order.get("source") == "shopify", "Gateway source is Shopify")
    rows = sale_orders(adapter, order["order_id"])
    check(len(rows) == 1, "Exactly one Odoo sale order for gateway reference", f"count={len(rows)}")
    sale = rows[0]
    check(sale["state"] in {"sale", "done"}, "Odoo sale order confirmed", f"state={sale['state']}")
    check(sale["client_order_ref"] == f"GW-{order['order_id']}", "Odoo reference links to gateway order")
    confirmations = [e["details"].get("erp_order_id") for e in order.get("audit_events", []) if e.get("event_type") == "order.confirmed"]
    check(str(sale["id"]) in confirmations, "Gateway confirmation audit links to Odoo id")
    product_ids = products(adapter, [line["sku"] for line in order["lines"]])
    expected = Counter((product_ids[line["sku"]], Decimal(str(line["qty"])), Decimal(line["unit_price"])) for line in order["lines"])
    lines = adapter._call("sale.order.line", "search_read", {
        "domain": [["order_id", "=", sale["id"]]],
        "fields": ["product_id", "product_uom_qty", "price_unit", "display_type"], "limit": 0,
    })
    actual = Counter((line["product_id"][0], Decimal(str(line["product_uom_qty"])), Decimal(str(line["price_unit"])))
                     for line in lines if not line.get("display_type"))
    check(actual == expected, "Odoo lines, quantities and unit prices match gateway")
    check(sale["currency_id"][1] == order["currency"], "Odoo currency matches gateway")
    partners = adapter._call("res.partner", "read", {"ids": [sale["partner_id"][0]], "fields": ["name", "email", "phone"]})
    partner, customer = partners[0], order["customer"]
    email_match = bool(customer.get("email")) and str(partner.get("email", "")).casefold() == customer["email"].casefold()
    phone_match = bool(customer.get("phone")) and partner.get("name") == customer["name"] and partner.get("phone") == customer["phone"]
    check(email_match or phone_match, "Odoo customer matches gateway customer")
    say(f"Odoo order id: {sale['id']}; name: {sale['name']}")
    return sale


def synthetic(client: httpx.Client, adapter: OdooAdapter) -> None:
    skus = list(SKUS[:2])
    products(adapter, skus, seed=True)
    run_id = f"E2E-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:12]}"
    # Use an E2E- order number as requested for the synthetic fixture. Real
    # Shopify order_number is numeric; the mapper only consumes the numeric id.
    number = uuid.uuid4().int % 10**15
    payload = {
        "id": number, "order_number": run_id, "name": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "currency": adapter.expected_currency, "financial_status": "paid",
        "email": "E2E-shopify@example.com", "test": True,
        "customer": {"id": number + 1, "first_name": "E2E-Shopify", "last_name": "Customer", "email": "E2E-shopify@example.com"},
        "shipping_address": {"name": "E2E-Shopify Customer", "address1": "E2E-1 Test Street", "city": "E2E-Test City", "country_code": "US"},
        "line_items": [
            {"id": number + 2, "title": "E2E-Book", "sku": skus[0], "quantity": 2, "price": "10.00"},
            {"id": number + 3, "title": "E2E-Pen", "sku": skus[1], "quantity": 3, "price": "2.50"},
        ],
        "subtotal_price": "27.50", "total_price": "27.50", "total_tax": "0.00",
    }
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    signature = base64.b64encode(hmac.new(os.environ["SHOPIFY_WEBHOOK_SECRET"].encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")
    SECRET_VALUES.append(signature)
    headers = {"Content-Type": "application/json", "X-Shopify-Hmac-Sha256": signature,
               "X-Shopify-Webhook-Id": run_id, "X-Shopify-Topic": "orders/create"}
    say(f"Synthetic order name / webhook id: {run_id}")
    accepted = http_json(client.post(f"{GATEWAY}/webhooks/shopify/orders-create", content=body, headers=headers), "Shopify webhook")
    check(bool(accepted.get("order_id")), "Signed webhook accepted", f"status={accepted.get('status')}")
    order_id = str(uuid.UUID(accepted["order_id"]))
    say(f"Gateway order id: {order_id}")
    order = wait_confirmed(client, order_id)
    canonical = map_shopify_order(payload)
    check(order["external_ref"] == canonical["external_ref"] and order["customer"] == {**canonical["customer"], "phone": None}
          and order["currency"] == canonical["currency"]
          and Counter((x["sku"], x["qty"], Decimal(x["unit_price"])) for x in order["lines"])
          == Counter((x["sku"], x["qty"], Decimal(x["unit_price"])) for x in canonical["lines"]),
          "Shopify customer, reference, currency and lines mapped correctly")
    sale = verify_odoo(adapter, order)
    replay = http_json(client.post(f"{GATEWAY}/webhooks/shopify/orders-create", content=body, headers=headers), "Identical Shopify replay")
    check(replay.get("order_id") == order_id, "Identical replay returns same gateway order")
    # Re-read both systems after replay; the existing worker remains responsible
    # for delivery, with no direct call to create_sales_order from this script.
    final = read_order(client, order_id)
    check(final.get("status") == "CONFIRMED", "Gateway stays CONFIRMED after replay")
    rows = sale_orders(adapter, order_id)
    check(len(rows) == 1 and rows[0]["id"] == sale["id"] and rows[0]["state"] in {"sale", "done"},
          "Replay creates no second Odoo sale order", f"count={len(rows)}")


GATEWAY = os.getenv("GATEWAY_URL", "http://localhost:8002").rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-order", type=uuid.UUID, metavar="GATEWAY_ORDER_ID", help="read-only check of a real Shopify gateway order")
    args = parser.parse_args()
    say("Mode: check-order" if args.check_order else "Mode: synthetic")
    success = False
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            adapter = prechecks(client, synthetic=not bool(args.check_order))
            try:
                if args.check_order:
                    order = read_order(client, str(args.check_order))
                    say(f"Gateway status: {order.get('status')}")
                    check(order.get("status") == "CONFIRMED", "Gateway order CONFIRMED")
                    verify_odoo(adapter, order)
                else:
                    synthetic(client, adapter)
                success = True
            finally:
                adapter.client.close()
    except Exception as exc:
        if not CHECKS or CHECKS[-1][1] != "FAIL":
            CHECKS.append(("Execution", "FAIL"))
            say(f"FAIL: {type(exc).__name__}: {exc}")
    say("Check | Result")
    for label, result in CHECKS:
        say(f"{label} | {result}")
    say(f"FINAL: {'PASS' if success else 'FAIL'}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
