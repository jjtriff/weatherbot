from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol

from polymarket_us import PolymarketUS

from weatherbot.config import Settings
from weatherbot.domain import BUY_LONG, GTC, LIMIT, USD, MarketQuote, TradeCandidate


class MarketplaceError(RuntimeError):
    """Base error raised when the Polymarket US SDK request cannot complete."""


class LiveTradingDisabled(MarketplaceError):
    """Raised when an order request is attempted outside explicitly enabled live mode."""


class MarketplaceGateway(Protocol):
    def get_bbo(self, market_slug: str) -> MarketQuote: ...

    def list_events(self) -> list[Mapping[str, object]]: ...

    def get_settlement(self, market_slug: str) -> Mapping[str, object]: ...


class PolymarketUSGateway:
    """All Polymarket US access goes through the official SDK client."""

    def __init__(self, *, client: Any, live_enabled: bool) -> None:
        self._client = client
        self._live_enabled = live_enabled

    @classmethod
    def public(cls) -> PolymarketUSGateway:
        return cls(client=PolymarketUS(), live_enabled=False)

    @classmethod
    def authenticated(cls, settings: Settings) -> PolymarketUSGateway:
        if (
            settings.mode != "live"
            or not settings.polymarket_key_id
            or not settings.polymarket_secret_key
        ):
            raise LiveTradingDisabled("live mode and Polymarket US credentials are required")
        return cls(
            client=PolymarketUS(
                key_id=settings.polymarket_key_id,
                secret_key=settings.polymarket_secret_key,
            ),
            live_enabled=True,
        )

    def list_events(self) -> list[Mapping[str, object]]:
        try:
            response = self._client.search.query(
                {"query": "weather", "limit": 100, "status": "active"}
            )
            if isinstance(response, Mapping):
                events = response.get("events", [])
            else:
                events = getattr(response, "events", [])
            return [event for event in events if isinstance(event, Mapping)]
        except Exception as exc:
            raise MarketplaceError("Polymarket US event discovery failed") from exc

    def get_bbo(self, market_slug: str) -> MarketQuote:
        try:
            response = self._client.markets.bbo(market_slug)
            payload = _mapping(response)
            market_data = _mapping(payload.get("marketData", {}))
            bid = _decimal_or_none(market_data.get("bestBid"))
            ask = _decimal_or_none(market_data.get("bestAsk"))
            volume = _decimal_or_none(
                market_data.get("volume") or market_data.get("sharesTraded")
            ) or Decimal(0)
            return MarketQuote(market_slug=market_slug, best_bid=bid, best_ask=ask, volume=volume)
        except MarketplaceError:
            raise
        except Exception as exc:
            raise MarketplaceError(f"Polymarket US BBO request failed for {market_slug}") from exc

    def get_settlement(self, market_slug: str) -> Mapping[str, object]:
        try:
            return _mapping(self._client.markets.settlement(market_slug))
        except Exception as exc:
            raise MarketplaceError(
                f"Polymarket US settlement request failed for {market_slug}"
            ) from exc

    def get_balances(self) -> Mapping[str, object]:
        self._require_live()
        try:
            return _mapping(self._client.account.balances())
        except Exception as exc:
            raise MarketplaceError("Polymarket US balance request failed") from exc

    def list_positions(self) -> Mapping[str, object]:
        self._require_live()
        try:
            return _mapping(self._client.portfolio.positions())
        except Exception as exc:
            raise MarketplaceError("Polymarket US position request failed") from exc

    def list_activities(self) -> Mapping[str, object]:
        self._require_live()
        try:
            return _mapping(self._client.portfolio.activities())
        except Exception as exc:
            raise MarketplaceError("Polymarket US activity request failed") from exc

    def create_order(self, candidate: TradeCandidate) -> Mapping[str, object]:
        self._require_live()
        params: dict[str, object] = {
            "marketSlug": candidate.market_slug,
            "intent": BUY_LONG,
            "type": LIMIT,
            "price": {"value": str(candidate.entry_price), "currency": USD},
            "quantity": float(candidate.shares),
            "tif": GTC,
        }
        try:
            return _mapping(self._client.orders.create(params))
        except Exception as exc:
            raise MarketplaceError(
                f"Polymarket US order creation failed for {candidate.market_slug}"
            ) from exc

    def _require_live(self) -> None:
        if not self._live_enabled:
            raise LiveTradingDisabled("live Polymarket US orders are disabled")


def _mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        converted = value.to_dict()
        if isinstance(converted, Mapping):
            return converted
    raise MarketplaceError("unexpected Polymarket US SDK response shape")


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        return _decimal_or_none(value.get("value"))
    return Decimal(str(value))
