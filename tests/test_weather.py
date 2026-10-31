from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from weatherbot.weather import CITIES, OpenMeteoProvider, local_market_date


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class FakeSession:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.payload)


def test_city_registry_uses_temperature_resolution_stations() -> None:
    assert CITIES["chicago"].station == "KMDW"
    assert CITIES["philadelphia"].station == "KPHL"
    assert CITIES["san-francisco"].timezone == "America/Los_Angeles"


def test_open_meteo_forecast_uses_station_coordinates_and_explicit_model() -> None:
    session = FakeSession(
        {
            "daily": {
                "time": ["2026-10-31", "2026-11-01"],
                "temperature_2m_max": [Decimal("71.4"), Decimal("68.8")],
            }
        }
    )
    provider = OpenMeteoProvider(session=session)

    forecast = provider.daily_high(CITIES["chicago"], date(2026, 11, 1))

    assert forecast is not None
    assert forecast.value == Decimal("68.8")
    assert forecast.source == "open-meteo:ecmwf_ifs025"
    assert forecast.station == "KMDW"
    assert session.calls == [
        {
            "url": "https://api.open-meteo.com/v1/forecast",
            "params": {
                "latitude": "41.7868",
                "longitude": "-87.7522",
                "daily": "temperature_2m_max",
                "temperature_unit": "fahrenheit",
                "timezone": "America/Chicago",
                "models": "ecmwf_ifs025",
                "forecast_days": "7",
            },
            "timeout": (5, 10),
        }
    ]


def test_open_meteo_returns_none_when_target_date_has_no_value() -> None:
    session = FakeSession({"daily": {"time": ["2026-11-01"], "temperature_2m_max": [None]}})

    assert OpenMeteoProvider(session=session).daily_high(CITIES["miami"], date(2026, 11, 1)) is None


def test_local_market_date_preserves_market_timezone_across_utc_boundary() -> None:
    now = datetime(2026, 11, 1, 3, 30, tzinfo=timezone.utc)

    assert local_market_date(now, CITIES["los-angeles"].timezone) == date(2026, 10, 31)
