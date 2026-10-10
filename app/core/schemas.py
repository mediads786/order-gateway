from decimal import Decimal
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

MAX_REQUEST_BODY_BYTES = 1048576
MAX_CUSTOMER_NAME_LENGTH = 200
MAX_SKU_LENGTH = 64
MAX_EXTERNAL_REF_LENGTH = 200
MAX_SHIPMENT_LINES = 200


def reject_nul(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL characters are not allowed")
    return value


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_CUSTOMER_NAME_LENGTH)
    phone: str | None = None
    email: EmailStr | None = None

    @field_validator("name", "phone", "email", mode="before")
    @classmethod
    def trim_contact_text(cls, value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("customer contact fields must be strings")
        return reject_nul(value).strip()

    @model_validator(mode="after")
    def contact_required(self) -> "CustomerInput":
        if not self.phone and not self.email:
            raise ValueError("customer.phone or customer.email is required")
        return self


class LineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=MAX_SKU_LENGTH)
    qty: int = Field(ge=1, le=1000000, strict=True)
    unit_price: Decimal = Field(ge=0, lt=1000000000, decimal_places=4, allow_inf_nan=False)

    @field_validator("sku", mode="before")
    @classmethod
    def trim_sku(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("sku must be a string")
        return reject_nul(value).strip()

    @field_validator("unit_price", mode="before")
    @classmethod
    def reject_float_price(cls, value: object) -> object:
        if isinstance(value, float):
            raise ValueError("unit_price must be a decimal value")
        return value


class OrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["web", "whatsapp", "shopify", "manual"]
    external_ref: str | None = Field(default=None, max_length=MAX_EXTERNAL_REF_LENGTH)
    customer: CustomerInput
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    lines: list[LineInput] = Field(min_length=1, max_length=200)

    @field_validator("external_ref")
    @classmethod
    def validate_external_ref(cls, value: str | None) -> str | None:
        return reject_nul(value) if value is not None else None


class ShipmentLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=MAX_SKU_LENGTH)
    qty: int = Field(gt=0, strict=True)

    @field_validator("sku", mode="before")
    @classmethod
    def trim_sku(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("sku must be a string")
        return reject_nul(value).strip()


class ShipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shipment_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    order_id: uuid.UUID
    lines: list[ShipmentLineInput] = Field(min_length=1, max_length=MAX_SHIPMENT_LINES)
