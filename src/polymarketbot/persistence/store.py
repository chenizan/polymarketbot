"""SQLite persistence for signals, orders, fills, and PnL snapshots."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from polymarketbot.models import Fill, Order, PortfolioSnapshot, Signal, utcnow

class Store:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id TEXT PRIMARY KEY,
                strategy TEXT,
                market_condition_id TEXT,
                token_id TEXT,
                side TEXT,
                price REAL,
                size REAL,
                confidence REAL,
                reason TEXT,
                created_at TEXT,
                payload TEXT
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                client_order_id TEXT,
                exchange_order_id TEXT,
                signal_id TEXT,
                strategy TEXT,
                market_condition_id TEXT,
                token_id TEXT,
                side TEXT,
                price REAL,
                size REAL,
                filled_size REAL,
                status TEXT,
                paper INTEGER,
                created_at TEXT,
                updated_at TEXT,
                payload TEXT
            );
            CREATE TABLE IF NOT EXISTS fills (
                id TEXT PRIMARY KEY,
                order_id TEXT,
                token_id TEXT,
                side TEXT,
                price REAL,
                size REAL,
                fee REAL,
                paper INTEGER,
                strategy TEXT,
                market_condition_id TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT,
                message TEXT,
                created_at TEXT,
                payload TEXT
            );
            CREATE TABLE IF NOT EXISTS pnl_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cash REAL,
                equity REAL,
                unrealized_pnl REAL,
                realized_pnl REAL,
                daily_pnl REAL,
                exposure REAL,
                open_markets INTEGER,
                created_at TEXT,
                payload TEXT
            );
            """
        )
        self._conn.commit()

    def save_signal(self, signal: Signal) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO signals
            (id, strategy, market_condition_id, token_id, side, price, size,
             confidence, reason, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.id,
                signal.strategy,
                signal.market_condition_id,
                signal.token_id,
                signal.side.value,
                signal.price,
                signal.size,
                signal.confidence,
