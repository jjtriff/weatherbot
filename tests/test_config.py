from __future__ import annotations

from decimal import Decimal

import pytest

from weatherbot.config import ConfigurationError, load_paper_strategy_settings, load_settings


def test_defaults_to_paper_mode_without_credentials() -> None:
    settings = load_settings({})

    assert settings.mode == "paper"
    assert settings.database_path.as_posix() == "data/v3/weatherbot.sqlite3"


def test_live_mode_requires_polymarket_us_credentials() -> None:
    with pytest.raises(ConfigurationError, match="POLYMARKET_KEY_ID"):
        load_settings({"WEATHERBOT_MODE": "live"})


def test_live_mode_rejects_legacy_polygon_credentials() -> None:
    with pytest.raises(ConfigurationError, match="POLYMARKET_KEY_ID"):
        load_settings(
            {
                "WEATHERBOT_MODE": "live",
                "PK": "legacy-private-key",
                "WALLET": "0xlegacy",
                "SIG_TYPE": "0",
            }
        )


def test_rejects_unknown_mode() -> None:
    with pytest.raises(ConfigurationError, match="WEATHERBOT_MODE"):
        load_settings({"WEATHERBOT_MODE": "dangerous"})


def test_paper_strategy_settings_read_profit_constraints_from_json(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """{
            "balance": 1000,
            "max_bet": 25,
            "min_ev": 0.10,
            "max_price": 0.45,
            "min_volume": 500,
            "min_hours": 2,
            "max_hours": 72,
            "kelly_fraction": 0.25,
            "max_slippage": 0.03
        }"""
    )

    strategy = load_paper_strategy_settings(config_path)

    assert strategy.starting_balance == Decimal(1000)
    assert strategy.max_bet == Decimal(25)
    assert strategy.min_hours == Decimal(2)
