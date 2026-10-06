import asyncio
import threading
import uuid
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()
lock = threading.Lock()
fault_mode: Literal["none", "error_500", "rate_limit_429", "timeout", "slow"] = "none"
sales_orders: dict[str, dict] = {}
external_ids: dict[str, str] = {}


class SalesOrderInput(BaseModel):
    external_id: str
    customer: dict
    currency: str
    lines: list[dict]
    total: Decimal


class FaultInput(BaseModel):
    mode: Literal["none", "error_500", "rate_limit_429", "timeout", "slow"]


@app.post("/sales-orders")
async def create_sales_order(order: SalesOrderInput):
    with lock:
        mode = fault_mode
    if mode == "error_500":
        raise HTTPException(status_code=500, detail="Injected ERP error")
    if mode == "rate_limit_429":
        raise HTTPException(status_code=429, detail="Injected ERP rate limit")
    if mode == "timeout":
        await asyncio.sleep(10)
    if mode == "slow":
        await asyncio.sleep(1)
    with lock:
        existing_id = external_ids.get(order.external_id)
        if existing_id:
            return JSONResponse(status_code=200, content={"erp_order_id": existing_id})
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
    global fault_mode
    with lock:
        fault_mode = faults.mode
    return {"mode": fault_mode}


@app.post("/admin/reset")
def reset():
    global fault_mode
    with lock:
        fault_mode = "none"
        sales_orders.clear()
        external_ids.clear()
    return {"status": "reset"}
