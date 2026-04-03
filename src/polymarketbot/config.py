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
    signature_type: int = 0
    funder_address: str = ""

    clob_api_key: str = ""
    clob_api_secret: str = ""
    clob_api_passphrase: str = ""

    live_trading: bool = False
    kill_switch: bool = False

    clob_host: str = "https://clob.polymarket.com"
    chain_id: int = 137
    gamma_host: str = "https://gamma-api.polymarket.com"
    data_api_host: str = "https://data-api.polymarket.com"
    ws_market_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    ws_user_url: str = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    config_path: str = "config/default.yaml"
    db_path: str = "data/bot.db"
    log_dir: str = "logs"
    kill_switch_file: str = "data/KILL"

    max_order_size: float | None = None
    max_position_per_market: float | None = None
    max_total_exposure: float | None = None
    max_daily_loss: float | None = None

    paper_starting_cash: float = 10_000.0

class AppConfig(BaseModel):
    settings: Settings
    yaml: YamlConfig

    @property
    def paper(self) -> bool:
        return not self.settings.live_trading

    @property
    def risk(self) -> RiskConfig:
        r = self.yaml.risk.model_copy(deep=True)
        s = self.settings
        if s.max_order_size is not None:
            r.max_order_size = s.max_order_size
        if s.max_position_per_market is not None:
            r.max_position_per_market = s.max_position_per_market
        if s.max_total_exposure is not None:
            r.max_total_exposure = s.max_total_exposure
        if s.max_daily_loss is not None:
            r.max_daily_loss = s.max_daily_loss
        return r

    def enabled_strategies(self, override: list[str] | None = None) -> list[str]:
        if override:
            return override
        sc = self.yaml.strategies
        return ["binary_arb"] if sc.binary_arb.enabled else []

def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config YAML must be a mapping: {path}")
    return data
def load_config(
    config_path: str | Path | None = None,
    env_file: str | Path | None = None,
) -> AppConfig:
    """Load settings from env and strategy/risk defaults from YAML."""
    kwargs: dict[str, Any] = {}
    if env_file:
        kwargs["_env_file"] = str(env_file)
    settings = Settings(**kwargs) if kwargs else Settings()

    path = Path(config_path or settings.config_path)
    if not path.is_absolute():
        # Prefer CWD, then repo root
        if not path.exists():
            alt = ROOT / path
            if alt.exists():
                path = alt
    yaml_cfg = YamlConfig.model_validate(_load_yaml(path))
    return AppConfig(settings=settings, yaml=yaml_cfg)

def resolve_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    cwd = Path.cwd() / p
    if cwd.exists() or os.getenv("POLYMARKETBOT_FORCE_CWD"):
        return cwd
    return ROOT / p

