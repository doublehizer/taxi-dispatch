"""dispatch-service: воркер автоматического распределения заказов.

Читает очередь orders.new, подбирает ближайшего свободного водителя
нужного класса и назначает его на заказ. Если водителя нет, заказ
откладывается в очередь orders.retry и через заданное время
возвращается на повторный поиск.
"""

import asyncio
import json
import logging
import socket

import aio_pika
from prometheus_client import Counter, Histogram, start_http_server

from taxi_common.config import env_float, env_int
from taxi_common.connections import (
    ORDERS_QUEUE,
    RETRY_QUEUE,
    connect_postgres,
    connect_rabbitmq,
    connect_redis,
    declare_queues,
)
from taxi_common.logs import setup_logging

from .matching import find_and_claim, return_driver

setup_logging("dispatch-service")
log = logging.getLogger("dispatch-service")

WORKER = socket.gethostname()
MAX_ATTEMPTS = env_int("DISPATCH_MAX_ATTEMPTS", 6)
RETRY_DELAY_MS = env_int("DISPATCH_RETRY_DELAY_MS", 5000)
PREFETCH = env_int("DISPATCH_PREFETCH", 5)
METRICS_PORT = env_int("METRICS_PORT", 8000)
MAX_RADIUS_KM = env_float("DISPATCH_MAX_RADIUS_KM", 10.0)

RADII = tuple(r for r in (1.0, 3.0, 5.0, 10.0, 20.0) if r <= MAX_RADIUS_KM)

DISPATCH_RESULTS = Counter(
    "taxi_dispatch_results_total", "Результаты обработки заказов", ["result", "worker"]
)
DISPATCH_SECONDS = Histogram(
    "taxi_dispatch_seconds", "Время обработки одного сообщения", ["worker"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
)
PICKUP_KM = Histogram(
    "taxi_pickup_distance_km", "Расстояние от водителя до точки подачи",
    buckets=(0.25, 0.5, 1, 2, 3, 5, 10, 20),
)


class Dispatcher:
    def __init__(self, db, redis, channel):
        self.db = db
        self.redis = redis
        self.channel = channel

    async def schedule_retry(self, order_id: int, attempt: int) -> None:
        body = json.dumps({"order_id": order_id, "attempt": attempt}).encode()
        await self.channel.default_exchange.publish(
            aio_pika.Message(body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT),
            routing_key=RETRY_QUEUE,
        )

    async def process(self, order_id: int, attempt: int) -> str:
        order = await self.db.fetchrow(
            """
            UPDATE orders SET status = 'searching', attempts = $2
            WHERE id = $1 AND status IN ('new', 'searching')
            RETURNING *
            """,
            order_id, attempt,
        )
        if not order:
            return "skipped"  # заказ отменён или уже обработан

        candidate = await find_and_claim(
            self.redis, order["tariff"], order["pickup_lat"], order["pickup_lon"], RADII
        )

        if candidate is None:
            if attempt < MAX_ATTEMPTS:
                await self.schedule_retry(order_id, attempt + 1)
                log.info("Заказ %s: свободных водителей нет, повтор %d/%d",
                         order_id, attempt + 1, MAX_ATTEMPTS)
                return "retry"
            await self.db.execute(
                "UPDATE orders SET status = 'no_drivers', finished_at = now() "
                "WHERE id = $1 AND status = 'searching'",
                order_id,
            )
            log.warning("Заказ %s: водитель не найден за %d попыток", order_id, attempt)
            return "failed"

        async with self.db.acquire() as conn, conn.transaction():
            assigned = await conn.fetchval(
                """
                UPDATE orders
                SET status = 'assigned', driver_id = $2, pickup_distance_km = $3,
                    assigned_at = now(), assigned_by = $4
                WHERE id = $1 AND status = 'searching'
                RETURNING id
                """,
                order_id, candidate.driver_id, candidate.distance_km, WORKER,
            )
            if assigned:
                await conn.execute(
                    "UPDATE drivers SET status = 'busy', current_order_id = $2, updated_at = now() "
                    "WHERE id = $1",
                    candidate.driver_id, order_id,
                )

        if not assigned:
            await return_driver(self.redis, order["tariff"], candidate)
            return "skipped"

        PICKUP_KM.observe(candidate.distance_km)
        log.info("Заказ %s -> водитель %s (%.2f км до подачи)",
                 order_id, candidate.driver_id, candidate.distance_km)
        return "assigned"

    async def on_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        async with message.process(requeue=True):
            try:
                data = json.loads(message.body)
                order_id = int(data["order_id"])
                attempt = int(data.get("attempt", 1))
            except (ValueError, KeyError, TypeError):
                log.error("Некорректное сообщение отброшено: %r", message.body)
                return
            with DISPATCH_SECONDS.labels(WORKER).time():
                result = await self.process(order_id, attempt)
            DISPATCH_RESULTS.labels(result, WORKER).inc()


async def main() -> None:
    start_http_server(METRICS_PORT)
    db = await connect_postgres()
    redis = await connect_redis()
    connection = await connect_rabbitmq()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=PREFETCH)
    await declare_queues(channel, RETRY_DELAY_MS)

    dispatcher = Dispatcher(db, redis, channel)
    queue = await channel.get_queue(ORDERS_QUEUE)
    await queue.consume(dispatcher.on_message)
    log.info("Воркер %s запущен: радиусы поиска %s км, попыток %d", WORKER, RADII, MAX_ATTEMPTS)
    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
