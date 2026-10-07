import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.schemas import OrderInput


class AdjustStockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID | None = None
    sku: str = Field(min_length=1)
    qty_delta: int = Field(strict=True, ne=0)
    reason: str = Field(min_length=1, max_length=200)


class CancelOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class Workflow:
    name: str
    description: str
    risk: Literal["low", "medium", "high"]
    request_roles: tuple[str, ...]
    executable: bool
    input_model: type[BaseModel]


WORKFLOWS: dict[str, Workflow] = {
    "create_order": Workflow(
        name="create_order",
        description="Accept a canonical order into the normal intake and queue.",
        risk="low",
        request_roles=("operator", "admin"),
        executable=True,
        input_model=OrderInput,
    ),
    "adjust_stock": Workflow(
        name="adjust_stock",
        description="Request a stock adjustment; execution is added in Module 7.",
        risk="high",
        request_roles=("operator", "admin"),
        executable=False,
        input_model=AdjustStockInput,
    ),
    "cancel_order": Workflow(
        name="cancel_order",
        description="Request order cancellation; execution is added in Module 7.",
        risk="medium",
        request_roles=("operator", "admin"),
        executable=False,
        input_model=CancelOrderInput,
    ),
}


def can_request(role: str, workflow: str) -> bool:
    registered = WORKFLOWS.get(workflow)
    return registered is not None and role in registered.request_roles
