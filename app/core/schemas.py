from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class CustomerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    phone: str | None = None
    email: EmailStr | None = None

    @field_validator("name", "phone", mode="before")
    @classmethod
    def trim_contact_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def contact_required(self) -> "CustomerInput":
        if not self.phone and not self.email:
            raise ValueError("customer.phone or customer.email is required")
        return self


class LineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1)
    qty: int = Field(gt=0, strict=True)
    unit_price: Decimal = Field(ge=0)

    @field_validator("sku", mode="before")
    @classmethod
    def trim_sku(cls, value: str) -> str:
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
    lines: list[LineInput] = Field(min_length=1)
