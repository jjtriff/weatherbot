from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from weatherbot.discovery import UnsupportedWeatherMarket, parse_temperature_bucket
from weatherbot.domain import MarketQuote
from weatherbot.repository import PaperRepository


class PaperMarketplaceGateway(Protocol):
    def list_events(self) -> list[Mapping[str, object]]: ...

    def get_bbo(self, market_slug: str) -> MarketQuote: ...

    def get_settlement(self, market_slug: str) -> Mapping[str, object]: ...


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
    """Paper-only weather scanner that deliberately bypasses v3 risk filters."""

    def __init__(
        self,
        *,
        gateway: PaperMarketplaceGateway,
        repository: PaperRepository,
        stake_per_trade: Decimal,
    ) -> None:
        if stake_per_trade <= 0:
            raise ValueError("stake_per_trade must be positive")
        self._gateway = gateway
        self._repository = repository
        self._stake_per_trade = stake_per_trade

    def scan_once(self) -> ScanSummary:
        discovered = 0
        paper_trades = 0
        for event in self._gateway.list_events():
            if not _is_weather_event(event):
                continue
            markets = event.get("markets", [])
            if not isinstance(markets, list):
                continue
            for market in markets:
                if not isinstance(market, Mapping) or market.get("closed") is True:
                    continue
                slug = str(market.get("slug") or "").strip()
                question = str(market.get("question") or "").strip()
                title = str(market.get("title") or "").strip()
                contract_text = " ".join(part for part in (question, title) if part)
                if not slug or not contract_text:
                    continue
                try:
                    parse_temperature_bucket(contract_text)
                except UnsupportedWeatherMarket:
                    continue
                discovered += 1
                quote = self._gateway.get_bbo(slug)
                if quote.best_ask is None:
                    continue
                shares = (self._stake_per_trade / quote.best_ask).quantize(
                    Decimal("0.0001"), rounding=ROUND_DOWN
                )
                created = self._repository.create_trade(
                    market_slug=slug,
                    market_title=str(market.get("title") or market.get("question") or slug),
                    entry_price=quote.best_ask,
                    cost=self._stake_per_trade,
                    shares=shares,
                )
                if created:
                    paper_trades += 1
        return ScanSummary(discovered=discovered, paper_trades=paper_trades)

    def reconcile_once(self) -> ReconcileSummary:
        settled = 0
        positive_outcomes = 0
        pnl = Decimal(0)
        for trade in self._repository.open_trades():
            market_slug = str(trade["market_slug"])
            response = self._gateway.get_settlement(market_slug)
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


def _is_weather_event(event: Mapping[str, object]) -> bool:
    category = str(event.get("category") or "").lower()
    text = " ".join(
        str(event.get(key) or "") for key in ("title", "description", "slug", "category")
    ).lower()
    return category == "weather" or any(
        term in text for term in ("weather", "temperature", "rainfall")
    )
