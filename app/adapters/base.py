from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
import uuid


@dataclass(frozen=True)
class AdapterLine:
    sku: str
    qty: int
    unit_price: Decimal


@dataclass(frozen=True)
class AdapterOrder:
    order_id: uuid.UUID
    job_id: uuid.UUID
    external_id: str
    customer: dict[str, str | None]
    currency: str
    lines: list[AdapterLine]
    total: Decimal


@dataclass(frozen=True)
class ShipmentLine:
    sku: str
    qty: int


@dataclass(frozen=True)
class ErpOrderResult:
    erp_order_id: str
    duplicate: bool


class NonRetryableAdapterError(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class ErpAdapter(Protocol):
    def create_sales_order(self, order: AdapterOrder) -> ErpOrderResult: ...

    def adjust_stock_for_shipment(self, shipment_id: str, lines: list[ShipmentLine]) -> str: ...

    def adjust_stock(self, reference: str, sku: str, qty_delta: int, reason: str) -> dict: ...

    def cancel_order(self, reference: str, erp_order_id: str, reason: str) -> dict: ...
