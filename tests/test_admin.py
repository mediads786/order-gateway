from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import uuid

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import AuditEvent, Job, Order
from app.db.session import SessionLocal
from app.workers.worker import process_one

TOKEN = "test-admin-token-0123456789abcdef"


def payload(name="Ada", ref="admin-test", sku="BOOK"):
    return {
        "source": "manual", "external_ref": ref,
        "customer": {"name": name, "email": "ada@example.com"},
        "currency": "USD", "lines": [{"sku": sku, "qty": 2, "unit_price": "19.99"}],
    }


def create_order(client, key="admin-test", **kwargs):
    response = client.post("/orders", json=payload(**kwargs), headers={"Idempotency-Key": key})
    assert response.status_code == 201
    return uuid.UUID(response.json()["order_id"])


def mark_dead(order_id, attempts=2):
    with SessionLocal.begin() as db:
        order = db.get(Order, order_id)
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        order.status = "FAILED_DEAD"
        job.status = "FAILED"
        job.attempts = attempts


def login(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    response = client.post("/admin/login", data={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    return response


@pytest.mark.parametrize("token", ["", "x" * 15])
def test_admin_disabled_for_all_routes(client, monkeypatch, token):
    monkeypatch.setenv("ADMIN_TOKEN", token)
    order_id = create_order(client, f"admin-disabled-{len(token)}")
    mark_dead(order_id)
    with SessionLocal() as db:
        counts_before = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Order, Job, AuditEvent))
    requests = [
        client.get("/admin"), client.get("/admin/login"), client.post("/admin/login", data={"token": TOKEN}),
        client.get("/admin/orders"), client.get(f"/admin/orders/{order_id}"),
        client.post("/admin/logout"), client.post(f"/admin/orders/{order_id}/retry"),
    ]
    assert all(response.status_code == 503 and response.json() == {"error": "admin_disabled"} for response in requests)
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "FAILED_DEAD"
        counts_after = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Order, Job, AuditEvent))
    assert counts_after == counts_before


def test_admin_unauthenticated_access_and_post_retry_change_nothing(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    order_id = create_order(client, "admin-unauth")
    mark_dead(order_id)
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.order_id == order_id))
    assert client.get("/admin/orders", follow_redirects=False).status_code == 303
    assert client.get(f"/admin/orders/{order_id}", follow_redirects=False).headers["location"] == "/admin/login"
    assert client.post(f"/admin/orders/{order_id}/retry").status_code == 401
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "FAILED_DEAD"
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.order_id == order_id)) == before


def test_admin_login_cookie_validation_and_logout(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    wrong = client.post("/admin/login", data={"token": "wrong"}, follow_redirects=False)
    missing = client.post("/admin/login", data={}, follow_redirects=False)
    assert wrong.status_code == missing.status_code == 401
    assert "set-cookie" not in wrong.headers
    valid = client.post("/admin/login", data={"token": TOKEN}, follow_redirects=False)
    assert valid.status_code == 303 and valid.headers["location"] == "/admin/orders"
    cookie = valid.headers["set-cookie"]
    assert "gw_admin=" in cookie and "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower() and "path=/admin" in cookie.lower()
    assert TOKEN not in cookie
    assert client.get("/admin/orders").status_code == 200
    client.cookies.set("gw_admin", "")
    assert client.get("/admin/orders", follow_redirects=False).status_code == 303
    client.cookies.set("gw_admin", "forged")
    assert client.get("/admin/orders", follow_redirects=False).status_code == 303
    client.cookies.clear()
    login(client, monkeypatch)
    logout = client.post("/admin/logout", follow_redirects=False)
    assert logout.status_code == 303 and logout.headers["location"] == "/admin/login"
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert client.get("/admin/orders", follow_redirects=False).status_code == 303


def test_admin_security_headers_and_no_scripts(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    order_id = create_order(client, "admin-headers")
    login(client, monkeypatch)
    responses = [client.get("/admin/orders"), client.get(f"/admin/orders/{order_id}"), client.get("/admin/login")]
    for response in responses:
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"
        assert "<script" not in response.text.lower()


def test_admin_list_status_counts_filter_order_and_pagination(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    login(client, monkeypatch)
    states = ("RECEIVED", "REJECTED", "QUEUED", "PROCESSING", "RETRYING", "CONFIRMED", "FAILED_DEAD")
    ids = []
    for index in range(30):
        order_id = create_order(client, f"admin-list-{index}", ref=f"ref-{index}")
        ids.append(order_id)
        state = states[index % len(states)]
        with SessionLocal.begin() as db:
            db.get(Order, order_id).status = state
            db.get(Order, order_id).created_at = datetime.now(timezone.utc) + timedelta(seconds=index)
            if state == "FAILED_DEAD":
                db.scalar(select(Job).where(Job.order_id == order_id)).status = "FAILED"
    listing = client.get("/admin/orders?page=abc")
    assert listing.status_code == 200 and "Page 1" in listing.text and "Next" in listing.text
    assert listing.text.count("<tr>") == 26
    first_data_row = listing.text.split("<tbody>", 1)[1].split("</tr>", 1)[0]
    assert f'href="/admin/orders/{ids[-1]}"' in first_data_row
    second = client.get("/admin/orders?page=2")
    assert second.text.count("<tr>") == 6 and "Prev" in second.text
    assert "FAILED_DEAD (4)" in listing.text and "RECEIVED (5)" in listing.text
    filtered = client.get("/admin/orders?status=FAILED_DEAD")
    assert "Filter: FAILED_DEAD" in filtered.text
    assert filtered.text.count("<tr>") == 5
    assert client.get("/admin/orders?status=unknown&page=0").status_code == 200
    assert "Page 1" in client.get("/admin/orders?status=unknown&page=0").text


def test_admin_detail_and_unknown_ids(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    order_id = create_order(client, "admin-detail", name="Detail User")
    httpx.post("http://127.0.0.1:9001/admin/faults", json={"mode": "error_500"}, timeout=3)
    assert process_one()
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    httpx.post("http://127.0.0.1:9001/admin/faults", json={"mode": "none"}, timeout=3)
    assert process_one()
    login(client, monkeypatch)
    detail = client.get(f"/admin/orders/{order_id}")
    assert detail.status_code == 200
    assert "BOOK" in detail.text and "Job" in detail.text and "Audit trail" in detail.text
    assert detail.text.index("order.received") < detail.text.index("order.queued")
    event_order = ["order.received", "order.queued", "order.processing", "order.retrying", "order.processing", "order.confirmed"]
    audit_html = detail.text.split("Audit trail", 1)[1]
    positions = []
    cursor = 0
    for event_type in event_order:
        cursor = audit_html.index(event_type, cursor)
        positions.append(cursor)
    assert positions == sorted(positions)
    assert client.get("/admin/orders/not-a-uuid").status_code == 404
    assert client.get(f"/admin/orders/{uuid.uuid4()}").status_code == 404


def test_admin_retry_visibility_xss_flash_and_read_only_gets(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    dead_id = create_order(client, "admin-xss", name='<script>alert(1)</script>', ref='"><img src=x>', sku='<script>alert(1)</script>')
    mark_dead(dead_id)
    with SessionLocal.begin() as db:
        job = db.scalar(select(Job).where(Job.order_id == dead_id))
        job.last_error = '<script>alert(1)</script> "><img src=x>'
        db.add(AuditEvent(order_id=dead_id, event_type="order.failed", details={"text": '<script>alert(1)</script> "><img src=x>'}))
    login(client, monkeypatch)
    with SessionLocal() as db:
        counts_before = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Order, Job, AuditEvent))
    listing = client.get("/admin/orders")
    detail = client.get(f"/admin/orders/{dead_id}")
    client.get("/admin/login")
    assert "Retry" in detail.text
    assert "&lt;script&gt;" in listing.text and "&lt;script&gt;" in detail.text
    assert '<script>alert(1)</script>' not in listing.text + detail.text
    assert "&lt;img" in detail.text
    assert "Order requeued." in client.get(f"/admin/orders/{dead_id}?msg=requeued").text
    malicious = client.get(f"/admin/orders/{dead_id}?msg=%3Cscript%3E")
    assert "<script>" not in malicious.text and "%3Cscript%3E" not in malicious.text
    with SessionLocal() as db:
        counts_after = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Order, Job, AuditEvent))
    assert counts_after == counts_before
    live_id = create_order(client, "admin-live-button")
    assert "Retry" not in client.get(f"/admin/orders/{live_id}").text


def test_admin_retry_requeues_dead_order_and_worker_confirms(client, monkeypatch):
    from app.workers import worker

    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    order_id = create_order(client, "admin-retry")
    mark_dead(order_id, attempts=1)
    login(client, monkeypatch)
    response = client.post(f"/admin/orders/{order_id}/retry", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("?msg=requeued")
    with SessionLocal() as db:
        order = db.get(Order, order_id)
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        event = db.scalar(select(AuditEvent).where(AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued"))
        assert (order.status, job.status, job.attempts) == ("QUEUED", "QUEUED", 0)
        assert event.details == {"previous_attempts": 1, "via": "admin"}
    assert process_one()
    with SessionLocal() as db:
        assert db.get(Order, order_id).status == "CONFIRMED"


@pytest.mark.parametrize("state", ["CONFIRMED", "RETRYING", "QUEUED"])
def test_admin_retry_non_dead_is_noop(client, monkeypatch, state):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    order_id = create_order(client, f"admin-not-dead-{state}")
    with SessionLocal.begin() as db:
        db.get(Order, order_id).status = state
        job = db.scalar(select(Job).where(Job.order_id == order_id))
        job.status = "DONE" if state == "CONFIRMED" else "QUEUED"
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.order_id == order_id))
    login(client, monkeypatch)
    response = client.post(f"/admin/orders/{order_id}/retry", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].endswith("?msg=not_dead")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.order_id == order_id)) == before


def test_two_simultaneous_admin_retries_requeue_once(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    order_id = create_order(client, "admin-retry-race")
    mark_dead(order_id)
    login(client, monkeypatch)
    barrier = Barrier(2)

    def retry_together(_):
        barrier.wait(timeout=10)
        return client.post(f"/admin/orders/{order_id}/retry", follow_redirects=False).headers["location"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        locations = list(pool.map(retry_together, range(2)))
    assert sorted(locations) == [f"/admin/orders/{order_id}?msg=not_dead", f"/admin/orders/{order_id}?msg=requeued"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued",
        )) == 1


def test_api_retry_remains_unchanged_without_admin_via(client):
    order_id = create_order(client, "api-retry-unchanged")
    mark_dead(order_id, attempts=1)
    response = client.post(f"/orders/{order_id}/retry")
    assert response.status_code == 200 and response.json() == {"order_id": str(order_id), "status": "QUEUED"}
    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.order_id == order_id, AuditEvent.event_type == "order.requeued"))
        assert event.details == {"previous_attempts": 1}


@pytest.mark.parametrize("missing", ["ODOO_BASE_URL", "ODOO_API_KEY"])
def test_shipments_missing_odoo_settings_returns_503(client, monkeypatch, missing):
    import base64
    import hashlib
    import hmac
    import json

    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("ERP_ADAPTER", "odoo")
    monkeypatch.setenv("ODOO_BASE_URL", "http://odoo:8069")
    monkeypatch.setenv("ODOO_API_KEY", "configured")
    monkeypatch.setenv(missing, "")
    monkeypatch.setenv("SHIPMENT_WEBHOOK_SECRET", "test-shipment-secret")
    body = json.dumps({"shipment_id": "SHP-MISSING-CONFIG", "order_id": str(uuid.uuid4()), "lines": [{"sku": "BOOK", "qty": 1}]}).encode()
    signature = base64.b64encode(hmac.new(b"test-shipment-secret", body, hashlib.sha256).digest()).decode()
    response = client.post("/shipments", content=body, headers={"X-Gateway-Signature": signature})
    assert response.status_code == 503 and response.json() == {"error": "erp_adapter_not_configured"}
    with SessionLocal() as db:
        from app.db.models import Shipment
        assert db.scalar(select(func.count()).select_from(Shipment)) == 0
