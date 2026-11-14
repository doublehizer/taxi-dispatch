"""Подключение к PostgreSQL, Redis и RabbitMQ с повторными попытками.

В Docker Swarm директива depends_on не действует, поэтому сервис
должен сам дождаться готовности зависимостей.
"""

import asyncio
import logging

import aio_pika
import asyncpg
import redis.asyncio as aioredis

from .config import postgres_params, rabbitmq_url, redis_url

log = logging.getLogger(__name__)

ORDERS_QUEUE = "orders.new"
RETRY_QUEUE = "orders.retry"


async def _retry(name: str, factory, attempts: int = 30, delay: float = 2.0):
    for attempt in range(1, attempts + 1):
        try:
            result = await factory()
            log.info("Подключение к %s установлено", name)
            return result
        except Exception as exc:  # noqa: BLE001
            log.warning("%s недоступен (%s), попытка %d/%d", name, exc, attempt, attempts)
            await asyncio.sleep(delay)
    raise RuntimeError(f"Не удалось подключиться к {name}")


async def connect_postgres() -> asyncpg.Pool:
    return await _retry(
        "PostgreSQL",
        lambda: asyncpg.create_pool(**postgres_params(), min_size=1, max_size=10),
    )


async def connect_redis() -> aioredis.Redis:
    async def factory():
        client = aioredis.from_url(redis_url(), decode_responses=True)
        await client.ping()
        return client

    return await _retry("Redis", factory)


async def connect_rabbitmq() -> aio_pika.abc.AbstractRobustConnection:
    return await _retry("RabbitMQ", lambda: aio_pika.connect_robust(rabbitmq_url()))


async def declare_queues(channel: aio_pika.abc.AbstractChannel, retry_delay_ms: int) -> None:
    """Очередь новых заказов и очередь отложенного повтора.

    Сообщение из orders.retry по истечении TTL автоматически
    возвращается в orders.new через dead-letter exchange.
    """
    await channel.declare_queue(ORDERS_QUEUE, durable=True)
    await channel.declare_queue(
        RETRY_QUEUE,
        durable=True,
        arguments={
            "x-message-ttl": retry_delay_ms,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": ORDERS_QUEUE,
        },
    )
