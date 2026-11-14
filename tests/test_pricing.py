import pytest

from order_service.pricing import TARIFFS, calculate_price, road_distance_km


def test_minimum_price_for_short_ride():
    assert calculate_price("economy", 0.5) == TARIFFS["economy"].minimum


def test_price_grows_with_distance():
    assert calculate_price("economy", 10) < calculate_price("economy", 20)


def test_price_formula():
    # 99 + 18 * 10 = 279
    assert calculate_price("economy", 10) == 279


def test_business_more_expensive_than_economy():
    assert calculate_price("business", 10) > calculate_price("comfort", 10) > calculate_price("economy", 10)


def test_road_distance_longer_than_straight():
    assert road_distance_km(10) == 13.0


def test_unknown_tariff():
    with pytest.raises(ValueError):
        calculate_price("premium", 5)


def test_negative_distance():
    with pytest.raises(ValueError):
        calculate_price("economy", -1)
