import httpx
import pytest

from app.adapters.base import NonRetryableAdapterError
from app.adapters.odoo import OdooAdapter


@pytest.mark.parametrize(
    "exception_name",
    ["builtins.TypeError", "builtins.KeyError", "builtins.AttributeError"],
)
def test_builtin_odoo_argument_errors_are_non_retryable(exception_name: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"name": exception_name, "message": "invalid arguments"},
            request=request,
        )

    adapter = OdooAdapter(
        base_url="http://odoo",
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(respond)),
    )

    with pytest.raises(NonRetryableAdapterError, match="invalid arguments"):
        adapter._call("res.partner", "search_read", {})

    adapter.client.close()
