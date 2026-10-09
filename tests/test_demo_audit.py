from datetime import datetime
import json
import uuid

from app.services.orders import submit_order
from scripts.demo_audit import fetch_audit_lines


def test_fetch_audit_lines_are_time_ordered_and_include_received():
    payload = {
        "source": "manual",
        "external_ref": f"demo-audit-{uuid.uuid4()}",
        "customer": {"name": "Demo Audit", "email": "audit@example.com"},
        "currency": "USD",
        "lines": [{"sku": "BOOK", "qty": 1, "unit_price": "1.00"}],
    }
    status, response = submit_order(
        json.dumps(payload).encode("utf-8"),
        f"demo-audit-{uuid.uuid4()}",
    )
    assert status == 201

    lines = fetch_audit_lines(response["order_id"])
    timestamps = [
        datetime.fromisoformat(line.split(" ", 1)[0].replace("Z", "+00:00"))
        for line in lines
    ]
    assert timestamps == sorted(timestamps)
    assert any(" order.received " in line for line in lines)
