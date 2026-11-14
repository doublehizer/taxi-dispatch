"""Расчёт стоимости поездки."""

from dataclasses import dataclass

# Коэффициент перехода от расстояния по прямой к расстоянию по дорогам
ROAD_FACTOR = 1.3


@dataclass(frozen=True)
class Tariff:
    base: float      # подача, руб.
    per_km: float    # стоимость километра, руб.
    minimum: float   # минимальная стоимость, руб.


TARIFFS: dict[str, Tariff] = {
    "economy": Tariff(base=99, per_km=18, minimum=149),
    "comfort": Tariff(base=149, per_km=25, minimum=249),
    "business": Tariff(base=299, per_km=45, minimum=499),
}


def road_distance_km(straight_km: float) -> float:
    return round(straight_km * ROAD_FACTOR, 2)


def calculate_price(tariff: str, road_km: float) -> float:
    if tariff not in TARIFFS:
        raise ValueError(f"Неизвестный тариф: {tariff}")
    if road_km < 0:
        raise ValueError("Расстояние не может быть отрицательным")
    t = TARIFFS[tariff]
    return float(round(max(t.minimum, t.base + t.per_km * road_km)))
