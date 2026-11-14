"""Хеширование паролей, выпуск и проверка JWT, политика доступа."""

import base64
import hashlib
import hmac
import os
import re
import time

import jwt

PBKDF2_ITERATIONS = 200_000
TOKEN_TTL_SECONDS = 8 * 3600
ALGORITHM = "HS256"

ROLES = ("admin", "dispatcher")


# ---------- Пароли ----------

def hash_password(password: str) -> str:
    """Хеш в формате pbkdf2_sha256$<итерации>$<соль>$<хеш>."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    salt = base64.b64decode(salt_b64)
    expected = base64.b64decode(digest_b64)
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iterations))
    return hmac.compare_digest(actual, expected)


# ---------- Токены ----------

def issue_token(secret: str, username: str, role: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    now = int(time.time())
    payload = {"sub": username, "role": role, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(secret: str, token: str) -> dict:
    """Возвращает содержимое токена или возбуждает jwt.InvalidTokenError."""
    return jwt.decode(token, secret, algorithms=[ALGORITHM])


# ---------- Политика доступа ----------
# Правило: (HTTP-метод или "*", регулярное выражение пути, допустимые роли).
# Применяется первое совпавшее правило; запрос без правила запрещён.

POLICY: list[tuple[str, str, tuple[str, ...]]] = [
    ("GET", r"^/api/(orders|drivers)(/.*)?$", ("admin", "dispatcher")),
    ("POST", r"^/api/orders$", ("admin", "dispatcher")),
    ("POST", r"^/api/orders/\d+/(start|complete|cancel)$", ("admin", "dispatcher")),
    ("PUT", r"^/api/drivers/\d+/status$", ("admin", "dispatcher")),
    ("*", r"^/api/drivers(/.*)?$", ("admin",)),
]


def is_allowed(method: str, path: str, role: str) -> bool:
    path = path.split("?", 1)[0].rstrip("/") or "/"
    for rule_method, pattern, roles in POLICY:
        if rule_method in ("*", method.upper()) and re.match(pattern, path):
            return role in roles
    return False
