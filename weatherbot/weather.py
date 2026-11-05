from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class City:
    slug: str
    name: str
    station: str
    latitude: Decimal
    longitude: Decimal
    timezone: str
    temperature_unit: str


@dataclass(frozen=True)
class Forecast:
    city_slug: str
    station: str
    target_date: date
    value: Decimal
    source: str


@dataclass(frozen=True)
class ForecastSelection:
    forecast: Forecast
    reason: str
    disagreement: Decimal | None
    forecasts: tuple[Forecast, ...] = ()


CITIES: Mapping[str, City] = {
    "nyc": City(
        slug="nyc",
        name="NYC",
        station="KLGA",
        latitude=Decimal("40.7772"),
        longitude=Decimal("-73.8726"),
        timezone="America/New_York",
        temperature_unit="fahrenheit",
    ),
    "chicago": City(
        slug="chicago",
        name="Chicago",
        station="KMDW",
        latitude=Decimal("41.7868"),
        longitude=Decimal("-87.7522"),
        timezone="America/Chicago",
        temperature_unit="fahrenheit",
    ),
    "miami": City(
        slug="miami",
        name="Miami",
        station="KMIA",
        latitude=Decimal("25.7959"),
        longitude=Decimal("-80.2870"),
        timezone="America/New_York",
        temperature_unit="fahrenheit",
    ),
    "los-angeles": City(
        slug="los-angeles",
        name="Los Angeles",
        station="KLAX",
        latitude=Decimal("33.9425"),
        longitude=Decimal("-118.4081"),
        timezone="America/Los_Angeles",
        temperature_unit="fahrenheit",
    ),
    "san-francisco": City(
        slug="san-francisco",
        name="San Francisco",
        station="KSFO",
        latitude=Decimal("37.6213"),
        longitude=Decimal("-122.3790"),
        timezone="America/Los_Angeles",
        temperature_unit="fahrenheit",
    ),
    "philadelphia": City(
        slug="philadelphia",
        name="Philadelphia",
        station="KPHL",
        latitude=Decimal("39.8744"),
        longitude=Decimal("-75.2424"),
        timezone="America/New_York",
        temperature_unit="fahrenheit",
    ),
}


class Response(Protocol):
    def json(self) -> Mapping[str, object]: ...


class HttpSession(Protocol):
    def get(self, url: str, **kwargs: object) -> Response: ...


class WeatherProvider(Protocol):
    def daily_high(self, city: City, target_date: date) -> Forecast | None: ...


class OpenMeteoProvider:
    """Station-coordinate daily high forecasts from Open-Meteo's ECMWF IFS model."""

    model = "ecmwf_ifs025"
    source = f"open-meteo:{model}"
    _url = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, *, session: HttpSession) -> None:
        self._session = session

    def daily_high(self, city: City, target_date: date) -> Forecast | None:
        response = self._session.get(
            self._url,
            params={
                "latitude": str(city.latitude),
                "longitude": str(city.longitude),
                "daily": "temperature_2m_max",
                "temperature_unit": city.temperature_unit,
                "timezone": city.timezone,
                "models": self.model,
                "forecast_days": "7",
            },
            timeout=(5, 10),
        )
        payload = response.json()
        daily = payload.get("daily")
        if not isinstance(daily, Mapping):
            return None
        dates = daily.get("time")
        temperatures = daily.get("temperature_2m_max")
        if not isinstance(dates, list) or not isinstance(temperatures, list):
            return None
        for forecast_date, temperature in zip(dates, temperatures, strict=True):
            if forecast_date != target_date.isoformat() or temperature is None:
                continue
            return Forecast(
                city_slug=city.slug,
                station=city.station,
                target_date=target_date,
                value=Decimal(str(temperature)),
                source=self.source,
            )
        return None


class VisualCrossingProvider:
    """Station-coordinate daily high forecasts from Visual Crossing Timeline API."""

    source = "visual-crossing:timeline"
    _url = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"

    def __init__(self, *, api_key: str, session: HttpSession) -> None:
        self._api_key = api_key
        self._session = session

    def daily_high(self, city: City, target_date: date) -> Forecast | None:
        response = self._session.get(
            f"{self._url}/{city.latitude},{city.longitude}/{target_date.isoformat()}",
            params={
                "unitGroup": "us" if city.temperature_unit == "fahrenheit" else "metric",
                "include": "days",
                "elements": "datetime,tempmax",
                "key": self._api_key,
                "contentType": "json",
            },
            timeout=(5, 10),
        )
        payload = response.json()
        days = payload.get("days")
        if not isinstance(days, list):
            return None
        for day in days:
            if not isinstance(day, Mapping) or day.get("datetime") != target_date.isoformat():
                continue
            temperature = day.get("tempmax")
            if temperature is None:
                continue
            return Forecast(
                city_slug=city.slug,
                station=city.station,
                target_date=target_date,
                value=Decimal(str(temperature)),
                source=self.source,
            )
        return None


class ForecastSelector:
    """Select a primary forecast and query secondary providers for the current market day."""

    def __init__(
        self,
        *,
        providers: Sequence[WeatherProvider],
        current_day_providers: Sequence[WeatherProvider] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._providers = tuple(providers)
        self._current_day_providers = tuple(current_day_providers)
        self._now = now or (lambda: datetime.now(ZoneInfo("UTC")))

    def select(self, city: City, target_date: date) -> ForecastSelection | None:
        providers = self._providers
        if target_date == local_market_date(self._now(), city.timezone):
            providers += self._current_day_providers
        forecasts = [
            forecast
            for provider in providers
            if (forecast := provider.daily_high(city, target_date)) is not None
        ]
        if not forecasts:
            return None
        values = [forecast.value for forecast in forecasts]
        disagreement = max(values) - min(values) if len(values) > 1 else None
        return ForecastSelection(
            forecast=forecasts[0],
            reason="first_available_provider",
            disagreement=disagreement,
            forecasts=tuple(forecasts),
        )


def local_market_date(now: datetime, timezone_name: str) -> date:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(ZoneInfo(timezone_name)).date()
