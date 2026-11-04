from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from weatherbot.config import PaperStrategySettings
from weatherbot.domain import MarketQuote, TemperatureBucket
from weatherbot.marketplace import MarketplaceError
from weatherbot.paper import (
    PaperWeatherBot,
    ReconcileSummary,
    ScanSummary,
    _resolution_metadata,
    write_heartbeat,
)
from weatherbot.probability import bucket_probability
from weatherbot.repository import PaperRepository
from weatherbot.weather import Forecast, ForecastSelection


class FakeGateway:
    def __init__(self) -> None:
        self.settlement_value: int | float | None = None

    def list_events(self) -> list[dict[str, object]]:
        return [
            {
                "category": "weather",
                "title": "Highest temperature in Chicago on October 31?",
                "markets": [
                    {
                        "slug": "tc-temp-mdwhigh-2026-10-31-gte64lt65f",
                        "question": "Highest temperature in Chicago on October 31?",
                        "description": (
                            "Will the highest temperature recorded at Chicago Midway Airport (KMDW) "
                            "in Chicago for 2026-10-31 as reported by the National Weather Service's "
                            "Climatological Report (Daily) be between 64F and 65F? "
                            "Outcome verified from NWS Climatological Report."
                        ),
                        "title": "64 to 65",
                        "endDate": "2026-11-01T11:00:00Z",
                        "closed": False,
                    },
                    {
                        "slug": "tc-temp-mdwhigh-2026-10-31-gte72f",
                        "question": "Highest temperature in Chicago on October 31?",
                        "description": (
                            "Will the highest temperature recorded at Chicago Midway Airport (KMDW) "
                            "in Chicago for 2026-10-31 as reported by the National Weather Service's "
                            "Climatological Report (Daily) be 72F or above? "
                            "Outcome verified from NWS Climatological Report."
                        ),
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


def paper_bot(
    repository: PaperRepository,
    gateway: FakeGateway | None = None,
    *,
    debug_analysis: bool = False,
    now: datetime | None = None,
) -> PaperWeatherBot:
    return PaperWeatherBot(
        gateway=gateway or FakeGateway(),
        repository=repository,
        strategy_settings=strategy_settings(),
        forecast_selector=FixedForecastSelector(),
        now=lambda: now or datetime(2026, 10, 31, 12, tzinfo=timezone.utc),
        debug_analysis=debug_analysis,
    )


def test_resolution_metadata_accepts_airport_suffix_station_format() -> None:
    rule, source, station = _resolution_metadata(
        {
            "description": (
                "Will the temperature recorded at San Jose (SJC Airport) be 70F? "
                "Outcome verified from NWS Climatological Report."
            )
        }
    )

    assert rule is not None
    assert source == "NWS Climatological Report"
    assert station == "SJC"


def test_paper_scan_only_trades_positive_ev_temperature_bucket(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    summary = paper_bot(repository).scan_once()

    assert summary.discovered == 2
    assert summary.paper_trades == 1
    assert summary.rejections == {"ev_too_low": 1, "unsupported_contract": 1}
    assert repository.count_open_trades() == 1
    trade = repository.get_trade("tc-temp-mdwhigh-2026-10-31-gte64lt65f")
    assert trade["entry_price"] == "0.12"
    assert trade["cost"] == "2.00"


def test_debug_analysis_records_raw_quote_and_decision_for_every_market(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    paper_bot(repository, debug_analysis=True).scan_once()

    analyses = repository.latest_market_analyses()
    assert len(analyses) == 3
    accepted = next(
        analysis
        for analysis in analyses
        if analysis["market_slug"] == "tc-temp-mdwhigh-2026-10-31-gte64lt65f"
    )
    assert accepted["decision_reason"] == "accepted"
    assert accepted["forecast_value"] == "64.5"
    assert accepted["best_ask"] == "0.12"
    assert accepted["quote_source"] == "bbo"
    assert accepted["resolution_source"] == "NWS Climatological Report"
    assert accepted["resolution_station"] == "KMDW"
    assert "Outcome verified from NWS Climatological Report." in accepted["resolution_rule"]
    assert accepted["probability"] is not None
    assert any(analysis["decision_reason"] == "ev_too_low" for analysis in analyses)
    assert any(analysis["decision_reason"] == "unsupported_contract" for analysis in analyses)


def test_paper_scan_is_idempotent_and_respects_exposure(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    bot = paper_bot(repository)

    bot.scan_once()
    summary = bot.scan_once()

    assert summary.paper_trades == 0
    assert summary.rejections["already_open"] == 1
    assert repository.count_open_trades() == 1


def test_paper_scan_verifies_rejecting_search_quote_with_live_bbo(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class SearchQuoteGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.bbo_requests: list[str] = []

        def list_events(self) -> list[dict[str, object]]:
            events = super().list_events()
            market = events[0]["markets"][0]
            assert isinstance(market, dict)
            market["bestBidQuote"] = {"value": "0.11", "currency": "USD"}
            market["bestAskQuote"] = {"value": "0.12", "currency": "USD"}
            market["sharesTraded"] = "0"
            return events

        def get_bbo(self, market_slug: str) -> MarketQuote:
            self.bbo_requests.append(market_slug)
            return super().get_bbo(market_slug)

    gateway = SearchQuoteGateway()

    assert paper_bot(repository, gateway).scan_once().paper_trades == 1
    assert "tc-temp-mdwhigh-2026-10-31-gte64lt65f" in gateway.bbo_requests


def test_paper_scan_defers_live_bbo_verification_for_future_market_dates(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class FutureSearchQuoteGateway(FakeGateway):
        def list_events(self) -> list[dict[str, object]]:
            events = super().list_events()
            market = events[0]["markets"][0]
            assert isinstance(market, dict)
            market["slug"] = "tc-temp-mdwhigh-2026-11-01-gte64lt65f"
            market["endDate"] = "2026-11-02T11:00:00Z"
            market["bestBidQuote"] = {"value": "0.11", "currency": "USD"}
            market["bestAskQuote"] = {"value": "0.12", "currency": "USD"}
            market["sharesTraded"] = "0"
            return events

        def get_bbo(self, market_slug: str) -> MarketQuote:
            if market_slug.endswith("gte64lt65f"):
                raise AssertionError("future market should not use live BBO verification")
            return super().get_bbo(market_slug)

    summary = paper_bot(repository, FutureSearchQuoteGateway()).scan_once()

    assert summary.rejections["volume_too_low"] == 1


def test_paper_scan_skips_temporarily_unavailable_bbo_without_failing(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class UnavailableQuoteGateway(FakeGateway):
        def get_bbo(self, market_slug: str) -> MarketQuote:
            raise MarketplaceError("rate limited")

    summary = paper_bot(repository, UnavailableQuoteGateway()).scan_once()

    assert summary.paper_trades == 0
    assert summary.rejections["quote_unavailable"] == 2
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


def test_reconciliation_ignores_settlement_for_a_different_market_slug(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")

    class WrongSlugGateway(FakeGateway):
        def get_settlement(self, market_slug: str) -> dict[str, object]:
            return {"slug": "different-market", "settlement": 1.0}

    bot = paper_bot(repository, WrongSlugGateway(), debug_analysis=True)
    bot.scan_once()

    summary = paper_bot(
        repository,
        WrongSlugGateway(),
        debug_analysis=True,
        now=datetime(2026, 11, 1, 12, tzinfo=timezone.utc),
    ).reconcile_once()

    assert summary.settled == 0
    assert summary.learned_markets == 0
    assert repository.count_open_trades() == 1


def test_reconciliation_records_conclusion_for_a_settled_untraded_bucket(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    gateway = FakeGateway()
    paper_bot(repository, gateway, debug_analysis=True).scan_once()
    gateway.settlement_value = 0.0

    summary = paper_bot(
        repository,
        gateway,
        debug_analysis=True,
        now=datetime(2026, 11, 1, 12, tzinfo=timezone.utc),
    ).reconcile_once()

    assert summary.learned_markets == 2
    conclusion = repository.get_market_conclusion("tc-temp-mdwhigh-2026-10-31-gte72f")
    assert conclusion["settlement"] == 0
    assert conclusion["decision_reason"] == "ev_too_low"
    assert conclusion["resolution_source"] == "NWS Climatological Report"
    assert conclusion["resolution_station"] == "KMDW"
    assert Decimal(str(conclusion["average_brier_score"])) > Decimal(0)


def test_calibration_requires_distinct_resolved_market_days(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    for index in range(30):
        market_slug = f"tc-temp-mdwhigh-2026-10-31-gte64lt65f-{index}"
        repository.record_market_analysis(
            scan_id=f"scan-{index}",
            scanned_at="2026-10-30T12:00:00+00:00",
            event_title="Highest temperature in Chicago on October 31?",
            market_slug=market_slug,
            market_title="64 to 65",
            city_slug="chicago",
            target_date="2026-10-31",
            settlement_time="2026-11-01T11:00:00+00:00",
            hours_to_settlement=Decimal(23),
            forecast_value=Decimal(60),
            forecast_source="test",
            probability=Decimal("0.01"),
            quote=None,
            quote_source="bbo",
            decision_reason="ev_too_low",
        )
        repository.record_market_conclusion(market_slug, settlement=1)

    update = repository.refresh_forecast_calibration(
        city_slug="chicago", forecast_source="test", default_sigma=Decimal(2)
    )

    assert update.applied is False
    assert update.market_day_count == 1
    assert repository.calibrated_sigma("chicago", "test", Decimal(2)) == Decimal(2)


def test_calibration_updates_sigma_after_minimum_resolved_observations(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    for index in range(30):
        market_slug = f"tc-temp-mdwhigh-2026-10-31-gte64lt65f-{index}"
        repository.record_market_analysis(
            scan_id=f"scan-{index}",
            scanned_at="2026-10-30T12:00:00+00:00",
            event_title="Highest temperature in Chicago on October 31?",
            market_slug=market_slug,
            market_title="64 to 65",
            city_slug="chicago",
            target_date=f"2026-10-{index + 1:02d}",
            settlement_time="2026-11-01T11:00:00+00:00",
            hours_to_settlement=Decimal(23),
            forecast_value=Decimal(60),
            forecast_source="test",
            probability=Decimal("0.01"),
            quote=None,
            quote_source="bbo",
            decision_reason="ev_too_low",
        )
        repository.record_market_conclusion(market_slug, settlement=1)

    update = repository.refresh_forecast_calibration(
        city_slug="chicago", forecast_source="test", default_sigma=Decimal(2)
    )

    assert update.applied is True
    assert update.observation_count == 30
    assert update.market_day_count == 30
    assert update.sigma > Decimal(2)
    assert repository.calibrated_sigma("chicago", "test", Decimal(2)) == update.sigma


def test_paper_scan_uses_persisted_calibrated_sigma(tmp_path) -> None:
    repository = PaperRepository(tmp_path / "paper.sqlite3")
    for index in range(30):
        market_slug = f"tc-temp-mdwhigh-2026-10-31-gte64lt65f-{index}"
        repository.record_market_analysis(
            scan_id=f"scan-{index}",
            scanned_at="2026-10-30T12:00:00+00:00",
            event_title="Highest temperature in Chicago on October 31?",
            market_slug=market_slug,
            market_title="64 to 65",
            city_slug="chicago",
            target_date=f"2026-10-{index + 1:02d}",
            settlement_time="2026-11-01T11:00:00+00:00",
            hours_to_settlement=Decimal(23),
            forecast_value=Decimal(60),
            forecast_source="test",
            probability=Decimal("0.01"),
            quote=None,
            quote_source="bbo",
            decision_reason="ev_too_low",
        )
        repository.record_market_conclusion(market_slug, settlement=1)
    update = repository.refresh_forecast_calibration(
        city_slug="chicago", forecast_source="test", default_sigma=Decimal(2)
    )

    paper_bot(repository, debug_analysis=True).scan_once()

    analysis = next(
        row
        for row in repository.latest_market_analyses()
        if row["market_slug"] == "tc-temp-mdwhigh-2026-10-31-gte64lt65f"
    )
    expected = bucket_probability(
        Decimal("64.5"), TemperatureBucket(lower=Decimal(64), upper=Decimal(65)), update.sigma
    )
    assert Decimal(str(analysis["probability"])) == expected


def test_runner_heartbeat_records_cycle_status(tmp_path) -> None:
    heartbeat_path = tmp_path / "paper-run-heartbeat.json"

    write_heartbeat(
        heartbeat_path,
        now=datetime(2026, 10, 31, 19, tzinfo=timezone.utc),
        scan=ScanSummary(discovered=5, paper_trades=1, rejections={"ev_too_low": 4}),
        reconciliation=ReconcileSummary(
            settled=2, positive_outcomes=1, hypothetical_pnl=Decimal("3.25")
        ),
    )

    assert json.loads(heartbeat_path.read_text()) == {
        "calibration_updates": 0,
        "discovered": 5,
        "hypothetical_pnl": "3.25",
        "learned_markets": 0,
        "new_paper_trades": 1,
        "positive_outcomes": 1,
        "rejections": {"ev_too_low": 4},
        "settled": 2,
        "updated_at": "2026-10-31T19:00:00+00:00",
    }
