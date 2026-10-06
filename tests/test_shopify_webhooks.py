import base64
import hashlib
import hmac
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.db.models import AuditEvent, IdempotencyKey, Job, Order
from app.db.session import SessionLocal
from app.services.shopify_webhooks import UnmappableShopifyOrder, map_shopify_order, verify_signature

TEST_SECRET = "shopify-test-secret"
FIXTURES = Path(__file__).parent / "fixtures"


def sign_body(raw_body: bytes, secret: str = TEST_SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_verify_signature_accepts_correct_signature():
    body = b'{"id":123}'
    assert verify_signature(TEST_SECRET, sign_body(body), body) is True


@pytest.mark.parametrize("signature", ["", None, "not-a-signature", "é"])
def test_verify_signature_rejects_invalid_signature_headers(signature):
    assert verify_signature(TEST_SECRET, signature, b'{"id":123}') is False


def test_verify_signature_rejects_wrong_secret():
    body = b'{"id":123}'
    assert verify_signature("another-secret", sign_body(body), body) is False


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_map_shopify_order_maps_canonical_fields_and_ignores_extras():
    mapped = map_shopify_order(_fixture("order_full.json"))
    assert mapped == {
        "source": "shopify",
        "external_ref": "820982911946154508",
        "customer": {"name": "Ayesha Khan", "email": "ayesha.khan@example.com", "phone": "+923001234567"},
        "currency": "PKR",
        "lines": [
            {"sku": "TSHIRT-BLK-M", "qty": 2, "unit_price": "1500.00"},
            {"sku": "MUG-WHT", "qty": 1, "unit_price": "2400.00"},
        ],
    }


def test_map_shopify_order_uses_shipping_and_billing_fallbacks():
    shipping = map_shopify_order(_fixture("order_fallbacks.json"))
    assert shipping["customer"] == {"name": "Bilal Ahmed", "phone": "+923451112233"}
    assert shipping["lines"][0]["sku"] == "NOTEBOOK-A5"
    billing_payload = _fixture("order_full.json")
    billing_payload["customer"]["first_name"] = " "
    billing_payload["customer"]["last_name"] = ""
    billing_payload["email"] = billing_payload["phone"] = ""
    billing_payload["shipping_address"]["name"] = ""
    billing_payload["shipping_address"]["phone"] = ""
    billing = map_shopify_order(billing_payload)
    assert billing["customer"] == {
        "name": "Ayesha Khan", "email": "ayesha.khan@example.com", "phone": "+923001234567",
    }


@pytest.mark.parametrize("fixture_name", ["order_no_sku.json", "order_no_sku_null.json", "order_no_contact.json"])
def test_map_shopify_order_signals_unmappable_required_fields(fixture_name):
    payload = _fixture(fixture_name)
    with pytest.raises(UnmappableShopifyOrder):
        map_shopify_order(payload)


def test_map_shopify_order_rejects_when_all_name_sources_are_unusable():
    payload = _fixture("order_fallbacks.json")
    payload["shipping_address"]["name"] = " "
    payload["billing_address"]["name"] = ""
    with pytest.raises(UnmappableShopifyOrder):
        map_shopify_order(payload)


def _headers(body: bytes, webhook_id: str = "wh-test-1", topic: str | None = "orders/create") -> dict:
    headers = {
        "X-Shopify-Hmac-Sha256": sign_body(body),
        "X-Shopify-Webhook-Id": webhook_id,
    }
    if topic is not None:
        headers["X-Shopify-Topic"] = topic
    return headers


def _counts() -> tuple[int, int, int, int]:
    with SessionLocal() as db:
        return tuple(
            db.scalar(select(func.count()).select_from(model))
            for model in (Order, Job, IdempotencyKey, AuditEvent)
        )


def _fixture_body(name: str = "order_full.json") -> bytes:
    return (FIXTURES / name).read_bytes()


def test_valid_signed_webhook_creates_order_job_then_worker_confirms(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    response = client.post("/webhooks/shopify/orders-create", content=_fixture_body(), headers=_headers(_fixture_body()))
    assert response.status_code == 200
    assert response.json()["status"] == "RECEIVED"
    with SessionLocal() as db:
        orders = db.scalars(select(Order)).all()
        jobs = db.scalars(select(Job)).all()
        assert len(orders) == len(jobs) == 1
        order = orders[0]
        assert order.source == "shopify"
        assert order.external_ref == "820982911946154508"
        assert order.total == Decimal("5400.00")
        assert jobs[0].order_id == order.order_id
    from app.workers.worker import process_one
    assert process_one() is True
    with SessionLocal() as db:
        assert db.get(Order, order.order_id).status == "CONFIRMED"


def test_same_webhook_replay_returns_same_order_without_duplicates(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    headers = _headers(body, webhook_id="same-webhook-id")
    first = client.post("/webhooks/shopify/orders-create", content=body, headers=headers)
    second = client.post("/webhooks/shopify/orders-create", content=body, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert _counts()[:2] == (1, 1)
    with SessionLocal() as db:
        assert db.get(IdempotencyKey, "shopify:same-webhook-id") is not None


def test_bad_signature_does_not_store_anything(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    before = _counts()
    response = client.post("/webhooks/shopify/orders-create", content=body,
                           headers={**_headers(body), "X-Shopify-Hmac-Sha256": "bad-signature"})
    assert response.status_code == 401
    assert _counts() == before


def test_missing_signature_and_tampered_body_do_not_store_anything(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    before = _counts()
    headers = _headers(body)
    headers.pop("X-Shopify-Hmac-Sha256")
    assert client.post("/webhooks/shopify/orders-create", content=body, headers=headers).status_code == 401
    changed_body = body + b" "
    assert client.post("/webhooks/shopify/orders-create", content=changed_body,
                       headers=_headers(body, webhook_id="tampered")).status_code == 401
    assert _counts() == before


def test_raw_body_signature_preserves_whitespace_and_unicode(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = ('{\n  "id": 809, "email": "zoe@example.com", "currency": "USD",\n'
            '  "customer": {"first_name": "Zoë", "last_name": "Ng"},\n'
            '  "line_items": [{"sku": "TEA", "quantity": 1, "price": "3.40"}],\n'
            '  "total_price": "999.00", "note": "sample, not live data"\n}').encode("utf-8")
    response = client.post("/webhooks/shopify/orders-create", content=body, headers=_headers(body, "raw-bytes"))
    assert response.status_code == 200
    with SessionLocal() as db:
        order = db.scalar(select(Order))
        assert order.customer_name == "Zoë Ng"
        assert order.total == Decimal("3.40")


def test_missing_webhook_id_is_400_without_storage(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    headers = _headers(body, webhook_id=" ")
    before = _counts()
    assert client.post("/webhooks/shopify/orders-create", content=body, headers=headers).status_code == 400
    assert _counts() == before


def test_other_topic_is_ignored_without_storage(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    before = _counts()
    response = client.post("/webhooks/shopify/orders-create", content=body,
                           headers=_headers(body, topic="orders/updated"))
    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
    assert _counts() == before


@pytest.mark.parametrize("secret", [None, ""])
def test_missing_or_empty_webhook_secret_fails_closed(client, monkeypatch, secret):
    if secret is None:
        monkeypatch.delenv("SHOPIFY_WEBHOOK_SECRET", raising=False)
    else:
        monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", secret)
    body = _fixture_body()
    before = _counts()
    assert client.post("/webhooks/shopify/orders-create", content=body, headers=_headers(body)).status_code == 503
    assert _counts() == before


@pytest.mark.parametrize("kind", ["no_sku", "no_sku_null", "invalid_json"])
def test_signed_unmappable_or_non_json_payload_is_acknowledged_as_rejected(client, monkeypatch, kind):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    if kind in ("no_sku", "no_sku_null"):
        body = _fixture_body(f"order_{kind}.json")
    else:
        body = b'{not valid "json"'
    response = client.post("/webhooks/shopify/orders-create", content=body,
                           headers=_headers(body, webhook_id=f"rejected-{kind}"))
    assert response.status_code == 200
    assert response.json()["status"] == "REJECTED"
    with SessionLocal() as db:
        rejected = db.scalar(select(Order).where(Order.status == "REJECTED"))
        assert rejected is not None
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        event = db.scalar(select(AuditEvent).where(
            AuditEvent.order_id == rejected.order_id, AuditEvent.event_type == "order.rejected",
        ))
        assert event is not None


def test_lowercase_shopify_header_names_work(client, monkeypatch):
    monkeypatch.setenv("SHOPIFY_WEBHOOK_SECRET", TEST_SECRET)
    body = _fixture_body()
    response = client.post("/webhooks/shopify/orders-create", content=body, headers={
        "x-shopify-hmac-sha256": sign_body(body),
        "x-shopify-webhook-id": "lowercase-headers",
        "x-shopify-topic": "orders/create",
    })
    assert response.status_code == 200
    assert _counts()[:2] == (1, 1)
