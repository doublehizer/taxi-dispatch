"""Алгоритм подбора водителя.

Поиск ведётся по расширяющимся радиусам: сначала ближайшие водители
в радиусе 1 км, затем 3, 5 и 10 км. Из найденных кандидатов водитель
«захватывается» командой ZREM: она атомарна, поэтому при нескольких
репликах dispatch-service один водитель не может получить два заказа.
"""

from dataclasses import dataclass

DEFAULT_RADII_KM = (1.0, 3.0, 5.0, 10.0)


@dataclass(frozen=True)
class Candidate:
    driver_id: int
    distance_km: float
    lat: float
    lon: float


def free_key(car_class: str) -> str:
    return f"drivers:free:{car_class}"


async def find_and_claim(
    redis,
    car_class: str,
    lat: float,
    lon: float,
    radii_km: tuple[float, ...] = DEFAULT_RADII_KM,
    batch: int = 5,
) -> Candidate | None:
    key = free_key(car_class)
    for radius in radii_km:
        found = await redis.geosearch(
            key,
            longitude=lon,
            latitude=lat,
            radius=radius,
            unit="km",
            sort="ASC",
            count=batch,
            withdist=True,
            withcoord=True,
        )
        for member, dist, (c_lon, c_lat) in found:
            if await redis.zrem(key, member) == 1:
                return Candidate(int(member), round(float(dist), 2), c_lat, c_lon)
    return None


async def return_driver(redis, car_class: str, candidate: Candidate) -> None:
    """Вернуть захваченного водителя в набор свободных (если заказ уже отменён)."""
    await redis.geoadd(free_key(car_class), (candidate.lon, candidate.lat, str(candidate.driver_id)))
