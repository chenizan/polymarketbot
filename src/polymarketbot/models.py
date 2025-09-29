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
    tags: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class MarketState(BaseModel):
    market: Market
    yes_book: OrderBook | None = None
    no_book: OrderBook | None = None
    yes_mid_history: list[float] = Field(default_factory=list)
    no_mid_history: list[float] = Field(default_factory=list)
    last_trade_price_yes: float | None = None
    last_trade_price_no: float | None = None
    updated_at: datetime = Field(default_factory=utcnow)


class Signal(BaseModel):
    id: str = Field(default_factory=new_id)
    strategy: str
    market_condition_id: str
    token_id: str
    side: Side
    price: float
    size: float
    confidence: float = 1.0
    reason: str = ""
    order_type: OrderType = OrderType.GTC
    tick_size: str = "0.01"
    neg_risk: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Order(BaseModel):
    id: str = Field(default_factory=new_id)
    client_order_id: str = Field(default_factory=new_id)
    exchange_order_id: str | None = None
    signal_id: str | None = None
    strategy: str = ""
    market_condition_id: str = ""
    token_id: str
    side: Side
    price: float
    size: float
    filled_size: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    order_type: OrderType = OrderType.GTC
    paper: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)


class Fill(BaseModel):
    id: str = Field(default_factory=new_id)
    order_id: str
    token_id: str
    side: Side
    price: float
    size: float
    fee: float = 0.0
    paper: bool = True
    strategy: str = ""
    market_condition_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Position(BaseModel):
    token_id: str
    market_condition_id: str = ""
    size: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def notional(self) -> float:
        return abs(self.size) * self.avg_price


class PortfolioSnapshot(BaseModel):
    cash: float = 0.0
    equity: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    exposure: float = 0.0
    open_markets: int = 0
    positions: dict[str, Position] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utcnow)
