import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import OrderInput


class AdjustStockInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID | None = None
    sku: str = Field(min_length=1)
    qty_delta: int = Field(strict=True)
    reason: str = Field(min_length=1, max_length=200)

    @field_validator("qty_delta")
    @classmethod
    def reject_zero_delta(cls, value: int) -> int:
        if value == 0:
            raise ValueError("qty_delta must not be zero")
        return value


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
    approval: Literal["never", "conditional", "always"]
    decision_roles: tuple[str, ...]
    input_model: type[BaseModel]


WORKFLOWS: dict[str, Workflow] = {
    "create_order": Workflow(
        name="create_order",
        description="Accept a canonical order into the normal intake and queue.",
        risk="low",
        request_roles=("operator", "admin"),
        executable=True,
        approval="conditional",
        decision_roles=("approver", "admin"),
        input_model=OrderInput,
    ),
    "adjust_stock": Workflow(
        name="adjust_stock",
        description="Request a stock adjustment for approval.",
        risk="high",
        request_roles=("operator", "admin"),
        executable=True,
        approval="always",
        decision_roles=("approver", "admin"),
        input_model=AdjustStockInput,
    ),
    "cancel_order": Workflow(
        name="cancel_order",
        description="Request order cancellation; not executable in this version.",
        risk="medium",
        request_roles=("operator", "admin"),
        executable=False,
        approval="always",
        decision_roles=("approver", "admin"),
        input_model=CancelOrderInput,
    ),
}


def can_request(role: str, workflow: str) -> bool:
    registered = WORKFLOWS.get(workflow)
    return registered is not None and role in registered.request_roles


def can_decide(role: str, workflow: str) -> bool:
    registered = WORKFLOWS.get(workflow)
    return registered is not None and role in registered.decision_roles
