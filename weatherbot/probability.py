from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

from weatherbot.domain import TemperatureBucket

_HALF = Decimal("0.5")
_ONE = Decimal(1)
_SQRT_2 = math.sqrt(2.0)


def normal_cdf(value: Decimal) -> Decimal:
    return Decimal(str(0.5 * (1.0 + math.erf(float(value) / _SQRT_2))))


def _boundaries(bucket: TemperatureBucket) -> tuple[Decimal | None, Decimal | None]:
    if bucket.lower is not None and bucket.upper is not None and bucket.lower == bucket.upper:
        return bucket.lower - _HALF, bucket.upper + _HALF
    lower = bucket.lower - _HALF if bucket.lower is not None else None
    upper = bucket.upper + _HALF if bucket.upper is not None else None
    return lower, upper


def bucket_probability(forecast: Decimal, bucket: TemperatureBucket, sigma: Decimal) -> Decimal:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    lower, upper = _boundaries(bucket)
    if lower is None:
        assert upper is not None
        return normal_cdf((upper - forecast) / sigma)
    if upper is None:
        return _ONE - normal_cdf((lower - forecast) / sigma)
    return normal_cdf((upper - forecast) / sigma) - normal_cdf((lower - forecast) / sigma)


def validate_probability_partition(
    buckets: Sequence[TemperatureBucket], forecast: Decimal, sigma: Decimal
) -> Decimal:
    return sum((bucket_probability(forecast, bucket, sigma) for bucket in buckets), Decimal(0))
