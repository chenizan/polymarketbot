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
                signal.reason,
                signal.created_at.isoformat(),
                signal.model_dump_json(),
            ),
        )
        self._conn.commit()

    def save_order(self, order: Order) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO orders
            (id, client_order_id, exchange_order_id, signal_id, strategy,
             market_condition_id, token_id, side, price, size, filled_size,
             status, paper, created_at, updated_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order.id,
                order.client_order_id,
                order.exchange_order_id,
                order.signal_id,
                order.strategy,
                order.market_condition_id,
                order.token_id,
                order.side.value,
                order.price,
                order.size,
                order.filled_size,
                order.status.value,
                1 if order.paper else 0,
                order.created_at.isoformat(),
                order.updated_at.isoformat(),
                order.model_dump_json(),
            ),
        )
        self._conn.commit()

    def save_fill(self, fill: Fill) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO fills
            (id, order_id, token_id, side, price, size, fee, paper,
             strategy, market_condition_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fill.id,
                fill.order_id,
                fill.token_id,
                fill.side.value,
                fill.price,
                fill.size,
                fill.fee,
                1 if fill.paper else 0,
                fill.strategy,
                fill.market_condition_id,
                fill.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    def log_event(self, kind: str, message: str, payload: dict[str, Any] | None = None) -> None:
        self._conn.execute(
            """
            INSERT INTO events (kind, message, created_at, payload)
            VALUES (?, ?, ?, ?)
            """,
            (kind, message, utcnow().isoformat(), json.dumps(payload or {})),
        )
        self._conn.commit()

    def save_pnl(self, snap: PortfolioSnapshot) -> None:
        self._conn.execute(
            """
            INSERT INTO pnl_snapshots
            (cash, equity, unrealized_pnl, realized_pnl, daily_pnl,
             exposure, open_markets, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snap.cash,
                snap.equity,
                snap.unrealized_pnl,
                snap.realized_pnl,
                snap.daily_pnl,
                snap.exposure,
                snap.open_markets,
                snap.updated_at.isoformat(),
                snap.model_dump_json(),
            ),
        )
        self._conn.commit()

    def recent_fills(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM fills ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def open_orders(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM orders WHERE status IN ('OPEN', 'PARTIAL', 'PENDING', 'PAPER')"
        ).fetchall()
        return [dict(r) for r in rows]

    def latest_pnl(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM pnl_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

