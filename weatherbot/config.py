from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

Mode = Literal["paper", "live"]

DEFAULT_DATABASE_PATH = Path("data/v3/weatherbot.sqlite3")
DEFAULT_STARTING_BALANCE = Decimal("10.00")
DEFAULT_MAX_BET = Decimal("2.00")
DEFAULT_MAX_TOTAL_EXPOSURE = Decimal("10.00")
DEFAULT_MIN_EV = Decimal("0.10")
DEFAULT_MAX_PRICE = Decimal("0.45")
DEFAULT_MIN_VOLUME = Decimal(500)
DEFAULT_MAX_SPREAD = Decimal("0.03")
DEFAULT_KELLY_FRACTION = Decimal("0.25")


class ConfigurationError(ValueError):
    """Raised when runtime settings are unsafe or invalid."""


@dataclass(frozen=True)
class Settings:
    mode: Mode
    database_path: Path
    polymarket_key_id: str | None
    polymarket_secret_key: str | None
    starting_balance: Decimal
    max_bet: Decimal
    max_total_exposure: Decimal
    min_ev: Decimal
    max_price: Decimal
    min_volume: Decimal
    max_spread: Decimal
    kelly_fraction: Decimal
    visual_crossing_key: str | None
    debug_analysis: bool


@dataclass(frozen=True)
class PaperStrategySettings:
    starting_balance: Decimal
    max_bet: Decimal
    min_ev: Decimal
    max_price: Decimal
    min_volume: Decimal
    min_hours: Decimal
    max_hours: Decimal
    max_spread: Decimal
    kelly_fraction: Decimal


def _decimal(environ: Mapping[str, str], key: str, default: Decimal) -> Decimal:
    value = environ.get(key)
    if value is None or not value.strip():
        return default
    try:
        return Decimal(value)
    except Exception as exc:  # Decimal's exception type is an implementation detail here.
        raise ConfigurationError(f"{key} must be a decimal number") from exc


def _bool(environ: Mapping[str, str], key: str, default: bool = False) -> bool:
    value = environ.get(key)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{key} must be a boolean")


def _json_decimal(config: Mapping[str, object], key: str, default: Decimal) -> Decimal:
    value = config.get(key, default)
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ConfigurationError(f"config.json {key} must be a decimal number") from exc


def load_paper_strategy_settings(path: Path) -> PaperStrategySettings:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read paper strategy config: {path}") from exc
    if not isinstance(payload, Mapping):
        raise ConfigurationError("paper strategy config must be a JSON object")
    settings = PaperStrategySettings(
        starting_balance=_json_decimal(payload, "balance", DEFAULT_STARTING_BALANCE),
        max_bet=_json_decimal(payload, "max_bet", DEFAULT_MAX_BET),
        min_ev=_json_decimal(payload, "min_ev", DEFAULT_MIN_EV),
        max_price=_json_decimal(payload, "max_price", DEFAULT_MAX_PRICE),
        min_volume=_json_decimal(payload, "min_volume", DEFAULT_MIN_VOLUME),
        min_hours=_json_decimal(payload, "min_hours", Decimal(0)),
        max_hours=_json_decimal(payload, "max_hours", Decimal(999999)),
        max_spread=_json_decimal(payload, "max_slippage", DEFAULT_MAX_SPREAD),
        kelly_fraction=_json_decimal(payload, "kelly_fraction", DEFAULT_KELLY_FRACTION),
    )
    if settings.starting_balance <= 0 or settings.max_bet <= 0:
        raise ConfigurationError("paper strategy balance and max_bet must be positive")
    if settings.min_hours < 0 or settings.max_hours < settings.min_hours:
        raise ConfigurationError("paper strategy hours range is invalid")
    return settings


def load_settings(environ: Mapping[str, str]) -> Settings:
    raw_mode = environ.get("WEATHERBOT_MODE", "paper").strip().lower()
    if raw_mode not in {"paper", "live"}:
        raise ConfigurationError("WEATHERBOT_MODE must be 'paper' or 'live'")
    mode: Mode = raw_mode  # type: ignore[assignment]

    key_id = environ.get("POLYMARKET_KEY_ID") or None
    secret_key = environ.get("POLYMARKET_SECRET_KEY") or None
    if mode == "live":
        if not key_id:
            raise ConfigurationError("POLYMARKET_KEY_ID is required in live mode")
        if not secret_key:
            raise ConfigurationError("POLYMARKET_SECRET_KEY is required in live mode")

    return Settings(
        mode=mode,
        database_path=Path(environ.get("WEATHERBOT_DATABASE", str(DEFAULT_DATABASE_PATH))),
        polymarket_key_id=key_id,
        polymarket_secret_key=secret_key,
        starting_balance=_decimal(environ, "WEATHERBOT_STARTING_BALANCE", DEFAULT_STARTING_BALANCE),
        max_bet=_decimal(environ, "WEATHERBOT_MAX_BET", DEFAULT_MAX_BET),
        max_total_exposure=_decimal(
            environ, "WEATHERBOT_MAX_TOTAL_EXPOSURE", DEFAULT_MAX_TOTAL_EXPOSURE
        ),
        min_ev=_decimal(environ, "WEATHERBOT_MIN_EV", DEFAULT_MIN_EV),
        max_price=_decimal(environ, "WEATHERBOT_MAX_PRICE", DEFAULT_MAX_PRICE),
        min_volume=_decimal(environ, "WEATHERBOT_MIN_VOLUME", DEFAULT_MIN_VOLUME),
        max_spread=_decimal(environ, "WEATHERBOT_MAX_SPREAD", DEFAULT_MAX_SPREAD),
        kelly_fraction=_decimal(environ, "WEATHERBOT_KELLY_FRACTION", DEFAULT_KELLY_FRACTION),
        visual_crossing_key=environ.get("VISUAL_CROSSING_KEY") or None,
        debug_analysis=_bool(environ, "WEATHERBOT_DEBUG_ANALYSIS"),
    )
