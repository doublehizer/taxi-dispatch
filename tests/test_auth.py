import jwt
import pytest

from auth_service.security import (
    decode_token,
    hash_password,
    is_allowed,
    issue_token,
    verify_password,
)

SECRET = "test-secret"


def test_password_hash_roundtrip():
    stored = hash_password("correct horse")
    assert verify_password("correct horse", stored)
    assert not verify_password("wrong", stored)


def test_same_password_gives_different_hashes():
    assert hash_password("qwerty123") != hash_password("qwerty123")


def test_malformed_hash_rejected():
    assert not verify_password("x", "not-a-hash")


def test_token_contains_user_and_role():
    claims = decode_token(SECRET, issue_token(SECRET, "ivanov", "dispatcher"))
    assert claims["sub"] == "ivanov"
    assert claims["role"] == "dispatcher"


def test_expired_token_rejected():
    token = issue_token(SECRET, "ivanov", "admin", ttl=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(SECRET, token)


def test_token_with_foreign_signature_rejected():
    token = issue_token("other-secret", "ivanov", "admin")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(SECRET, token)


@pytest.mark.parametrize(
    ("method", "path", "role", "expected"),
    [
        ("GET", "/api/orders", "dispatcher", True),
        ("GET", "/api/orders/stats", "dispatcher", True),
        ("POST", "/api/orders", "dispatcher", True),
        ("POST", "/api/orders/15/cancel", "dispatcher", True),
        ("GET", "/api/drivers", "dispatcher", True),
        ("POST", "/api/drivers", "dispatcher", False),
        ("DELETE", "/api/drivers/3", "dispatcher", False),
        ("DELETE", "/api/drivers/3", "admin", True),
        ("PUT", "/api/drivers/3", "admin", True),
        ("PUT", "/api/drivers/3/status", "dispatcher", True),
        ("GET", "/api/unknown", "admin", False),
        ("GET", "/api/orders?limit=10", "dispatcher", True),
    ],
)
def test_access_policy(method, path, role, expected):
    assert is_allowed(method, path, role) is expected
