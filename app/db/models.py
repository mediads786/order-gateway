import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String, nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    erp_order_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_payload: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="OrderLine.line_id")
    events: Mapped[list["AuditEvent"]] = relationship(back_populates="order", order_by="AuditEvent.event_id")


class OrderLine(Base):
    __tablename__ = "order_lines"

    line_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), nullable=False)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric, nullable=False)
    order: Mapped[Order] = relationship(back_populates="lines")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), nullable=False)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    order: Mapped[Order] = relationship(back_populates="events")


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    order: Mapped[Order] = relationship()


class Shipment(Base):
    __tablename__ = "shipments"

    shipment_id: Mapped[str] = mapped_column(Text, primary_key=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.order_id"), nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    lines: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    erp_reference: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint("role IN ('operator', 'approver', 'admin')", name="ck_api_keys_role"),
    )

    key_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_keys.key_id"), nullable=True)
    actor_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str | None] = mapped_column(Text, nullable=True)
    workflow: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.order_id"), nullable=True)
    input_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
