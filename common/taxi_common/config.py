"""Чтение настроек из переменных окружения.

Поддерживается схема Docker secrets: если задана переменная NAME_FILE,
значение читается из указанного файла (например, /run/secrets/pg_password).
"""

import os


def env(name: str, default: str | None = None) -> str:
    file_path = os.getenv(f"{name}_FILE")
    if file_path:
        with open(file_path, encoding="utf-8") as fh:
            return fh.read().strip()
    value = os.getenv(name, default)
    if value is None:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


def env_int(name: str, default: int) -> int:
    return int(env(name, str(default)))


def env_float(name: str, default: float) -> float:
    return float(env(name, str(default)))


def postgres_params() -> dict:
    # Параметры передаются по отдельности, а не строкой DSN:
    # сгенерированный пароль может содержать символы, недопустимые в URL
    return {
        "user": env("POSTGRES_USER", "taxi"),
        "password": env("POSTGRES_PASSWORD", "taxi"),
        "host": env("POSTGRES_HOST", "postgres"),
        "port": env_int("POSTGRES_PORT", 5432),
        "database": env("POSTGRES_DB", "taxi"),
    }


def rabbitmq_url() -> str:
    user = env("RABBITMQ_USER", "taxi")
    password = env("RABBITMQ_PASSWORD", "taxi")
    host = env("RABBITMQ_HOST", "rabbitmq")
    port = env("RABBITMQ_PORT", "5672")
    return f"amqp://{user}:{password}@{host}:{port}/"


def redis_url() -> str:
    host = env("REDIS_HOST", "redis")
    port = env("REDIS_PORT", "6379")
    return f"redis://{host}:{port}/0"
