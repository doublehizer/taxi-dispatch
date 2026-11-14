"""driver-service: регистрация водителей, их статусы и координаты."""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Response
from prometheus_client import Gauge, make_asgi_app
from pydantic import BaseModel, Field

from taxi_common.connections import connect_postgres, connect_redis
from taxi_common.logs import setup_logging

from . import geoindex

setup_logging("driver-service")
log = logging.getLogger("driver-service")

DRIVERS_BY_STATUS = Gauge("taxi_drivers", "Количество водителей по статусам", ["status"])

CarClass = Literal["economy", "comfort", "business"]


class DriverCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    car: str = Field(min_length=1, max_length=100)
    plate: str = Field(min_length=1, max_length=15)
    car_class: CarClass = "economy"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class DriverUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    car: str | None = Field(default=None, min_length=1, max_length=100)
    plate: str | None = Field(default=None, min_length=1, max_length=15)
    car_class: CarClass | None = None


class Location(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class StatusChange(BaseModel):
    status: Literal["free", "offline"]
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


state: dict = {}


async def refresh_gauges():
    """Периодически обновляет метрику числа водителей по статусам."""
    while True:
        try:
            rows = await state["db"].fetch("SELECT status, count(*) AS cnt FROM drivers GROUP BY status")
            counts = {r["status"]: r["cnt"] for r in rows}
            for status in ("free", "busy", "offline"):
                DRIVERS_BY_STATUS.labels(status).set(counts.get(status, 0))
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось обновить метрики: %s", exc)
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["db"] = await connect_postgres()
    state["redis"] = await connect_redis()
    task = asyncio.create_task(refresh_gauges())
    yield
    task.cancel()
    await state["redis"].aclose()
    await state["db"].close()


app = FastAPI(title="driver-service", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def row_to_dict(row) -> dict:
    result = dict(row)
    for key, value in result.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif value.__class__.__name__ == "Decimal":
            result[key] = float(value)
    return result


async def get_driver_or_404(driver_id: int):
    row = await state["db"].fetchrow("SELECT * FROM drivers WHERE id = $1", driver_id)
    if not row:
        raise HTTPException(404, "Водитель не найден")
    return row


@app.get("/health")
async def health():
    await state["db"].fetchval("SELECT 1")
    await state["redis"].ping()
    return {"status": "ok"}


@app.post("/drivers", status_code=201)
async def register_driver(data: DriverCreate):
    try:
        row = await state["db"].fetchrow(
            """
            INSERT INTO drivers (name, car, plate, car_class, lat, lon)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            data.name, data.car, data.plate, data.car_class, data.lat, data.lon,
        )
    except Exception as exc:  # noqa: BLE001
        if "unique" in str(exc).lower():
            raise HTTPException(409, "Водитель с таким госномером уже зарегистрирован") from exc
        raise
    await geoindex.mark_free(state["redis"], data.car_class, row["id"], data.lat, data.lon)
    log.info("Зарегистрирован водитель %s (%s)", row["id"], data.car_class)
    return row_to_dict(row)


@app.get("/drivers")
async def list_drivers(status: str | None = None):
    if status:
        rows = await state["db"].fetch("SELECT * FROM drivers WHERE status = $1 ORDER BY id", status)
    else:
        rows = await state["db"].fetch("SELECT * FROM drivers ORDER BY id")
    return [row_to_dict(r) for r in rows]


@app.get("/drivers/{driver_id}")
async def get_driver(driver_id: int):
    return row_to_dict(await get_driver_or_404(driver_id))


@app.put("/drivers/{driver_id}")
async def update_driver(driver_id: int, data: DriverUpdate):
    current = await get_driver_or_404(driver_id)
    new_class = data.car_class or current["car_class"]
    if new_class != current["car_class"] and current["status"] == "busy":
        raise HTTPException(409, "Нельзя сменить класс автомобиля во время заказа")
    try:
        row = await state["db"].fetchrow(
            """
            UPDATE drivers SET name = $2, car = $3, plate = $4, car_class = $5, updated_at = now()
            WHERE id = $1 RETURNING *
            """,
            driver_id,
            data.name or current["name"],
            data.car or current["car"],
            data.plate or current["plate"],
            new_class,
        )
    except Exception as exc:  # noqa: BLE001
        if "unique" in str(exc).lower():
            raise HTTPException(409, "Водитель с таким госномером уже зарегистрирован") from exc
        raise
    if new_class != current["car_class"] and row["status"] == "free":
        # Перенос водителя в геоиндекс нового класса
        await geoindex.mark_unavailable(state["redis"], current["car_class"], driver_id)
        await geoindex.mark_free(state["redis"], new_class, driver_id, row["lat"], row["lon"])
    return row_to_dict(row)


@app.delete("/drivers/{driver_id}", status_code=204)
async def delete_driver(driver_id: int):
    current = await get_driver_or_404(driver_id)
    if current["status"] == "busy":
        raise HTTPException(409, "Нельзя удалить водителя, который выполняет заказ")
    await geoindex.mark_unavailable(state["redis"], current["car_class"], driver_id)
    await state["db"].execute("DELETE FROM drivers WHERE id = $1", driver_id)
    log.info("Водитель %s удалён", driver_id)
    return Response(status_code=204)


@app.put("/drivers/{driver_id}/location")
async def update_location(driver_id: int, loc: Location):
    row = await state["db"].fetchrow(
        "UPDATE drivers SET lat = $2, lon = $3, updated_at = now() WHERE id = $1 RETURNING *",
        driver_id, loc.lat, loc.lon,
    )
    if not row:
        raise HTTPException(404, "Водитель не найден")
    if row["status"] == "free":
        await geoindex.update_position(state["redis"], row["car_class"], driver_id, loc.lat, loc.lon)
    return {"id": driver_id, "lat": loc.lat, "lon": loc.lon}


@app.put("/drivers/{driver_id}/status")
async def change_status(driver_id: int, change: StatusChange):
    current = await get_driver_or_404(driver_id)
    if current["status"] == "busy" and change.status == "offline":
        raise HTTPException(409, "Водитель выполняет заказ и не может уйти с линии")
    lat =change.lat if change.lat is not None else current["lat"]
    lon = change.lon if change.lon is not None else current["lon"]
    row = await state["db"].fetchrow(
        """
        UPDATE drivers
        SET status = $2, lat = $3, lon = $4, current_order_id = NULL, updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        driver_id, change.status, lat, lon,
    )
    if change.status == "free":
        await geoindex.mark_free(state["redis"], row["car_class"], driver_id, lat, lon)
    else:
        await geoindex.mark_unavailable(state["redis"], row["car_class"], driver_id)
    log.info("Водитель %s: %s -> %s", driver_id, current["status"], change.status)
    return row_to_dict(row)
