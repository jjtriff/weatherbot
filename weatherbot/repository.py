from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from weatherbot.domain import MarketQuote


class PaperRepository:
    """SQLite-backed, idempotent paper-trading and learning ledger."""

    def __init__(self, database_path: Path) -> None:
        self._database_path = database_path
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_trades (
                    market_slug TEXT PRIMARY KEY,
                    market_title TEXT NOT NULL,
                    entry_price TEXT NOT NULL,
                    cost TEXT NOT NULL,
                    shares TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('open', 'settled')),
                    settlement INTEGER,
                    pnl TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    settled_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS market_analyses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    event_title TEXT NOT NULL,
                    market_slug TEXT NOT NULL,
                    market_title TEXT NOT NULL,
                    city_slug TEXT,
                    target_date TEXT,
                    settlement_time TEXT,
                    hours_to_settlement TEXT,
                    forecast_value TEXT,
                    forecast_source TEXT,
                    probability TEXT,
                    best_bid TEXT,
                    best_ask TEXT,
                    volume TEXT,
                    quote_source TEXT NOT NULL,
                    decision_reason TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS market_analyses_scan_id ON market_analyses(scan_id, id)"
            )

    def create_trade(
        self,
        *,
        market_slug: str,
        market_title: str,
        entry_price: Decimal,
        cost: Decimal,
        shares: Decimal,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO paper_trades
                (market_slug, market_title, entry_price, cost, shares, status)
                VALUES (?, ?, ?, ?, ?, 'open')
                """,
                (market_slug, market_title, str(entry_price), str(cost), str(shares)),
            )
            return cursor.rowcount == 1

    def get_trade(self, market_slug: str) -> Mapping[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM paper_trades WHERE market_slug = ?", (market_slug,)
            ).fetchone()
        if row is None:
            raise KeyError(market_slug)
        return dict(row)

    def open_trades(self) -> list[Mapping[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM paper_trades WHERE status = 'open' ORDER BY created_at, market_slug"
            ).fetchall()
        return [dict(row) for row in rows]

    def count_open_trades(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM paper_trades WHERE status = 'open'"
            ).fetchone()
        assert row is not None
        return int(row["count"])

    def settle_trade(self, market_slug: str, settlement: int) -> Decimal | None:
        if settlement not in {0, 1}:
            raise ValueError("settlement must be 0 or 1")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT entry_price, cost, shares FROM paper_trades WHERE market_slug = ? AND status = 'open'",
                (market_slug,),
            ).fetchone()
            if row is None:
                return None
            price = Decimal(str(row["entry_price"]))
            cost = Decimal(str(row["cost"]))
            shares = Decimal(str(row["shares"]))
            pnl = (shares * (Decimal(1) - price)) if settlement == 1 else -cost
            connection.execute(
                """
                UPDATE paper_trades
                SET status = 'settled', settlement = ?, pnl = ?, settled_at = CURRENT_TIMESTAMP
                WHERE market_slug = ? AND status = 'open'
                """,
                (settlement, str(pnl), market_slug),
            )
            return pnl

    def learning_stats(self) -> dict[str, int | Decimal]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT settlement, pnl FROM paper_trades WHERE status = 'settled'"
            ).fetchall()
        settled_trades = len(rows)
        positive_outcomes = sum(1 for row in rows if int(row["settlement"]) == 1)
        hypothetical_pnl = sum((Decimal(str(row["pnl"])) for row in rows), Decimal(0))
        positive_outcome_rate = (
            Decimal(positive_outcomes) / Decimal(settled_trades) if settled_trades else Decimal(0)
        )
        return {
            "settled_trades": settled_trades,
            "positive_outcomes": positive_outcomes,
            "positive_outcome_rate": positive_outcome_rate,
            "hypothetical_pnl": hypothetical_pnl,
        }

    def record_market_analysis(
        self,
        *,
        scan_id: str,
        scanned_at: str,
        event_title: str,
        market_slug: str,
        market_title: str,
        city_slug: str | None,
        target_date: str | None,
        settlement_time: str | None,
        hours_to_settlement: Decimal | None,
        forecast_value: Decimal | None,
        forecast_source: str | None,
        probability: Decimal | None,
        quote: MarketQuote | None,
        quote_source: str,
        decision_reason: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO market_analyses (
                    scan_id, scanned_at, event_title, market_slug, market_title,
                    city_slug, target_date, settlement_time, hours_to_settlement,
                    forecast_value, forecast_source, probability, best_bid, best_ask,
                    volume, quote_source, decision_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_id,
                    scanned_at,
                    event_title,
                    market_slug,
                    market_title,
                    city_slug,
                    target_date,
                    settlement_time,
                    str(hours_to_settlement) if hours_to_settlement is not None else None,
                    str(forecast_value) if forecast_value is not None else None,
                    forecast_source,
                    str(probability) if probability is not None else None,
                    str(quote.best_bid) if quote and quote.best_bid is not None else None,
                    str(quote.best_ask) if quote and quote.best_ask is not None else None,
                    str(quote.volume) if quote else None,
                    quote_source,
                    decision_reason,
                ),
            )

    def latest_market_analyses(self) -> list[Mapping[str, object]]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT scan_id FROM market_analyses ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return []
            rows = connection.execute(
                "SELECT * FROM market_analyses WHERE scan_id = ? ORDER BY id", (row["scan_id"],)
            ).fetchall()
        return [dict(row) for row in rows]
