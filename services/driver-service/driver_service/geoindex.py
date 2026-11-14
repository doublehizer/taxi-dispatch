"""Геоиндекс свободных водителей в Redis.

Для каждого класса автомобиля ведётся отдельный GEO-набор
drivers:free:<класс>. В наборе находятся только свободные водители,
поэтому поиск ближайшего исполнителя выполняется одной командой GEOSEARCH.
"""

import redis.asyncio as aioredis


def free_key(car_class: str) -> str:
    return f"drivers:free:{car_class}"


async def mark_free(r: aioredis.Redis, car_class: str, driver_id: int, lat: float, lon: float):
    await r.geoadd(free_key(car_class), (lon, lat, str(driver_id)))


async def mark_unavailable(r: aioredis.Redis, car_class: str, driver_id: int):
    await r.zrem(free_key(car_class), str(driver_id))


async def update_position(r: aioredis.Redis, car_class: str, driver_id: int, lat: float, lon: float):
    # XX: обновлять только существующий элемент. Если водителя уже
    # забрал dispatch-service, он не вернётся в набор свободных.
    await r.geoadd(free_key(car_class), (lon, lat, str(driver_id)), xx=True)
