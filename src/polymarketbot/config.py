"""Configuration loaded from environment variables and YAML."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

REFERENCE_GAMMA_MARKETS = (
    "https://gamma-api.polymarket.com/markets?limit=20&active=true&closed=false"
)
REFERENCE_DATA_API_HEALTH = "https://data-api.polymarket.com/health"
REFERENCE_SYMBOL_CATALOG = "https://api.stockslab.xyz/symbols/updown"
DEFAULT_REFERENCE_FEEDS: tuple[str, ...] = (
    REFERENCE_GAMMA_MARKETS,
    REFERENCE_DATA_API_HEALTH,
    REFERENCE_SYMBOL_CATALOG,
)

class MarketFilters(BaseModel):
    # Short up/down windows are thin; don't over-filter
    min_liquidity: float = 200.0
    min_volume_24h: float = 0.0
    active_only: bool = True
    closed: bool = False
    tags: list[str] = Field(default_factory=list)
    query: str | None = None
    queries: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    token_ids: list[str] = Field(default_factory=list)
    condition_ids: list[str] = Field(default_factory=list)
    # Deterministic slug discovery: {asset}-updown-{5m|15m|4h}-{unix}
    updown_enabled: bool = True
    updown_assets: list[str] = Field(
        default_factory=lambda: ["btc", "eth", "sol", "xrp", "doge", "bnb"]
    )
    updown_windows: list[str] = Field(default_factory=lambda: ["5m"])
    updown_past: int = 0
    updown_future: int = 1

class RiskConfig(BaseModel):
    max_order_size: float = 50.0
    max_position_per_market: float = 200.0
    max_open_markets: int = 10
    max_total_exposure: float = 1000.0
    max_daily_loss: float = 100.0
    max_drawdown: float = 250.0
    min_edge: float = 0.01
    max_spread: float = 0.08
    error_cooldown_sec: float = 30.0
    max_consecutive_errors: int = 5

class BinaryArbConfig(BaseModel):
    enabled: bool = True
    min_edge: float = 0.015
    fee_buffer: float = 0.01
    size: float = 25.0

class StrategiesConfig(BaseModel):
    binary_arb: BinaryArbConfig = Field(default_factory=BinaryArbConfig)

class WebsocketConfig(BaseModel):
    ping_interval_sec: float = 10.0
    reconnect_delay_sec: float = 3.0
    custom_feature_enabled: bool = True

class NotifyConfig(BaseModel):
    on_fill: bool = True
    on_risk_halt: bool = True
    on_kill: bool = True

class YamlConfig(BaseModel):
    loop_interval_sec: float = 2.0
    max_markets: int = 40
    # Re-resolve rolling 5m/15m windows this often
    market_refresh_sec: float = 15.0
    market_filters: MarketFilters = Field(default_factory=MarketFilters)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategies: StrategiesConfig = Field(default_factory=StrategiesConfig)
    websocket: WebsocketConfig = Field(default_factory=WebsocketConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    private_key: str = ""
