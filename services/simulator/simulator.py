"""simulator: генератор тестовой нагрузки.

Регистрирует водителей, перемещает свободных водителей по городу,
создаёт заказы пассажиров и проводит назначенные заказы по всему
жизненному циклу: подача -> поездка -> завершение.
"""

import asyncio
import logging
import math
import os
import random
import time

import httpx

logging.basicConfig(level="INFO", format="%(asctime)s [simulator] %(levelname)s: %(message)s")
log = logging.getLogger("simulator")
logging.getLogger("httpx").setLevel(logging.WARNING)

ORDER_API = os.getenv("ORDER_API_URL", "http://order-service:8000")
DRIVER_API = os.getenv("DRIVER_API_URL", "http://driver-service:8000")
CENTER_LAT = float(os.getenv("CITY_CENTER_LAT", "55.7558"))
CENTER_LON = float(os.getenv("CITY_CENTER_LON", "37.6173"))
CITY_RADIUS_KM = float(os.getenv("CITY_RADIUS_KM", "8"))
DRIVERS = int(os.getenv("SIM_DRIVERS", "30"))
ORDER_INTERVAL = float(os.getenv("SIM_ORDER_INTERVAL", "3"))
PICKUP_DELAY = float(os.getenv("SIM_PICKUP_DELAY", "4"))
SECONDS_PER_KM = float(os.getenv("SIM_SECONDS_PER_KM", "1.5"))
CANCEL_RATE = float(os.getenv("SIM_CANCEL_RATE", "0.05"))

MALE_NAMES = ["Алексей", "Иван", "Дмитрий", "Сергей", "Павел", "Артём", "Никита", "Михаил"]
FEMALE_NAMES = ["Мария", "Ольга", "Анна", "Екатерина", "Наталья", "Юлия", "Светлана", "Ирина"]
LAST_NAMES = ["Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов",
              "Михайлов", "Новиков", "Фёдоров", "Морозов", "Волков", "Алексеев"]
CARS = {
    "economy": ["Kia Rio", "Hyundai Solaris", "Lada Vesta", "Renault Logan"],
    "comfort": ["Skoda Octavia", "Kia K5", "Toyota Camry", "Haval Jolion"],
    "business": ["Mercedes E-класс", "BMW 5", "Audi A6", "Genesis G80"],
}
PLATE_LETTERS = "АВЕКМНОРСТУХ"


def random_person() -> tuple[str, str]:
    """Имя и фамилия с согласованием по роду (Смирнов / Смирнова)."""
    last = random.choice(LAST_NAMES)
    if random.random() < 0.5:
        return random.choice(MALE_NAMES), last
    return random.choice(FEMALE_NAMES), last + "а"


def weighted_class(weights: tuple[float, float, float]) -> str:
    return random.choices(["economy", "comfort", "business"], weights=weights)[0]


def random_point(radius_km: float = CITY_RADIUS_KM) -> dict:
    r = radius_km * math.sqrt(random.random())
    angle = random.uniform(0, 2 * math.pi)
    d_lat = r * math.cos(angle) / 111.32
    d_lon = r * math.sin(angle) / (111.32 * math.cos(math.radians(CENTER_LAT)))
    return {"lat": round(CENTER_LAT + d_lat, 6), "lon": round(CENTER_LON + d_lon, 6)}


def random_plate() -> str:
    letters = random.sample(PLATE_LETTERS, 3)
    return f"{letters[0]}{random.randint(100, 999)}{letters[1]}{letters[2]}{random.choice(['77', '97', '199', '799'])}"


async def wait_for(client: httpx.AsyncClient, url: str) -> None:
    while True:
        try:
            if (await client.get(f"{url}/health")).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        log.info("Ожидание %s ...", url)
        await asyncio.sleep(2)


async def ensure_drivers(client: httpx.AsyncClient) -> None:
    existing = (await client.get(f"{DRIVER_API}/drivers")).json()
    missing = DRIVERS - len(existing)
    for _ in range(max(0, missing)):
        car_class = weighted_class((0.6, 0.3, 0.1))
        point = random_point()
        payload = {
            "name": " ".join(random_person()),
            "car": random.choice(CARS[car_class]),
            "plate": random_plate(),
            "car_class": car_class,
            **point,
        }
        resp = await client.post(f"{DRIVER_API}/drivers", json=payload)
        if resp.status_code == 409:
            continue
        resp.raise_for_status()
    log.info("Водителей на линии: %d", DRIVERS)


async def move_drivers(client: httpx.AsyncClient) -> None:
    """Свободные водители немного перемещаются (около 100–200 м за шаг)."""
    while True:
        try:
            drivers = (await client.get(f"{DRIVER_API}/drivers", params={"status": "free"})).json()
            tasks = []
            for d in drivers:
                step = random.uniform(0.1, 0.2) / 111.32
                angle = random.uniform(0, 2 * math.pi)
                lat = d["lat"] + step * math.cos(angle)
                lon = d["lon"] + step * math.sin(angle) / math.cos(math.radians(lat))
                tasks.append(client.put(f"{DRIVER_API}/drivers/{d['id']}/location",
                                        json={"lat": round(lat, 6), "lon": round(lon, 6)}))
            await asyncio.gather(*tasks, return_exceptions=True)
        except httpx.HTTPError as exc:
            log.warning("Ошибка перемещения водителей: %s", exc)
        await asyncio.sleep(3)


async def generate_orders(client: httpx.AsyncClient) -> None:
    while True:
        first, last = random_person()
        payload = {
            "passenger_name": f"{first} {last[0]}.",
            "passenger_phone": f"+7 9{random.randint(10, 99)} {random.randint(100, 999)}-"
                               f"{random.randint(10, 99)}-{random.randint(10, 99)}",
            "tariff": weighted_class((0.7, 0.22, 0.08)),
            "pickup": random_point(),
            "dropoff": random_point(),
        }
        try:
            resp = await client.post(f"{ORDER_API}/orders", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Не удалось создать заказ: %s", exc)
        await asyncio.sleep(ORDER_INTERVAL * random.uniform(0.5, 1.5))


async def ride_lifecycle(client: httpx.AsyncClient) -> None:
    """Имитация действий водителя: подача, поездка, завершение."""
    seen: dict[int, float] = {}
    cancel_checked: set[int] = set()
    while True:
        now = time.monotonic()
        try:
            for order in (await client.get(f"{ORDER_API}/orders", params={"status": "searching"})).json():
                if order["id"] in cancel_checked:
                    continue
                cancel_checked.add(order["id"])
                if random.random() < CANCEL_RATE:
                    await client.post(f"{ORDER_API}/orders/{order['id']}/cancel")
                    log.info("Пассажир отменил заказ %s", order["id"])

            for order in (await client.get(f"{ORDER_API}/orders", params={"status": "assigned"})).json():
                first_seen = seen.setdefault(order["id"], now)
                if now - first_seen >= PICKUP_DELAY:
                    await client.post(f"{ORDER_API}/orders/{order['id']}/start")
                    seen[order["id"]] = now

            for order in (await client.get(f"{ORDER_API}/orders", params={"status": "in_progress"})).json():
                started = seen.setdefault(order["id"], now)
                ride_time = min(25.0, max(5.0, order["distance_km"] * SECONDS_PER_KM))
                if now - started >= ride_time:
                    await client.post(f"{ORDER_API}/orders/{order['id']}/complete")
                    seen.pop(order["id"], None)
        except httpx.HTTPError as exc:
            log.warning("Ошибка обработки поездок: %s", exc)
        await asyncio.sleep(1)


async def main() -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await wait_for(client, ORDER_API)
        await wait_for(client, DRIVER_API)
        await ensure_drivers(client)
        await asyncio.gather(
            move_drivers(client),
            generate_orders(client),
            ride_lifecycle(client),
        )


if __name__ == "__main__":
    asyncio.run(main())
