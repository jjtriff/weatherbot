from __future__ import annotations

from decimal import Decimal

import pytest

from weatherbot.discovery import UnsupportedWeatherMarket, parse_temperature_bucket
from weatherbot.domain import TemperatureBucket


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "Will the highest temperature in Paris be 14°C on October 31?",
            TemperatureBucket.exact(Decimal(14)),
        ),
        (
            "Will the highest temperature in Paris be between 14-15°C on October 31?",
            TemperatureBucket(lower=Decimal(14), upper=Decimal(15)),
        ),
        (
            "Will the highest temperature in Paris be 9°C or below on October 31?",
            TemperatureBucket.below_or_equal(Decimal(9)),
        ),
        (
            "Will the highest temperature in Paris be 19°C or higher on October 31?",
            TemperatureBucket.above_or_equal(Decimal(19)),
        ),
    ],
)
def test_parse_supported_temperature_contracts(question: str, expected: TemperatureBucket) -> None:
    assert parse_temperature_bucket(question) == expected


def test_parses_polymarket_us_daily_high_bucket_titles() -> None:
    assert parse_temperature_bucket(
        "Highest temperature in Philadelphia on November 1? 61 or below"
    ) == TemperatureBucket.below_or_equal(Decimal(61))
    assert parse_temperature_bucket(
        "Highest temperature in Philadelphia on November 1? 62 to 63"
    ) == TemperatureBucket(lower=Decimal(62), upper=Decimal(63))
    assert parse_temperature_bucket(
        "Highest temperature in Philadelphia on November 1? 70 or above"
    ) == TemperatureBucket.above_or_equal(Decimal(70))


def test_rejects_ambiguous_weather_contract() -> None:
    with pytest.raises(UnsupportedWeatherMarket):
        parse_temperature_bucket("Will it rain in Paris tomorrow?")
