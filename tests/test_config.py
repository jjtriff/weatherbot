from __future__ import annotations

import pytest

from weatherbot.config import ConfigurationError, load_settings


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
