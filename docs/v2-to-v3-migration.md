# WeatherBot v2 → v3 migration

## What stays unchanged

- `bot_v1.py`, `bot_v2.py`, `config.json`, and the existing `data/` JSON ledger remain legacy artifacts.
- v3 does not import, mutate, or migrate v2 state automatically.

## What v3 uses

- Package: `weatherbot/`
- Entrypoint: `python -m weatherbot.cli`
- State database: `data/v3/weatherbot.sqlite3`
- Polymarket integration: official `polymarket-us` SDK only.

## Paper mode

```bash
cd /home/jtriff/weatherbot
WEATHERBOT_MODE=paper .venv/bin/python -m weatherbot.cli paper-scan --stake 1.00
WEATHERBOT_MODE=paper .venv/bin/python -m weatherbot.cli paper-run --interval 3600 --stake 1.00
WEATHERBOT_MODE=paper .venv/bin/python -m weatherbot.cli paper-status
```

Paper mode never constructs an authenticated SDK client and never submits orders. It creates one `$1.00` hypothetical long position per supported active daily high-temperature market, regardless of risk limits. It records settlement results, positive outcomes, positive-outcome rate, and hypothetical P&L.

## Live mode

Live execution is not implemented or enabled in v3. Existing v2 values such as `PK`, `WALLET`, `SIG_TYPE`, Polygon RPC endpoints, `web3`, and the international CLOB client are intentionally not used by v3.
