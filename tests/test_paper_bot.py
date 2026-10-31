from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from weatherbot.config import PaperStrategySettings
from weatherbot.domain import MarketQuote
from weatherbot.marketplace import MarketplaceError
from weatherbot.paper import PaperWeatherBot
from weatherbot.repository import PaperRepository
from weatherbot.weather import Forecast, ForecastSelection


class FakeGateway:
    def __init__(self) -> None:
        self.settlement_value: int | None = None

    def list_events(self) -> list[dict[str, object]]:
        return [
            {
                "category": "weather",
                "title": "Highest temperature in Chicago on October 31?",
                "markets": [
                    {
                        "slug": "tc-temp-mdwhigh-2026-10-31-gte64lt65f",
                        "question": "Highest temperature in Chicago on October 31?",
                        "title": "64 to 65",
                        "endDate": "2026-11-01T11:00:00Z",
                        "closed": False,
                    },
                    {
                        "slug": "tc-temp-mdwhigh-2026-10-31-gte72f",
                        "question": "Highest temperature in Chicago on October 31?",
                        "title": "72 or above",
                        "endDate": "2026-11-01T11:00:00Z",
                        "closed": False,
                    },
                    {
                        "slug": "chicago-rain",
                        "question": "Will it rain in Chicago tomorrow?",
                        "closed": False,
                    },
                ],
            }
        ]

    def get_bbo(self, market_slug: str) -> MarketQuote:
        prices = {
            "tc-temp-mdwhigh-2026-10-31-gte64lt65f": (Decimal("0.11"), Decimal("0.12")),
            "tc-temp-mdwhigh-2026-10-31-gte72f": (Decimal("0.19"), Decimal("0.20")),
        }
        bid, ask = prices[market_slug]
        return MarketQuote(
            market_slug=market_slug, best_bid=bid, best_ask=ask, volume=Decimal(1000)
        )

    def get_settlement(self, market_slug: str) -> dict[str, object]:
        return {"slug": market_slug, "settlement": self.settlement_value}


class FixedForecastSelector:
    def select(self, city, target_date):
        return ForecastSelection(
            forecast=Forecast(
                city_slug=city.slug,
                station=city.station,
                target_date=target_date,
                value=Decimal("64.5"),
                source="test",
            ),
            reason="test",
            disagreement=None,
        )


def strategy_settings() -> PaperStrategySettings:
    return PaperStrategySettings(
        starting_balance=Decimal(1000),
        max_bet=Decimal("2.00"),
        min_ev=Decimal("0.10"),
        max_price=Decimal("0.45"),
        min_volume=Decimal(500),
        min_hours=Decimal(2),
        max_hours=Decimal(72),
        max_spread=Decimal("0.03"),
        kelly_fraction=Decimal("0.25"),
    )


def paper_bot(repository: PaperRepository, gateway: FakeGateway | None = None) -> PaperWeatherBot:
    return PaperWeatherBot(
        gateway=gateway or FakeGateway(),
        repository=repository,
        strategy_settings=strategy_settings(),
        forecast_selector=FixedForecastSelector(),
        now=lambda: datetime(2026, 10, 31, 12, tzinfo=timezone.utc),
    )


def test_paper_scan_only_trades_positive_ev_temperature_bucket(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    summary = paper_bot(repository).scan_once()

    assert summary.discovered == 2
    assert summary.paper_trades == 1
    assert repository.count_open_trades() == 1
    trade = repository.get_trade("tc-temp-mdwhigh-2026-10-31-gte64lt65f")
    assert trade["entry_price"] == "0.12"
    assert trade["cost"] == "2.00"


def test_paper_scan_is_idempotent_and_respects_exposure(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    bot = paper_bot(repository)

    bot.scan_once()
    summary = bot.scan_once()

    assert summary.paper_trades == 0
    assert repository.count_open_trades() == 1


def test_paper_scan_uses_search_quote_before_requesting_bbo(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class SearchQuoteGateway(FakeGateway):
        def list_events(self) -> list[dict[str, object]]:
            events = super().list_events()
            market = events[0]["markets"][0]
            assert isinstance(market, dict)
            market["bestBidQuote"] = {"value": "0.11", "currency": "USD"}
            market["bestAskQuote"] = {"value": "0.12", "currency": "USD"}
            market["sharesTraded"] = "1000"
            return events

        def get_bbo(self, market_slug: str) -> MarketQuote:
            if market_slug.endswith("gte64lt65f"):
                raise AssertionError("search quote should avoid the BBO request")
            return super().get_bbo(market_slug)

    assert paper_bot(repository, SearchQuoteGateway()).scan_once().paper_trades == 1


def test_paper_scan_skips_temporarily_unavailable_bbo_without_failing(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class UnavailableQuoteGateway(FakeGateway):
        def get_bbo(self, market_slug: str) -> MarketQuote:
            raise MarketplaceError("rate limited")

    assert paper_bot(repository, UnavailableQuoteGateway()).scan_once().paper_trades == 0
    assert repository.count_open_trades() == 0


def test_reconciliation_records_positive_outcomes_and_hypothetical_profit(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    gateway = FakeGateway()
    bot = paper_bot(repository, gateway)
    bot.scan_once()
    gateway.settlement_value = 1

    summary = bot.reconcile_once()

    assert summary.settled == 1
    assert summary.positive_outcomes == 1
    assert summary.hypothetical_pnl > Decimal(0)
    assert repository.learning_stats()["positive_outcome_rate"] == Decimal(1)


def test_reconciliation_keeps_open_trade_when_settlement_is_not_available(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class UnsettledGateway(FakeGateway):
        def get_settlement(self, market_slug: str) -> dict[str, object]:
            raise MarketplaceError("settlement not found")

    bot = paper_bot(repository, UnsettledGateway())
    bot.scan_once()

    assert bot.reconcile_once().settled == 0
    assert repository.count_open_trades() == 1
