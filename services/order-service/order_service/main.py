"""order-service: приём заказов, расчёт стоимости, управление жизненным циклом заказа."""

import json
import logging
from contextlib import asynccontextmanager
from typing import Literal

import aio_pika
import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from prometheus_client import Counter, make_asgi_app
from pydantic import BaseModel, Field

from taxi_common.config import env, env_int
from taxi_common.connections import (
    ORDERS_QUEUE,
    connect_postgres,
    connect_rabbitmq,
    declare_queues,
)
from taxi_common.geo import haversine_km
from taxi_common.logs import setup_logging

from .pricing import calculate_price, road_distance_km

setup_logging("order-service")
log = logging.getLogger("order-service")

DRIVER_SERVICE_URL = env("DRIVER_SERVICE_URL", "http://driver-service:8000")
RETRY_DELAY_MS = env_int("DISPATCH_RETRY_DELAY_MS", 5000)

ORDERS_CREATED = Counter("taxi_orders_created_total", "Создано заказов", ["tariff"])
ORDERS_FINISHED = Counter("taxi_orders_finished_total", "Завершено заказов", ["result"])

Tariff = Literal["economy", "comfort", "business"]


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class OrderCreate(BaseModel):
    passenger_name: str = Field(min_length=1, max_length=100)
    passenger_phone: str | None = Field(default=None, max_length=20)
    tariff: Tariff = "economy"
    pickup: Point
    dropoff: Point


state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["db"] = await connect_postgres()
    state["mq"] = await connect_rabbitmq()
    state["channel"] = await state["mq"].channel()
    await declare_queues(state["channel"], RETRY_DELAY_MS)
    state["http"] = httpx.AsyncClient(base_url=DRIVER_SERVICE_URL, timeout=5)
    yield
    await state["http"].aclose()
    await state["mq"].close()
    await state["db"].close()


app = FastAPI(title="order-service", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def row_to_dict(row) -> dict:
    result = dict(row)
    for key, value in result.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif value.__class__.__name__ == "Decimal":
            result[key] = float(value)
    return result


async def publish_order(order_id: int) -> None:
    body = json.dumps({"order_id": order_id, "attempt": 1}).encode()
    await state["channel"].default_exchange.publish(
        aio_pika.Message(body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
        routing_key=ORDERS_QUEUE,
    )


async def release_driver(driver_id: int, lat: float, lon: float) -> None:
    """Синхронный вызов driver-service: водитель снова свободен."""
    try:
        resp = await state["http"].put(
            f"/drivers/{driver_id}/status",
            json={"status": "free", "lat": lat, "lon": lon},
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("Не удалось освободить водителя %s: %s", driver_id, exc)


@app.get("/health")
async def health():
    await state["db"].fetchval("SELECT 1")
    return {"status": "ok"}


@app.post("/orders", status_code=201)
async def create_order(data: OrderCreate, x_user: str | None = Header(default=None)):
    # Заголовок X-User добавляет шлюз после проверки токена в auth-service;
    # для внутренних вызовов (симулятор) он отсутствует
    created_by = x_user or "simulator"
    straight = haversine_km(data.pickup.lat, data.pickup.lon, data.dropoff.lat, data.dropoff.lon)
    distance = road_distance_km(straight)
    price = calculate_price(data.tariff, distance)
    row = await state["db"].fetchrow(
        """
        INSERT INTO orders (passenger_name, passenger_phone, tariff,
                            pickup_lat, pickup_lon, dropoff_lat, dropoff_lon,
                            distance_km, price, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING *
        """,
        data.passenger_name, data.passenger_phone, data.tariff,
        data.pickup.lat, data.pickup.lon, data.dropoff.lat, data.dropoff.lon,
        distance, price, created_by,
    )
    await publish_order(row["id"])
    ORDERS_CREATED.labels(data.tariff).inc()
    log.info("Создан заказ %s (%s, %.2f км, %.0f руб.)", row["id"], data.tariff, distance, price)
    return row_to_dict(row)


@app.get("/orders")
async def list_orders(status: str | None = None, limit: int = Query(50, le=500)):
    if status:
        rows = await state["db"].fetch(
            "SELECT * FROM orders WHERE status = $1 ORDER BY id DESC LIMIT $2", status, limit
        )
    else:
        rows = await state["db"].fetch("SELECT * FROM orders ORDER BY id DESC LIMIT $1", limit)
    return [row_to_dict(r) for r in rows]


@app.get("/orders/stats")
async def stats():
    by_status = await state["db"].fetch(
        "SELECT status, count(*) AS cnt FROM orders GROUP BY status"
    )
    summary = await state["db"].fetchrow(
        """
        SELECT
            avg(EXTRACT(EPOCH FROM assigned_at - created_at))
                FILTER (WHERE assigned_at > now() - interval '1 hour') AS avg_assign_sec,
            avg(pickup_distance_km)
                FILTER (WHERE assigned_at > now() - interval '1 hour') AS avg_pickup_km,
            coalesce(sum(price) FILTER (WHERE status = 'completed'), 0) AS revenue
        FROM orders
        """
    )
    return {
        "by_status": {r["status"]: r["cnt"] for r in by_status},
        **row_to_dict(summary),
    }


@app.get("/orders/{order_id}")
async def get_order(order_id: int):
    row = await state["db"].fetchrow("SELECT * FROM orders WHERE id = $1", order_id)
    if not row:
        raise HTTPException(404, "Заказ не найден")
    order = row_to_dict(row)
    history = await state["db"].fetch(
        "SELECT old_status, new_status, changed_at FROM order_status_log "
        "WHERE order_id = $1 ORDER BY id",
        order_id,
    )
    order["history"] = [row_to_dict(h) for h in history]
    return order


@app.post("/orders/{order_id}/start")
async def start_ride(order_id: int):
    row = await state["db"].fetchrow(
        """
        UPDATE orders SET status = 'in_progress', started_at = now()
        WHERE id = $1 AND status = 'assigned'
        RETURNING *
        """,
        order_id,
    )
    if not row:
        raise HTTPException(409, "Поездку можно начать только из статуса assigned")
    return row_to_dict(row)


@app.post("/orders/{order_id}/complete")
async def complete_ride(order_id: int):
    row = await state["db"].fetchrow(
        """
        UPDATE orders SET status = 'completed', finished_at = now()
        WHERE id = $1 AND status IN ('assigned', 'in_progress')
        RETURNING *
        """,
        order_id,
    )
    if not row:
        raise HTTPException(409, "Заказ нельзя завершить в текущем статусе")
    await release_driver(row["driver_id"], row["dropoff_lat"], row["dropoff_lon"])
    ORDERS_FINISHED.labels("completed").inc()
    return row_to_dict(row)


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: int):
    row = await state["db"].fetchrow(
        """
        UPDATE orders SET status = 'cancelled', finished_at = now()
        WHERE id = $1 AND status IN ('new', 'searching', 'assigned')
        RETURNING *
        """,
        order_id,
    )
    if not row:
        raise HTTPException(409, "Заказ нельзя отменить в текущем статусе")
    if row["driver_id"]:
        driver = await state["db"].fetchrow(
            "SELECT lat, lon FROM drivers WHERE id = $1", row["driver_id"]
        )
        await release_driver(row["driver_id"], driver["lat"], driver["lon"])
    ORDERS_FINISHED.labels("cancelled").inc()
    return row_to_dict(row)
