import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx


GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8002").rstrip("/")
MOCK_ERP_URL = os.getenv("MOCK_ERP_URL", "http://127.0.0.1:9001").rstrip("/")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def say(message: str) -> None:
    print(message.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


def expect(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def api_headers() -> dict[str, str]:
    headers = {}
    api_key = os.getenv("DEMO_API_KEY")
    if api_key:
        headers["X-API-Key"] = api_key
    return headers


def set_faults(client: httpx.Client, mode: str, fail_rate: int) -> None:
    response = client.post(
        f"{MOCK_ERP_URL}/admin/faults",
        json={"mode": mode, "fail_rate": fail_rate, "latency_ms": 0},
    )
    expect(response.status_code == 200, f"mock ERP fault reset/configuration returned HTTP {response.status_code}")


def post_order(client: httpx.Client, suffix: str) -> tuple[dict, dict, str]:
    key = f"demo-{suffix}-{uuid.uuid4()}"
    payload = {
        "source": "manual",
        "external_ref": f"demo-{suffix}-{uuid.uuid4()}",
        "customer": {"name": "Demo Customer", "email": "demo@example.com"},
        "currency": "USD",
        "lines": [{"sku": "BOOK", "qty": 1, "unit_price": "1.00"}],
    }
    response = client.post(
        f"{GATEWAY_URL}/orders",
        json=payload,
        headers={**api_headers(), "Idempotency-Key": key},
    )
    expect(response.status_code in (200, 201), f"POST /orders returned HTTP {response.status_code}: {response.text}")
    body = response.json()
    expect(isinstance(body, dict) and bool(body.get("order_id")), "POST /orders response omitted order_id")
    return payload, body, key


def get_order(client: httpx.Client, order_id: str) -> dict:
    response = client.get(f"{GATEWAY_URL}/orders/{order_id}", headers=api_headers())
    expect(response.status_code == 200, f"GET /orders/{order_id} returned HTTP {response.status_code}: {response.text}")
    return response.json()


def wait_for_status(client: httpx.Client, order_id: str, target: str, timeout: int) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        order = get_order(client, order_id)
        status = order.get("status")
        if status == target:
            return order
        if status == "FAILED_DEAD":
            raise RuntimeError(f"order {order_id} reached FAILED_DEAD while waiting for {target}")
        time.sleep(1)
    raise RuntimeError(f"order {order_id} did not reach {target} within {timeout} seconds")


def print_second_order_audit(order_id: str) -> None:
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "-m", "scripts.demo_audit", order_id],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    stdout = result.stdout.encode("ascii", "backslashreplace").decode("ascii")
    stderr = result.stderr.encode("ascii", "backslashreplace").decode("ascii")
    if stdout:
        print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)
    if stderr:
        say(stderr.rstrip())
    expect(result.returncode == 0, f"audit command exited with status {result.returncode}")
    expect("order.retrying" in stdout, "second order audit trail omitted order.retrying")
    expect("order.confirmed" in stdout, "second order audit trail omitted order.confirmed")


def run_demo(client: httpx.Client) -> None:
    say("1. Check gateway health")
    health = client.get(f"{GATEWAY_URL}/health")
    expect(health.status_code == 200 and health.json().get("status") == "ok", "GET /health did not return status ok")

    say("2. Submit first order and wait for confirmation")
    first_payload, first_response, first_key = post_order(client, "confirmed")
    first_id = str(first_response["order_id"])
    wait_for_status(client, first_id, "CONFIRMED", 60)

    say("3. Replay first order to verify idempotency")
    replay = client.post(
        f"{GATEWAY_URL}/orders",
        json=first_payload,
        headers={**api_headers(), "Idempotency-Key": first_key},
    )
    expect(replay.status_code in (200, 201), f"idempotent replay returned HTTP {replay.status_code}: {replay.text}")
    expect(replay.json().get("order_id") == first_id, "idempotent replay returned a different order_id")

    say("4. Inject ERP failure and wait for retry")
    set_faults(client, "error_500", 1)
    _, second_response, _ = post_order(client, "retry")
    second_id = str(second_response["order_id"])
    wait_for_status(client, second_id, "RETRYING", 60)

    say("5. Clear ERP fault and wait for confirmation")
    set_faults(client, "none", 0)
    wait_for_status(client, second_id, "CONFIRMED", 90)

    say("6. Print second order audit trail")
    print_second_order_audit(second_id)


def main() -> int:
    failure = None
    try:
        with httpx.Client(timeout=10) as client:
            try:
                run_demo(client)
            except Exception as exc:
                failure = str(exc) or type(exc).__name__
            finally:
                try:
                    set_faults(client, "none", 0)
                except Exception as exc:
                    reset_failure = f"failed to reset mock ERP faults: {exc}"
                    failure = f"{failure}; {reset_failure}" if failure else reset_failure
    except Exception as exc:
        failure = failure or (str(exc) or type(exc).__name__)

    if failure:
        say(f"DEMO FAILED: {failure}")
        return 1
    say("DEMO OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
