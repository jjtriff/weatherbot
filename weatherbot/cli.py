from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import cast

import requests
from dotenv import load_dotenv

from weatherbot.config import (
    ConfigurationError,
    load_paper_strategy_settings,
    load_settings,
)
from weatherbot.marketplace import PolymarketUSGateway
from weatherbot.paper import PaperWeatherBot, format_rejections
from weatherbot.repository import PaperRepository
from weatherbot.weather import (
    ForecastSelector,
    HttpSession,
    OpenMeteoProvider,
    VisualCrossingProvider,
)


def _paper_bot(database_override: str | None) -> PaperWeatherBot:
    load_dotenv()
    settings = load_settings(os.environ)
    if settings.mode != "paper":
        raise ConfigurationError("paper commands require WEATHERBOT_MODE=paper")
    database_path = (
        settings.database_path
        if database_override is None
        else settings.database_path.with_name(database_override)
    )
    strategy_path = Path(os.environ.get("WEATHERBOT_STRATEGY_CONFIG", "config.json"))
    strategy_settings = load_paper_strategy_settings(strategy_path)
    session = cast(HttpSession, requests.Session())
    current_day_providers = (
        [VisualCrossingProvider(api_key=strategy_settings.visual_crossing_key, session=session)]
        if strategy_settings.visual_crossing_key
        else []
    )
    return PaperWeatherBot(
        gateway=PolymarketUSGateway.public(),
        repository=PaperRepository(database_path),
        strategy_settings=strategy_settings,
        forecast_selector=ForecastSelector(
            providers=[OpenMeteoProvider(session=session)],
            current_day_providers=current_day_providers,
        ),
        heartbeat_path=database_path.with_name("paper-run-heartbeat.json"),
        debug_analysis=settings.debug_analysis,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="weatherbot")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("paper-scan", help="run one paper-only weather scan")
    run = subcommands.add_parser("paper-run", help="run paper scans continuously")
    run.add_argument("--interval", type=int, default=3600)
    subcommands.add_parser("paper-status", help="show paper learning results")
    debug_report = subcommands.add_parser(
        "paper-debug-report", help="show the latest per-market debug analysis scan"
    )
    debug_report.add_argument("--limit", type=int, default=200)

    arguments = parser.parse_args()
    bot = _paper_bot(None)
    if arguments.command == "paper-scan":
        reconciliation = bot.reconcile_once()
        summary = bot.scan_once()
        print(
            f"paper_scan discovered={summary.discovered} new_paper_trades={summary.paper_trades} "
            f"rejections={format_rejections(summary.rejections)} "
            f"settled={reconciliation.settled} learned_markets={reconciliation.learned_markets} "
            f"calibration_updates={reconciliation.calibration_updates} "
            f"positive_outcomes={reconciliation.positive_outcomes} "
            f"hypothetical_pnl={reconciliation.hypothetical_pnl}"
        )
    elif arguments.command == "paper-run":
        bot.run_forever(arguments.interval)
    elif arguments.command == "paper-status":
        stats = (
            bot._repository.learning_stats()
        )  # CLI presentation only; execution remains paper-only.
        print(
            f"paper_status settled={stats['settled_trades']} "
            f"positive_outcomes={stats['positive_outcomes']} "
            f"positive_outcome_rate={stats['positive_outcome_rate']} "
            f"hypothetical_pnl={stats['hypothetical_pnl']}"
        )
    elif arguments.command == "paper-debug-report":
        analyses = bot._repository.latest_market_analyses()[: arguments.limit]
        if not analyses:
            print("paper_debug_report no_debug_analysis_rows")
            return
        scan_id = analyses[0]["scan_id"]
        scanned_at = analyses[0]["scanned_at"]
        print(f"paper_debug_report scan_id={scan_id} scanned_at={scanned_at} rows={len(analyses)}")
        for record in analyses:
            print(
                " | ".join(
                    [
                        str(record["event_title"]),
                        str(record["market_title"]),
                        f"reason={record['decision_reason']}",
                        f"forecast={record['forecast_value']}",
                        f"forecast_source={record['forecast_source']}",
                        f"secondary_forecast={record['secondary_forecast_value']}",
                        f"secondary_source={record['secondary_forecast_source']}",
                        f"forecast_disagreement={record['forecast_disagreement']}",
                        f"probability={record['probability']}",
                        f"bid={record['best_bid']}",
                        f"ask={record['best_ask']}",
                        f"volume={record['volume']}",
                        f"quote_source={record['quote_source']}",
                    ]
                )
            )


if __name__ == "__main__":
    main()
