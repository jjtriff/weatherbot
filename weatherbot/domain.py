from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

USD = "USD"
BUY_LONG = "ORDER_INTENT_BUY_LONG"
SELL_LONG = "ORDER_INTENT_SELL_LONG"
LIMIT = "ORDER_TYPE_LIMIT"
GTC = "TIME_IN_FORCE_GOOD_TILL_CANCEL"
OrderDirection = Literal["ORDER_INTENT_BUY_LONG", "ORDER_INTENT_SELL_LONG"]


@dataclass(frozen=True)
class TemperatureBucket:
    lower: Decimal | None
    upper: Decimal | None

    def __post_init__(self) -> None:
        if self.lower is None and self.upper is None:
            raise ValueError("a temperature bucket needs at least one boundary")
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise ValueError("bucket lower boundary must not exceed upper boundary")

    @classmethod
    def exact(cls, value: Decimal) -> TemperatureBucket:
        return cls(lower=value, upper=value)

    @classmethod
    def below_or_equal(cls, value: Decimal) -> TemperatureBucket:
        return cls(lower=None, upper=value)

    @classmethod
    def above_or_equal(cls, value: Decimal) -> TemperatureBucket:
        return cls(lower=value, upper=None)


@dataclass(frozen=True)
class MarketQuote:
    market_slug: str
    best_bid: Decimal | None
    best_ask: Decimal | None
    volume: Decimal

    def __post_init__(self) -> None:
        if not self.market_slug:
            raise ValueError("market_slug is required")
        if self.volume < 0:
            raise ValueError("volume must be non-negative")
        if self.best_bid is not None and not Decimal(0) < self.best_bid < Decimal(1):
            raise ValueError("best_bid must be between 0 and 1")
        if self.best_ask is not None and not Decimal(0) < self.best_ask < Decimal(1):
            raise ValueError("best_ask must be between 0 and 1")
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid > self.best_ask
        ):
            raise ValueError("best_bid must not exceed best_ask")

    @property
    def spread(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


@dataclass(frozen=True)
class TradeCandidate:
    market_slug: str
    probability: Decimal
    entry_price: Decimal
    cost: Decimal
    shares: Decimal
    expected_value: Decimal

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.probability <= Decimal(1):
            raise ValueError("probability must be between 0 and 1")
        if self.entry_price <= 0 or self.cost <= 0 or self.shares <= 0:
            raise ValueError("entry price, cost, and shares must be positive")
