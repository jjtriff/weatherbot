from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
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
    rejections: Mapping[str, int]


@dataclass(frozen=True)
class ReconcileSummary:
    settled: int
    positive_outcomes: int
    hypothetical_pnl: Decimal
    learned_markets: int = 0
    calibration_updates: int = 0


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
        heartbeat_path: Path | None = None,
        debug_analysis: bool = False,
    ) -> None:
        self._gateway = gateway
        self._repository = repository
        self._strategy_settings = strategy_settings
        self._forecast_selector = forecast_selector
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._heartbeat_path = heartbeat_path
        self._debug_analysis = debug_analysis
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
        rejections: Counter[str] = Counter()
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
        scan_id = uuid.uuid4().hex
        scanned_at = now.isoformat()

        for event in self._gateway.list_events():
            if not _is_weather_event(event):
                continue
            city = _city_from_event(event)
            markets = event.get("markets", [])
            if not isinstance(markets, list):
                continue
            if city is None:
                rejections["unsupported_city"] += len(markets)
                for market in markets:
                    if isinstance(market, Mapping):
                        self._record_analysis(
                            scan_id=scan_id,
                            scanned_at=scanned_at,
                            event=event,
                            market=market,
                            decision_reason="unsupported_city",
                        )
                continue
            for market in markets:
                if not isinstance(market, Mapping) or market.get("closed") is True:
                    continue
                context = _market_context(city, market)
                if context is None:
                    rejections["unsupported_contract"] += 1
                    self._record_analysis(
                        scan_id=scan_id,
                        scanned_at=scanned_at,
                        event=event,
                        market=market,
                        city=city,
                        decision_reason="unsupported_contract",
                    )
                    continue
                slug, target_date, bucket, settlement_time = context
                discovered += 1
                hours_to_settlement = Decimal(
                    str((settlement_time - now.astimezone(timezone.utc)).total_seconds() / 3600)
                )
                if not self._is_within_trade_window(hours_to_settlement):
                    reason = (
                        "settlement_too_soon"
                        if hours_to_settlement < self._strategy_settings.min_hours
                        else "settlement_too_far"
                    )
                    rejections[reason] += 1
                    self._record_analysis(
                        scan_id=scan_id,
                        scanned_at=scanned_at,
                        event=event,
                        market=market,
                        city=city,
                        target_date=target_date,
                        settlement_time=settlement_time,
                        hours_to_settlement=hours_to_settlement,
                        decision_reason=reason,
                    )
                    continue
                cache_key = (city.slug, target_date)
                if cache_key not in forecast_cache:
                    forecast_cache[cache_key] = self._forecast_selector.select(city, target_date)
                selection = forecast_cache[cache_key]
                if selection is None:
                    rejections["forecast_unavailable"] += 1
                    self._record_analysis(
                        scan_id=scan_id,
                        scanned_at=scanned_at,
                        event=event,
                        market=market,
                        city=city,
                        target_date=target_date,
                        settlement_time=settlement_time,
                        hours_to_settlement=hours_to_settlement,
                        decision_reason="forecast_unavailable",
                    )
                    continue
                quote = _quote_from_market_payload(slug, market)
                quote_source = "search" if quote is not None else "bbo"
                if quote is None:
                    try:
                        quote = self._gateway.get_bbo(slug)
                    except MarketplaceError:
                        rejections["quote_unavailable"] += 1
                        self._record_analysis(
                            scan_id=scan_id,
                            scanned_at=scanned_at,
                            event=event,
                            market=market,
                            city=city,
                            target_date=target_date,
                            settlement_time=settlement_time,
                            hours_to_settlement=hours_to_settlement,
                            selection=selection,
                            quote_source="unavailable",
                            decision_reason="quote_unavailable",
                        )
                        continue
                default_sigma = (
                    Decimal("2.0") if city.temperature_unit == "fahrenheit" else Decimal("1.2")
                )
                sigma = self._repository.calibrated_sigma(
                    city.slug, selection.forecast.source, default_sigma
                )
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
                    rejections[decision.reason] += 1
                    self._record_analysis(
                        scan_id=scan_id,
                        scanned_at=scanned_at,
                        event=event,
                        market=market,
                        city=city,
                        target_date=target_date,
                        settlement_time=settlement_time,
                        hours_to_settlement=hours_to_settlement,
                        selection=selection,
                        probability=probability,
                        quote=quote,
                        quote_source=quote_source,
                        decision_reason=decision.reason,
                    )
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
                    reason = "accepted"
                else:
                    rejections["already_open"] += 1
                    reason = "already_open"
                self._record_analysis(
                    scan_id=scan_id,
                    scanned_at=scanned_at,
                    event=event,
                    market=market,
                    city=city,
                    target_date=target_date,
                    settlement_time=settlement_time,
                    hours_to_settlement=hours_to_settlement,
                    selection=selection,
                    probability=probability,
                    quote=quote,
                    quote_source=quote_source,
                    decision_reason=reason,
                )
        return ScanSummary(
            discovered=discovered,
            paper_trades=paper_trades,
            rejections=dict(sorted(rejections.items())),
        )

    def reconcile_once(self) -> ReconcileSummary:
        settled = 0
        positive_outcomes = 0
        pnl = Decimal(0)
        learned_markets = 0
        calibration_updates = 0
        refreshed_calibrations: set[tuple[str, str]] = set()
        for trade in self._repository.open_trades():
            market_slug = str(trade["market_slug"])
            try:
                response = self._gateway.get_settlement(market_slug)
            except MarketplaceError:
                continue
            settlement = _settlement_value(response, market_slug)
            if settlement is None:
                continue
            trade_pnl = self._repository.settle_trade(market_slug, settlement)
            if trade_pnl is None:
                continue
            settled += 1
            pnl += trade_pnl
            if settlement == 1:
                positive_outcomes += 1
        now = self._now()
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        for market_slug in self._repository.unresolved_observed_market_slugs(now.isoformat()):
            try:
                response = self._gateway.get_settlement(market_slug)
            except MarketplaceError:
                continue
            settlement = _settlement_value(response, market_slug)
            if settlement is None:
                continue
            if self._repository.record_market_conclusion(market_slug, settlement):
                learned_markets += 1
                conclusion = self._repository.get_market_conclusion(market_slug)
                city_slug = str(conclusion["city_slug"] or "")
                forecast_source = str(conclusion["forecast_source"] or "")
                calibration_key = (city_slug, forecast_source)
                city = CITIES.get(city_slug)
                if (
                    city is not None
                    and forecast_source
                    and calibration_key not in refreshed_calibrations
                ):
                    default_sigma = (
                        Decimal("2.0") if city.temperature_unit == "fahrenheit" else Decimal("1.2")
                    )
                    update = self._repository.refresh_forecast_calibration(
                        city_slug=city_slug,
                        forecast_source=forecast_source,
                        default_sigma=default_sigma,
                    )
                    refreshed_calibrations.add(calibration_key)
                    if update.applied:
                        calibration_updates += 1
        return ReconcileSummary(
            settled=settled,
            positive_outcomes=positive_outcomes,
            hypothetical_pnl=pnl,
            learned_markets=learned_markets,
            calibration_updates=calibration_updates,
        )

    def run_forever(self, interval_seconds: int) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while True:
            reconciliation = self.reconcile_once()
            scan = self.scan_once()
            if self._heartbeat_path is not None:
                write_heartbeat(
                    self._heartbeat_path,
                    now=self._now(),
                    scan=scan,
                    reconciliation=reconciliation,
                )
            print(
                "paper_cycle "
                f"discovered={scan.discovered} new_paper_trades={scan.paper_trades} "
                f"rejections={format_rejections(scan.rejections)} "
                f"settled={reconciliation.settled} learned_markets={reconciliation.learned_markets} "
                f"calibration_updates={reconciliation.calibration_updates} "
                f"positive_outcomes={reconciliation.positive_outcomes} "
                f"hypothetical_pnl={reconciliation.hypothetical_pnl}",
                flush=True,
            )
            time.sleep(interval_seconds)

    def _record_analysis(
        self,
        *,
        scan_id: str,
        scanned_at: str,
        event: Mapping[str, object],
        market: Mapping[str, object],
        decision_reason: str,
        city: City | None = None,
        target_date: date | None = None,
        settlement_time: datetime | None = None,
        hours_to_settlement: Decimal | None = None,
        selection: ForecastSelection | None = None,
        probability: Decimal | None = None,
        quote: MarketQuote | None = None,
        quote_source: str = "not_evaluated",
    ) -> None:
        if not self._debug_analysis:
            return
        resolution_rule, resolution_source, resolution_station = _resolution_metadata(market)
        self._repository.record_market_analysis(
            scan_id=scan_id,
            scanned_at=scanned_at,
            event_title=str(event.get("title") or event.get("slug") or "unknown_event"),
            market_slug=str(market.get("slug") or "unknown_market"),
            market_title=str(market.get("title") or market.get("question") or "unknown_market"),
            city_slug=city.slug if city else None,
            target_date=target_date.isoformat() if target_date else None,
            settlement_time=settlement_time.isoformat() if settlement_time else None,
            hours_to_settlement=hours_to_settlement,
            forecast_value=selection.forecast.value if selection else None,
            forecast_source=selection.forecast.source if selection else None,
            probability=probability,
            quote=quote,
            quote_source=quote_source,
            decision_reason=decision_reason,
            resolution_rule=resolution_rule,
            resolution_source=resolution_source,
            resolution_station=resolution_station,
        )

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


def _resolution_metadata(market: Mapping[str, object]) -> tuple[str | None, str | None, str | None]:
    rule = str(market.get("description") or "").strip() or None
    if rule is None:
        return None, None, None
    source_match = re.search(r"Outcome verified from ([^.]+)\.", rule, re.IGNORECASE)
    station_match = re.search(r"\(([A-Z]{3,4})(?:\s+Airport)?\)", rule)
    return (
        rule,
        source_match.group(1).strip() if source_match else None,
        station_match.group(1) if station_match else None,
    )


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


def _settlement_value(response: Mapping[str, object], market_slug: str) -> int | None:
    response_slug = response.get("slug")
    if response_slug is not None and str(response_slug) != market_slug:
        return None
    raw_settlement = response.get("settlement")
    if isinstance(raw_settlement, bool) or not isinstance(raw_settlement, (int, float, Decimal)):
        return None
    settlement = Decimal(str(raw_settlement))
    if settlement not in {Decimal(0), Decimal(1)}:
        return None
    return int(settlement)


def format_rejections(rejections: Mapping[str, int]) -> str:
    if not rejections:
        return "none"
    return ",".join(f"{reason}:{count}" for reason, count in sorted(rejections.items()))


def write_heartbeat(
    path: Path,
    *,
    now: datetime,
    scan: ScanSummary,
    reconciliation: ReconcileSummary,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": now.isoformat(),
        "discovered": scan.discovered,
        "new_paper_trades": scan.paper_trades,
        "rejections": dict(scan.rejections),
        "settled": reconciliation.settled,
        "learned_markets": reconciliation.learned_markets,
        "calibration_updates": reconciliation.calibration_updates,
        "positive_outcomes": reconciliation.positive_outcomes,
        "hypothetical_pnl": str(reconciliation.hypothetical_pnl),
    }
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(json.dumps(payload, sort_keys=True))
    temporary_path.replace(path)
