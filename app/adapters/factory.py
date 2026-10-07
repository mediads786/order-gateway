import os

from app.adapters.base import ErpAdapter
from app.adapters.mock import MockErpAdapter


def get_adapter() -> ErpAdapter:
    kind = os.getenv("ERP_ADAPTER", "mock").strip().lower()
    if kind == "mock":
        return MockErpAdapter()
    if kind == "odoo":
        base_url = os.getenv("ODOO_BASE_URL", "").strip()
        api_key = os.getenv("ODOO_API_KEY", "").strip()
        if not base_url or not api_key:
            raise RuntimeError("ERP_ADAPTER=odoo requires ODOO_BASE_URL and ODOO_API_KEY")
        from app.adapters.odoo import OdooAdapter

        return OdooAdapter(base_url=base_url, api_key=api_key)
    raise RuntimeError(f"Unsupported ERP_ADAPTER value: {kind!r}; expected 'mock' or 'odoo'")
