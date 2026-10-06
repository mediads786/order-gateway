import base64
import binascii
import hashlib
import hmac
from typing import Any


def verify_signature(secret: str, signature_header: str | None, raw_body: bytes) -> bool:
    if not secret or not signature_header:
        return False
    try:
        supplied = signature_header.encode("ascii")
        base64.b64decode(supplied, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return False
    expected = base64.b64encode(hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).digest())
    return hmac.compare_digest(expected, supplied)


class UnmappableShopifyOrder(ValueError):
    pass


def _usable_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _first_text(*values: Any) -> str | None:
    for value in values:
        usable = _usable_text(value)
        if usable is not None:
            return usable
    return None


def _address(payload: dict, name: str) -> dict:
    value = payload.get(name)
    return value if isinstance(value, dict) else {}


def map_shopify_order(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise UnmappableShopifyOrder("Shopify order must be a JSON object")

    shopify_id = payload.get("id")
    if shopify_id is None or shopify_id == "" or isinstance(shopify_id, bool):
        raise UnmappableShopifyOrder("Shopify order id is required")

    customer = _address(payload, "customer")
    shipping = _address(payload, "shipping_address")
    billing = _address(payload, "billing_address")
    customer_parts = [_usable_text(customer.get("first_name")), _usable_text(customer.get("last_name"))]
    customer_name = " ".join(part for part in customer_parts if part)
    customer_name = _first_text(customer_name, shipping.get("name"), billing.get("name"))
    if customer_name is None:
        raise UnmappableShopifyOrder("Shopify order has no usable customer name")

    email = _first_text(payload.get("email"), customer.get("email"))
    phone = _first_text(payload.get("phone"), customer.get("phone"), shipping.get("phone"))
    if email is None and phone is None:
        raise UnmappableShopifyOrder("Shopify order has no customer email or phone")

    currency = payload.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise UnmappableShopifyOrder("Shopify order currency is required")

    line_items = payload.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        raise UnmappableShopifyOrder("Shopify order must have line items")
    lines = []
    for item in line_items:
        if not isinstance(item, dict):
            raise UnmappableShopifyOrder("Shopify line item must be an object")
        sku = _usable_text(item.get("sku"))
        qty = item.get("quantity")
        unit_price = item.get("price")
        if sku is None:
            raise UnmappableShopifyOrder("Shopify line item SKU is required")
        if type(qty) is not int:
            raise UnmappableShopifyOrder("Shopify line item quantity must be an integer")
        if not isinstance(unit_price, str) or not unit_price.strip():
            raise UnmappableShopifyOrder("Shopify line item price must be a non-empty string")
        lines.append({"sku": sku, "qty": qty, "unit_price": unit_price})

    result = {
        "source": "shopify",
        "external_ref": str(shopify_id),
        "customer": {"name": customer_name},
        "currency": currency,
        "lines": lines,
    }
    if email is not None:
        result["customer"]["email"] = email
    if phone is not None:
        result["customer"]["phone"] = phone
    return result
