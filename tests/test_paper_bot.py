from __future__ import annotations

from decimal import Decimal

from weatherbot.domain import MarketQuote
from weatherbot.paper import PaperWeatherBot
from weatherbot.repository import PaperRepository


class FakeGateway:
    def __init__(self) -> None:
        self.settlement_value: int | None = None

    def list_events(self) -> list[dict[str, object]]:
        return [
            {
                "category": "weather",
                "title": "Highest temperature in Paris",
                "markets": [
                    {"slug": "paris-14c", "title": "Will Paris reach 14C?", "closed": False},
                    {"slug": "paris-15c", "title": "Will Paris reach 15C?", "closed": False},
                ],
            }
        ]

    def get_bbo(self, market_slug: str) -> MarketQuote:
        prices = {
            "paris-14c": (Decimal("0.10"), Decimal("0.12")),
            "paris-15c": (Decimal("0.40"), Decimal("0.45")),
        }
        bid, ask = prices[market_slug]
        return MarketQuote(market_slug=market_slug, best_bid=bid, best_ask=ask, volume=Decimal(1))

    def get_settlement(self, market_slug: str) -> dict[str, object]:
        return {"slug": market_slug, "settlement": self.settlement_value}


def test_paper_scan_trades_every_discovered_weather_market_without_risk_filters(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    gateway = FakeGateway()
    bot = PaperWeatherBot(gateway=gateway, repository=repository, stake_per_trade=Decimal("1.00"))

    summary = bot.scan_once()

    assert summary.discovered == 2
    assert summary.paper_trades == 2
    assert repository.count_open_trades() == 2
    assert repository.get_trade("paris-14c")["entry_price"] == "0.12"


def test_reconciliation_records_positive_outcomes_and_hypothetical_profit(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    gateway = FakeGateway()
    bot = PaperWeatherBot(gateway=gateway, repository=repository, stake_per_trade=Decimal("1.00"))
    bot.scan_once()
    gateway.settlement_value = 1

    summary = bot.reconcile_once()

    assert summary.settled == 2
    assert summary.positive_outcomes == 2
    assert summary.hypothetical_pnl > Decimal(0)
    stats = repository.learning_stats()
    assert stats["positive_outcomes"] == 2
    assert stats["hypothetical_pnl"] > Decimal(0)
    assert stats["positive_outcome_rate"] == Decimal(1)
