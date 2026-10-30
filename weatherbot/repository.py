from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path


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
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS settled_trades,
                    COALESCE(SUM(CASE WHEN settlement = 1 THEN 1 ELSE 0 END), 0) AS positive_outcomes,
                    COALESCE(SUM(CAST(pnl AS REAL)), 0) AS pnl
                FROM paper_trades
                WHERE status = 'settled'
                """
            ).fetchone()
        assert row is not None
        return {
            "settled_trades": int(row["settled_trades"]),
            "positive_outcomes": int(row["positive_outcomes"]),
            "hypothetical_pnl": Decimal(str(row["pnl"])),
        }
