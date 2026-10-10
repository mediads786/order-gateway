from decimal import Decimal
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    phone: str | None = None
    email: EmailStr | None = None

    @field_validator("name", "phone", "email", mode="before")
    @classmethod
    def trim_contact_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("customer contact fields must be strings")
        return value.strip()

    @model_validator(mode="after")
    def contact_required(self) -> "CustomerInput":
        if not self.phone and not self.email:
            raise ValueError("customer.phone or customer.email is required")
        return self


class LineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    qty: int = Field(ge=1, le=1000000, strict=True)
    unit_price: Decimal = Field(ge=0, lt=1000000000, decimal_places=4, allow_inf_nan=False)

    @field_validator("sku", mode="before")
    @classmethod
    def trim_sku(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("sku must be a string")
        return value.strip()

    @field_validator("unit_price", mode="before")
    @classmethod
    def reject_float_price(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("unit_price must be a decimal value")
        return value


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["web", "whatsapp", "shopify", "manual"]
    external_ref: str | None = None
    customer: CustomerInput
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    lines: list[LineInput] = Field(min_length=1, max_length=200)


class ShipmentLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    qty: int = Field(gt=0, strict=True)

    @field_validator("sku", mode="before")
    @classmethod
    def trim_sku(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("sku must be a string")
        return value.strip()


class ShipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    order_id: uuid.UUID
    lines: list[ShipmentLineInput] = Field(min_length=1)
