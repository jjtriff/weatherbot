from __future__ import annotations

import re
from decimal import Decimal

from weatherbot.domain import TemperatureBucket


class UnsupportedWeatherMarket(ValueError):
    """Raised when a weather-market question is outside v3's explicit contract grammar."""


_NUMBER = r"(-?\d+(?:\.\d+)?)"
_UNIT = r"(?:°\s*[CF]|\s*[CF])"


def parse_temperature_bucket(question: str) -> TemperatureBucket:
    """Parse only known high-temperature bucket wording; never infer ambiguous contracts."""
    normalized = " ".join(question.split())
    if not re.search(r"highest temperature", normalized, re.IGNORECASE):
        raise UnsupportedWeatherMarket("not a supported high-temperature market")

    below = re.search(rf"{_NUMBER}{_UNIT}\s+or\s+below", normalized, re.IGNORECASE)
    if below:
        return TemperatureBucket.below_or_equal(Decimal(below.group(1)))

    above = re.search(rf"{_NUMBER}{_UNIT}\s+or\s+higher", normalized, re.IGNORECASE)
    if above:
        return TemperatureBucket.above_or_equal(Decimal(above.group(1)))

    between = re.search(
        rf"between\s+{_NUMBER}\s*-\s*{_NUMBER}{_UNIT}", normalized, re.IGNORECASE
    )
    if between:
        return TemperatureBucket(lower=Decimal(between.group(1)), upper=Decimal(between.group(2)))

    exact = re.search(rf"be\s+{_NUMBER}{_UNIT}\s+on\b", normalized, re.IGNORECASE)
    if exact:
        return TemperatureBucket.exact(Decimal(exact.group(1)))

    raise UnsupportedWeatherMarket(f"unsupported temperature bucket wording: {question!r}")
