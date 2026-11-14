"""auth-service: аутентификация пользователей и авторизация запросов.

- POST /auth/login     — вход по логину и паролю, выдача JWT;
- GET  /auth/me        — данные текущего пользователя;
- GET  /auth/verify    — проверка запроса для Traefik ForwardAuth;
- /auth/users          — управление учётными записями (только admin).
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import Counter, make_asgi_app
from pydantic import BaseModel, Field

from taxi_common.config import env
from taxi_common.connections import connect_postgres
from taxi_common.logs import setup_logging

from .security import decode_token, hash_password, is_allowed, issue_token, verify_password

setup_logging("auth-service")
log = logging.getLogger("auth-service")

JWT_SECRET = env("JWT_SECRET", "dev-secret-change-me")

LOGINS = Counter("taxi_auth_logins_total", "Попытки входа", ["result"])
VERIFY = Counter("taxi_auth_verify_total", "Проверки доступа через шлюз", ["result"])

Role = Literal["admin", "dispatcher"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=100)
    role: Role = "dispatcher"


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: Role | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_active: bool | None = None


state: dict = {}


async def ensure_default_users(db) -> None:
    """Начальные учётные записи создаются, только если таблица пуста."""
    if await db.fetchval("SELECT count(*) FROM users"):
        return
    defaults = [
        ("admin", env("ADMIN_PASSWORD", "admin12345"), "Администратор системы", "admin"),
        ("dispatcher", env("DISPATCHER_PASSWORD", "dispatcher123"), "Диспетчер смены", "dispatcher"),
    ]
    for username, password, full_name, role in defaults:
        await db.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES ($1, $2, $3, $4)",
            username, hash_password(password), full_name, role,
        )
    log.info("Созданы начальные учётные записи admin и dispatcher")


@asynccontextmanager
async def lifespan(_: FastAPI):
    state["db"] = await connect_postgres()
    await ensure_default_users(state["db"])
    yield
    await state["db"].close()


app = FastAPI(title="auth-service", lifespan=lifespan)
app.mount("/metrics", make_asgi_app())


def user_public(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "full_name": row["full_name"],
        "role": row["role"],
        "is_active": row["is_active"],
        "created_at": row["created_at"].isoformat(),
    }


def extract_token(authorization: str | None) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def current_user(authorization: str | None = Header(default=None)) -> dict:
    token = extract_token(authorization)
    if not token:
        raise HTTPException(401, "Требуется вход в систему")
    try:
        claims = decode_token(JWT_SECRET, token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Недействительный или просроченный токен") from exc
    row = await state["db"].fetchrow(
        "SELECT * FROM users WHERE username = $1 AND is_active", claims["sub"]
    )
    if not row:
        raise HTTPException(401, "Учётная запись отключена")
    return user_public(row)


async def admin_only(user: Annotated[dict, Depends(current_user)]) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Недостаточно прав")
    return user


CurrentUser = Annotated[dict, Depends(current_user)]
AdminUser = Annotated[dict, Depends(admin_only)]


@app.get("/health")
async def health():
    await state["db"].fetchval("SELECT 1")
    return {"status": "ok"}


@app.post("/auth/login")
async def login(data: LoginRequest):
    row = await state["db"].fetchrow("SELECT * FROM users WHERE username = $1", data.username)
    if not row or not row["is_active"] or not verify_password(data.password, row["password_hash"]):
        LOGINS.labels("fail").inc()
        log.warning("Неудачная попытка входа: %s", data.username)
        raise HTTPException(401, "Неверный логин или пароль")
    await state["db"].execute("UPDATE users SET last_login_at = now() WHERE id = $1", row["id"])
    LOGINS.labels("ok").inc()
    token = issue_token(JWT_SECRET, row["username"], row["role"])
    return {"access_token": token, "token_type": "bearer", "user": user_public(row)}


@app.get("/auth/me")
async def me(user: CurrentUser):
    return user


@app.get("/auth/verify")
async def verify(
    response: Response,
    authorization: str | None = Header(default=None),
    x_forwarded_method: str = Header(default="GET"),
    x_forwarded_uri: str = Header(default="/"),
):
    """Вызывается шлюзом Traefik перед передачей запроса в сервис.

    200 — запрос пропускается, 401 — нет или неверный токен, 403 — нет прав.
    Проверка выполняется только по подписи токена, без обращения к БД,
    поэтому не замедляет каждый запрос.
    """
    token = extract_token(authorization)
    if not token:
        VERIFY.labels("unauthorized").inc()
        raise HTTPException(401, "Требуется вход в систему")
    try:
        claims = decode_token(JWT_SECRET, token)
    except jwt.InvalidTokenError as exc:
        VERIFY.labels("unauthorized").inc()
        raise HTTPException(401, "Недействительный или просроченный токен") from exc
    if not is_allowed(x_forwarded_method, x_forwarded_uri, claims["role"]):
        VERIFY.labels("forbidden").inc()
        raise HTTPException(403, "Недостаточно прав")
    VERIFY.labels("allowed").inc()
    response.headers["X-User"] = claims["sub"]
    response.headers["X-Role"] = claims["role"]
    return {"status": "allowed"}


# ---------- Управление пользователями (CRUD, только admin) ----------

@app.get("/auth/users")
async def list_users(_: AdminUser):
    rows = await state["db"].fetch("SELECT * FROM users ORDER BY id")
    return [user_public(r) for r in rows]


@app.post("/auth/users", status_code=201)
async def create_user(data: UserCreate, admin: AdminUser):
    try:
        row = await state["db"].fetchrow(
            "INSERT INTO users (username, password_hash, full_name, role) "
            "VALUES ($1, $2, $3, $4) RETURNING *",
            data.username, hash_password(data.password), data.full_name, data.role,
        )
    except Exception as exc:  # noqa: BLE001
        if "unique" in str(exc).lower():
            raise HTTPException(409, "Пользователь с таким логином уже существует") from exc
        raise
    log.info("%s создал пользователя %s (%s)", admin["username"], data.username, data.role)
    return user_public(row)


@app.put("/auth/users/{user_id}")
async def update_user(user_id: int, data: UserUpdate, admin: AdminUser):
    row = await state["db"].fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    if not row:
        raise HTTPException(404, "Пользователь не найден")
    password_hash = hash_password(data.password) if data.password else row["password_hash"]
    row = await state["db"].fetchrow(
        """
        UPDATE users SET full_name = $2, role = $3, password_hash = $4, is_active = $5
        WHERE id = $1 RETURNING *
        """,
        user_id,
        data.full_name or row["full_name"],
        data.role or row["role"],
        password_hash,
        row["is_active"] if data.is_active is None else data.is_active,
    )
    return user_public(row)


@app.delete("/auth/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin: AdminUser):
    if user_id == admin["id"]:
        raise HTTPException(409, "Нельзя удалить собственную учётную запись")
    deleted = await state["db"].execute("DELETE FROM users WHERE id = $1", user_id)
    if deleted == "DELETE 0":
        raise HTTPException(404, "Пользователь не найден")
    return Response(status_code=204)
