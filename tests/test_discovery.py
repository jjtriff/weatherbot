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
def test_parse_supported_temperature_contracts(
    question: str, expected: TemperatureBucket
) -> None:
    assert parse_temperature_bucket(question) == expected


def test_rejects_ambiguous_weather_contract() -> None:
    with pytest.raises(UnsupportedWeatherMarket):
        parse_temperature_bucket("Will it rain in Paris tomorrow?")
