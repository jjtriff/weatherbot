from __future__ import annotations

from decimal import Decimal

import pytest

from weatherbot.domain import MarketQuote, TemperatureBucket
from weatherbot.probability import bucket_probability, validate_probability_partition
from weatherbot.strategy import RiskPolicy, evaluate_candidate


def test_quote_rejects_crossed_book() -> None:
    with pytest.raises(ValueError, match="best_bid"):
        MarketQuote(
            market_slug="paris-14c",
            best_bid=Decimal("0.60"),
            best_ask=Decimal("0.55"),
            volume=Decimal(1000),
        )


def test_exact_temperature_bucket_probability_is_not_deterministic() -> None:
    probability = bucket_probability(
        forecast=Decimal("13.5"),
        bucket=TemperatureBucket.exact(Decimal(14)),
        sigma=Decimal("1.2"),
    )

    assert Decimal(0) < probability < Decimal(1)


def test_weather_bucket_probabilities_sum_to_one() -> None:
    buckets = [
        TemperatureBucket.below_or_equal(Decimal(9)),
        *[TemperatureBucket.exact(Decimal(str(value))) for value in range(10, 19)],
        TemperatureBucket.above_or_equal(Decimal(19)),
    ]

    total = validate_probability_partition(buckets, Decimal("13.5"), Decimal("1.2"))

    assert abs(total - Decimal(1)) < Decimal("0.0001")


def test_strategy_uses_executable_ask_and_rejects_wide_spread() -> None:
    quote = MarketQuote(
        market_slug="paris-14c",
        best_bid=Decimal("0.10"),
        best_ask=Decimal("0.15"),
        volume=Decimal(1000),
    )
    policy = RiskPolicy(
        min_ev=Decimal("0.10"),
        max_spread=Decimal("0.03"),
        max_bet=Decimal("2.00"),
        max_price=Decimal("0.45"),
        min_volume=Decimal(500),
        kelly_fraction=Decimal("0.25"),
        max_total_exposure=Decimal("10.00"),
    )

    decision = evaluate_candidate(
        market_slug="paris-14c",
        probability=Decimal("0.50"),
        quote=quote,
        available_cash=Decimal("10.00"),
        current_exposure=Decimal(0),
        policy=policy,
    )

    assert decision.accepted is False
    assert decision.reason == "spread_too_wide"
