from __future__ import annotations

import argparse
import os
from decimal import Decimal

from dotenv import load_dotenv

from weatherbot.config import ConfigurationError, load_settings
from weatherbot.marketplace import PolymarketUSGateway
from weatherbot.paper import PaperWeatherBot
from weatherbot.repository import PaperRepository


def _paper_bot(database_override: str | None, stake: Decimal) -> PaperWeatherBot:
    load_dotenv()
    settings = load_settings(os.environ)
    if settings.mode != "paper":
        raise ConfigurationError("paper commands require WEATHERBOT_MODE=paper")
    database_path = (
        settings.database_path
        if database_override is None
        else settings.database_path.with_name(database_override)
    )
    return PaperWeatherBot(
        gateway=PolymarketUSGateway.public(),
        repository=PaperRepository(database_path),
        stake_per_trade=stake,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="weatherbot")
    subcommands = parser.add_subparsers(dest="command", required=True)
    scan = subcommands.add_parser("paper-scan", help="run one paper-only weather scan")
    scan.add_argument("--stake", type=Decimal, default=Decimal("1.00"))
    run = subcommands.add_parser("paper-run", help="run paper scans continuously")
    run.add_argument("--interval", type=int, default=3600)
    run.add_argument("--stake", type=Decimal, default=Decimal("1.00"))
    status = subcommands.add_parser("paper-status", help="show paper learning results")
    status.add_argument("--stake", type=Decimal, default=Decimal("1.00"))

    arguments = parser.parse_args()
    bot = _paper_bot(None, arguments.stake)
    if arguments.command == "paper-scan":
        reconciliation = bot.reconcile_once()
        summary = bot.scan_once()
        print(
            f"paper_scan discovered={summary.discovered} new_paper_trades={summary.paper_trades} "
            f"settled={reconciliation.settled} positive_outcomes={reconciliation.positive_outcomes} "
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
            f"hypothetical_pnl={stats['hypothetical_pnl']}"
        )


if __name__ == "__main__":
    main()
