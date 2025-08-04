"""Domain models for markets, signals, orders, and portfolio state."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    PAPER = "PAPER"


class OrderType(StrEnum):
    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


class BookLevel(BaseModel):
    price: float
    size: float


class OrderBook(BaseModel):
    token_id: str
    bids: list[BookLevel] = Field(default_factory=list)
    asks: list[BookLevel] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utcnow)

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return self.best_ask - self.best_bid


class Market(BaseModel):
    condition_id: str
    question: str
    slug: str | None = None
    yes_token_id: str
    no_token_id: str
    tick_size: str = "0.01"
    neg_risk: bool = False
    active: bool = True
    closed: bool = False
    accepting_orders: bool | None = None
    liquidity: float = 0.0
    volume_24h: float = 0.0
