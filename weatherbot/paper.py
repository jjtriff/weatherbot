from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol

from weatherbot.config import PaperStrategySettings
from weatherbot.discovery import UnsupportedWeatherMarket, parse_temperature_bucket
from weatherbot.domain import MarketQuote, TemperatureBucket
from weatherbot.marketplace import MarketplaceError
from weatherbot.probability import bucket_probability
from weatherbot.repository import PaperRepository
from weatherbot.strategy import RiskPolicy, evaluate_candidate
from weatherbot.weather import CITIES, City, ForecastSelection


class PaperMarketplaceGateway(Protocol):
    def list_events(self) -> list[Mapping[str, object]]: ...

    def get_bbo(self, market_slug: str) -> MarketQuote: ...

    def get_settlement(self, market_slug: str) -> Mapping[str, object]: ...


class PaperForecastSelector(Protocol):
    def select(self, city: City, target_date: date) -> ForecastSelection | None: ...


@dataclass(frozen=True)
class ScanSummary:
    discovered: int
    paper_trades: int


@dataclass(frozen=True)
class ReconcileSummary:
    settled: int
    positive_outcomes: int
    hypothetical_pnl: Decimal


class PaperWeatherBot:
    """Paper execution of the same selective strategy used for eventual live evaluation."""

    def __init__(
        self,
        *,
        gateway: PaperMarketplaceGateway,
        repository: PaperRepository,
        strategy_settings: PaperStrategySettings,
        forecast_selector: PaperForecastSelector,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._strategy_settings = strategy_settings
        self._forecast_selector = forecast_selector
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._risk_policy = RiskPolicy(
            min_ev=strategy_settings.min_ev,
            max_spread=strategy_settings.max_spread,
            max_bet=strategy_settings.max_bet,
            max_price=strategy_settings.max_price,
            min_volume=strategy_settings.min_volume,
            kelly_fraction=strategy_settings.kelly_fraction,
            max_total_exposure=strategy_settings.starting_balance,
        )

    def scan_once(self) -> ScanSummary:
        discovered = 0
        paper_trades = 0
        current_exposure = sum(
            (Decimal(str(trade["cost"])) for trade in self._repository.open_trades()), Decimal(0)
        )
        available_cash = max(
            Decimal(0), self._strategy_settings.starting_balance - current_exposure
        )
        forecast_cache: dict[tuple[str, date], ForecastSelection | None] = {}
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")

        for event in self._gateway.list_events():
            if not _is_weather_event(event):
                continue
            city = _city_from_event(event)
            markets = event.get("markets", [])
            if city is None or not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, Mapping) or market.get("closed") is True:
                    continue
                context = _market_context(city, market)
                if context is None:
                    continue
                slug, target_date, bucket, settlement_time = context
                discovered += 1
                hours_to_settlement = Decimal(
                    str((settlement_time - now.astimezone(timezone.utc)).total_seconds() / 3600)
                )
                if not self._is_within_trade_window(hours_to_settlement):
                    continue
                cache_key = (city.slug, target_date)
                if cache_key not in forecast_cache:
                    forecast_cache[cache_key] = self._forecast_selector.select(city, target_date)
                selection = forecast_cache[cache_key]
                if selection is None:
                    continue
                quote = _quote_from_market_payload(slug, market)
                if quote is None:
                    try:
                        quote = self._gateway.get_bbo(slug)
                    except MarketplaceError:
                        continue
                sigma = Decimal("2.0") if city.temperature_unit == "fahrenheit" else Decimal("1.2")
                probability = bucket_probability(selection.forecast.value, bucket, sigma)
                decision = evaluate_candidate(
                    market_slug=slug,
                    probability=probability,
                    quote=quote,
                    available_cash=available_cash,
                    current_exposure=current_exposure,
                    policy=self._risk_policy,
                )
                if not decision.accepted:
                    continue
                assert decision.candidate is not None
                created = self._repository.create_trade(
                    market_slug=slug,
                    market_title=str(market.get("title") or market.get("question") or slug),
                    entry_price=decision.candidate.entry_price,
                    cost=decision.candidate.cost,
                    shares=decision.candidate.shares,
                )
                if created:
                    paper_trades += 1
                    current_exposure += decision.candidate.cost
                    available_cash -= decision.candidate.cost
        return ScanSummary(discovered=discovered, paper_trades=paper_trades)

    def reconcile_once(self) -> ReconcileSummary:
        settled = 0
        positive_outcomes = 0
        pnl = Decimal(0)
        for trade in self._repository.open_trades():
            market_slug = str(trade["market_slug"])
            try:
                response = self._gateway.get_settlement(market_slug)
            except MarketplaceError:
                continue
            raw_settlement = response.get("settlement")
            if raw_settlement not in {0, 1}:
                continue
            trade_pnl = self._repository.settle_trade(market_slug, int(raw_settlement))
            if trade_pnl is None:
                continue
            settled += 1
            pnl += trade_pnl
            if raw_settlement == 1:
                positive_outcomes += 1
        return ReconcileSummary(
            settled=settled, positive_outcomes=positive_outcomes, hypothetical_pnl=pnl
        )

    def run_forever(self, interval_seconds: int) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while True:
            reconciliation = self.reconcile_once()
            scan = self.scan_once()
            print(
                "paper_cycle "
                f"discovered={scan.discovered} new_paper_trades={scan.paper_trades} "
                f"settled={reconciliation.settled} positive_outcomes={reconciliation.positive_outcomes} "
                f"hypothetical_pnl={reconciliation.hypothetical_pnl}",
                flush=True,
            )
            time.sleep(interval_seconds)

    def _is_within_trade_window(self, hours_to_settlement: Decimal) -> bool:
        return (
            self._strategy_settings.min_hours
            <= hours_to_settlement
            <= self._strategy_settings.max_hours
        )


def _is_weather_event(event: Mapping[str, object]) -> bool:
    category = str(event.get("category") or "").lower()
    text = " ".join(
        str(event.get(key) or "") for key in ("title", "description", "slug", "category")
    ).lower()
    return category == "weather" or any(
        term in text for term in ("weather", "temperature", "rainfall")
    )


def _city_from_event(event: Mapping[str, object]) -> City | None:
    title = str(event.get("title") or "").lower()
    for city in CITIES.values():
        if f"in {city.name.lower()}" in title:
            return city
    return None


def _market_context(
    city: City, market: Mapping[str, object]
) -> tuple[str, date, TemperatureBucket, datetime] | None:
    slug = str(market.get("slug") or "").strip()
    question = str(market.get("question") or "").strip()
    title = str(market.get("title") or "").strip()
    end_date = str(market.get("endDate") or "").strip()
    match = re.search(r"-(\d{4}-\d{2}-\d{2})-", slug)
    if not slug or not match or not end_date:
        return None
    try:
        bucket = parse_temperature_bucket(" ".join(part for part in (question, title) if part))
        target_date = date.fromisoformat(match.group(1))
        settlement_time = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
    except (UnsupportedWeatherMarket, ValueError):
        return None
    return slug, target_date, bucket, settlement_time


def _quote_from_market_payload(
    market_slug: str, market: Mapping[str, object]
) -> MarketQuote | None:
    ask = _quote_value(market.get("bestAskQuote"))
    if ask is None:
        return None
    return MarketQuote(
        market_slug=market_slug,
        best_bid=_quote_value(market.get("bestBidQuote")),
        best_ask=ask,
        volume=_quote_value(market.get("sharesTraded")) or Decimal(0),
    )


def _quote_value(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        return _quote_value(value.get("value"))
    return Decimal(str(value))
