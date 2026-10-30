from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from weatherbot.domain import MarketQuote, TradeCandidate


@dataclass(frozen=True)
class RiskPolicy:
    min_ev: Decimal
    max_spread: Decimal
    max_bet: Decimal
    max_price: Decimal
    min_volume: Decimal
    kelly_fraction: Decimal
    max_total_exposure: Decimal


@dataclass(frozen=True)
class Decision:
    accepted: bool
    reason: str
    candidate: TradeCandidate | None = None


def expected_value(probability: Decimal, ask: Decimal) -> Decimal:
    if not Decimal(0) < ask < Decimal(1):
        raise ValueError("ask must be between 0 and 1")
    return probability * ((Decimal(1) / ask) - Decimal(1)) - (Decimal(1) - probability)


def fractional_kelly(probability: Decimal, ask: Decimal, fraction: Decimal) -> Decimal:
    if not Decimal(0) < ask < Decimal(1):
        raise ValueError("ask must be between 0 and 1")
    b = Decimal(1) / ask - Decimal(1)
    raw = (probability * b - (Decimal(1) - probability)) / b
    return max(Decimal(0), raw) * fraction


def evaluate_candidate(
    *,
    market_slug: str,
    probability: Decimal,
    quote: MarketQuote,
    available_cash: Decimal,
    current_exposure: Decimal,
    policy: RiskPolicy,
) -> Decision:
    if quote.best_bid is None or quote.best_ask is None:
        return Decision(False, "missing_bbo")
    spread = quote.spread
    assert spread is not None
    if spread > policy.max_spread:
        return Decision(False, "spread_too_wide")
    if quote.volume < policy.min_volume:
        return Decision(False, "volume_too_low")
    if quote.best_ask > policy.max_price:
        return Decision(False, "price_too_high")
    ev = expected_value(probability, quote.best_ask)
    if ev < policy.min_ev:
        return Decision(False, "ev_too_low")
    remaining_exposure = max(Decimal(0), policy.max_total_exposure - current_exposure)
    kelly_cost = (
        fractional_kelly(probability, quote.best_ask, policy.kelly_fraction) * available_cash
    )
    cost = min(kelly_cost, policy.max_bet, available_cash, remaining_exposure).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )
    if cost < Decimal("0.50"):
        return Decision(False, "size_too_small")
    shares = (cost / quote.best_ask).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    return Decision(
        True,
        "accepted",
        TradeCandidate(
            market_slug=market_slug,
            probability=probability,
            entry_price=quote.best_ask,
            cost=cost,
            shares=shares,
            expected_value=ev,
        ),
    )
