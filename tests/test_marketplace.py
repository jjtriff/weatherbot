from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from weatherbot.config import load_settings
from weatherbot.domain import BUY_LONG, GTC, LIMIT, MarketQuote, TradeCandidate
from weatherbot.marketplace import LiveTradingDisabled, PolymarketUSGateway


class FakeMarkets:
    def __init__(self) -> None:
        self.bbo_calls: list[str] = []

    def bbo(self, slug: str) -> dict[str, object]:
        self.bbo_calls.append(slug)
        return {"marketData": {"bestBid": "0.10", "bestAsk": "0.12", "volume": "750"}}

    def settlement(self, slug: str) -> dict[str, object]:
        return {"marketSlug": slug, "status": "SETTLED"}


class FakeEvents:
    def __init__(self) -> None:
        self.params: object | None = None

    def list(self, params: object | None = None) -> dict[str, object]:
        self.params = params
        return {"events": [{"slug": "paris-weather", "title": "Paris weather"}]}


class FakeSearch:
    def __init__(self) -> None:
        self.params: object | None = None

    def query(self, params: object | None = None) -> dict[str, object]:
        self.params = params
        return {"events": [{"slug": "paris-weather", "title": "Paris weather"}]}


class FakeOrders:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create(self, params: dict[str, object]) -> dict[str, object]:
        self.created.append(params)
        return {"order": {"id": "order-1"}}


class FakeClient:
    def __init__(self) -> None:
        self.markets = FakeMarkets()
        self.events = FakeEvents()
        self.search = FakeSearch()
        self.orders = FakeOrders()
        self.account = SimpleNamespace(balances=lambda: {"balances": []})
        self.portfolio = SimpleNamespace(
            positions=lambda: {"positions": []}, activities=lambda: {"activities": []}
        )


def test_gateway_uses_sdk_bbo_shape() -> None:
    client = FakeClient()
    gateway = PolymarketUSGateway(client=client, live_enabled=False)

    quote = gateway.get_bbo("paris-14c")

    assert quote == MarketQuote(
        market_slug="paris-14c",
        best_bid=Decimal("0.10"),
        best_ask=Decimal("0.12"),
        volume=Decimal(750),
    )
    assert client.markets.bbo_calls == ["paris-14c"]


def test_gateway_accepts_sdk_money_quote_objects() -> None:
    client = FakeClient()
    client.markets.bbo = lambda slug: {
        "marketData": {
            "bestBid": {"value": "0.10", "currency": "USD"},
            "bestAsk": {"value": "0.12", "currency": "USD"},
            "sharesTraded": "750",
        }
    }
    gateway = PolymarketUSGateway(client=client, live_enabled=False)

    quote = gateway.get_bbo("paris-14c")

    assert quote == MarketQuote(
        market_slug="paris-14c",
        best_bid=Decimal("0.10"),
        best_ask=Decimal("0.12"),
        volume=Decimal(750),
    )


def test_gateway_discovers_active_weather_events_through_sdk_search() -> None:
    client = FakeClient()
    gateway = PolymarketUSGateway(client=client, live_enabled=False)

    events = gateway.list_events()

    assert events == [{"slug": "paris-weather", "title": "Paris weather"}]
    assert client.search.params == {"query": "weather", "limit": 100, "status": "active"}


def test_public_gateway_cannot_create_orders() -> None:
    client = FakeClient()
    gateway = PolymarketUSGateway(client=client, live_enabled=False)
    candidate = TradeCandidate(
        market_slug="paris-14c",
        probability=Decimal("0.70"),
        entry_price=Decimal("0.12"),
        cost=Decimal("2.00"),
        shares=Decimal("16.66"),
        expected_value=Decimal("4.83"),
    )

    with pytest.raises(LiveTradingDisabled):
        gateway.create_order(candidate)

    assert client.orders.created == []


def test_live_gateway_sends_polymarket_us_order_payload() -> None:
    client = FakeClient()
    settings = load_settings(
        {
            "WEATHERBOT_MODE": "live",
            "POLYMARKET_KEY_ID": "key-id",
            "POLYMARKET_SECRET_KEY": "secret",
        }
    )
    gateway = PolymarketUSGateway(client=client, live_enabled=settings.mode == "live")
    candidate = TradeCandidate(
        market_slug="paris-14c",
        probability=Decimal("0.70"),
        entry_price=Decimal("0.12"),
        cost=Decimal("2.00"),
        shares=Decimal("16.66"),
        expected_value=Decimal("4.83"),
    )

    gateway.create_order(candidate)

    assert client.orders.created == [
        {
            "marketSlug": "paris-14c",
            "intent": BUY_LONG,
            "type": LIMIT,
            "price": {"value": "0.12", "currency": "USD"},
            "quantity": 16.66,
            "tif": GTC,
        }
    ]
