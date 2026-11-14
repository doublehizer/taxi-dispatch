"""Проверка алгоритма подбора водителя на упрощённой модели Redis."""

import asyncio

from dispatch_service.matching import find_and_claim, free_key
from taxi_common.geo import haversine_km


class FakeGeoRedis:
    """Минимальная имитация GEOSEARCH / ZREM / GEOADD."""

    def __init__(self):
        self.sets: dict[str, dict[str, tuple[float, float]]] = {}
        self.lock = asyncio.Lock()

    async def geoadd(self, key, values, xx=False):
        lon, lat, member = values
        self.sets.setdefault(key, {})[member] = (lon, lat)

    async def geosearch(self, key, longitude, latitude, radius, unit, sort, count, withdist, withcoord):
        found = []
        for member, (lon, lat) in self.sets.get(key, {}).items():
            dist = haversine_km(latitude, longitude, lat, lon)
            if dist <= radius:
                found.append([member, dist, (lon, lat)])
        found.sort(key=lambda item: item[1])
        return found[:count]

    async def zrem(self, key, member):
        async with self.lock:
            return 1 if self.sets.get(key, {}).pop(member, None) else 0


CENTER = (55.7558, 37.6173)


async def test_nearest_driver_selected():
    r = FakeGeoRedis()
    await r.geoadd(free_key("economy"), (37.6200, 55.7560, "1"))   # ~0.2 км
    await r.geoadd(free_key("economy"), (37.6500, 55.7700, "2"))   # ~2.5 км
    candidate = await find_and_claim(r, "economy", *CENTER)
    assert candidate.driver_id == 1
    assert candidate.distance_km < 0.5


async def test_claimed_driver_removed_from_free_set():
    r = FakeGeoRedis()
    await r.geoadd(free_key("economy"), (37.6200, 55.7560, "1"))
    await find_and_claim(r, "economy", *CENTER)
    assert "1" not in r.sets[free_key("economy")]


async def test_radius_expands():
    r = FakeGeoRedis()
    await r.geoadd(free_key("comfort"), (37.6900, 55.7558, "7"))   # ~4.5 км
    candidate = await find_and_claim(r, "comfort", *CENTER)
    assert candidate.driver_id == 7


async def test_no_driver_outside_max_radius():
    r = FakeGeoRedis()
    await r.geoadd(free_key("economy"), (38.2000, 55.7558, "3"))   # ~36 км
    assert await find_and_claim(r, "economy", *CENTER) is None


async def test_car_class_respected():
    r = FakeGeoRedis()
    await r.geoadd(free_key("economy"), (37.6180, 55.7560, "1"))
    assert await find_and_claim(r, "business", *CENTER) is None


async def test_parallel_workers_do_not_share_driver():
    """Две реплики dispatch-service одновременно ищут водителя для разных заказов."""
    r = FakeGeoRedis()
    await r.geoadd(free_key("economy"), (37.6180, 55.7560, "1"))
    await r.geoadd(free_key("economy"), (37.6300, 55.7600, "2"))
    a, b = await asyncio.gather(
        find_and_claim(r, "economy", *CENTER),
        find_and_claim(r, "economy", *CENTER),
    )
    assert {a.driver_id, b.driver_id} == {1, 2}
