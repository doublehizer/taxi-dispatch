import pytest

from taxi_common.config import env
from taxi_common.geo import haversine_km


def test_zero_distance():
    assert haversine_km(55.75, 37.61, 55.75, 37.61) == 0


def test_moscow_kremlin_to_vdnh():
    # Кремль -> ВДНХ, около 8 км по прямой
    distance = haversine_km(55.7520, 37.6175, 55.8263, 37.6377)
    assert 8.0 < distance < 8.6


def test_symmetry():
    a = haversine_km(55.70, 37.50, 55.80, 37.70)
    b = haversine_km(55.80, 37.70, 55.70, 37.50)
    assert a == pytest.approx(b)


def test_env_reads_secret_file(tmp_path, monkeypatch):
    secret = tmp_path / "pg_password"
    secret.write_text("s3cret\n")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(secret))
    assert env("POSTGRES_PASSWORD") == "s3cret"


def test_env_missing_without_default(monkeypatch):
    monkeypatch.delenv("SOME_MISSING_VAR", raising=False)
    with pytest.raises(RuntimeError):
        env("SOME_MISSING_VAR")
