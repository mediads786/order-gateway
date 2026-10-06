import asyncio
import random
import threading
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

app = FastAPI()
lock = threading.Lock()
fault_mode: Literal["none", "error_500", "rate_limit_429", "error_422", "timeout", "slow"] = "none"
fault_fail_rate = 0.0
fault_latency_ms = 0
sales_orders: dict[str, dict] = {}
external_ids: dict[str, str] = {}


class SalesOrderInput(BaseModel):
    external_id: str
    customer: dict
    currency: str
    lines: list[dict]
    total: Decimal


class FaultInput(BaseModel):
    mode: Literal["none", "error_500", "rate_limit_429", "error_422", "timeout", "slow"] = "none"
    fail_rate: float = Field(default=0, ge=0, le=1)
    latency_ms: int = Field(default=0, ge=0)


@app.post("/sales-orders")
async def create_sales_order(order: SalesOrderInput):
    with lock:
        mode = fault_mode
        fail_rate = fault_fail_rate
        latency_ms = fault_latency_ms
    if latency_ms:
        await asyncio.sleep(latency_ms / 1000)
    if mode == "error_500":
        raise HTTPException(status_code=500, detail="Injected ERP error")
    if mode == "rate_limit_429":
        raise HTTPException(status_code=429, detail="Injected ERP rate limit")
    if mode == "error_422":
        raise HTTPException(status_code=422, detail="Injected ERP validation error")
    if mode == "timeout":
        await asyncio.sleep(10)
        raise HTTPException(status_code=504, detail="Injected ERP timeout")
    if mode == "slow":
        await asyncio.sleep(1)
    if random.random() < fail_rate:
        raise HTTPException(status_code=500, detail="Injected random ERP error")
    with lock:
        existing_id = external_ids.get(order.external_id)
        if existing_id:
            return JSONResponse(status_code=409, content={"erp_order_id": existing_id})
        erp_order_id = str(uuid.uuid4())
        stored_order = order.model_dump(mode="json")
        stored_order["erp_order_id"] = erp_order_id
        external_ids[order.external_id] = erp_order_id
        sales_orders[erp_order_id] = stored_order
        return JSONResponse(status_code=201, content={"erp_order_id": erp_order_id})


@app.get("/sales-orders")
def list_sales_orders():
    with lock:
        return list(sales_orders.values())


@app.get("/sales-orders/{erp_order_id}")
def get_sales_order(erp_order_id: str):
    with lock:
        order = sales_orders.get(erp_order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="ERP order not found")
    return order


@app.post("/admin/faults")
def set_faults(faults: FaultInput):
    global fault_mode, fault_fail_rate, fault_latency_ms
    with lock:
        fault_mode = faults.mode
        fault_fail_rate = faults.fail_rate
        fault_latency_ms = faults.latency_ms
    return {"mode": fault_mode, "fail_rate": fault_fail_rate, "latency_ms": fault_latency_ms}


@app.post("/admin/reset")
def reset():
    global fault_mode, fault_fail_rate, fault_latency_ms
    with lock:
        fault_mode = "none"
        fault_fail_rate = 0.0
        fault_latency_ms = 0
        sales_orders.clear()
        external_ids.clear()
    return {"status": "reset"}
