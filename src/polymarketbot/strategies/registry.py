"""Strategy registry / factory."""

from __future__ import annotations

from polymarketbot.config import AppConfig, StrategiesConfig
from polymarketbot.strategies.base import Strategy
from polymarketbot.strategies.binary_arb import BinaryArbStrategy

def build_strategies(
    config: AppConfig | StrategiesConfig,
    enabled: list[str] | None = None,
) -> list[Strategy]:
    sc = config.yaml.strategies if isinstance(config, AppConfig) else config
    names = enabled
    if names is None and isinstance(config, AppConfig):
        names = config.enabled_strategies()
    if names is None:
        names = ["binary_arb"] if sc.binary_arb.enabled else []

    strategies: list[Strategy] = []
    for name in names:
        if name == "binary_arb":
            strategies.append(BinaryArbStrategy(sc.binary_arb))
        else:
            raise ValueError(f"Unknown strategy: {name}")
    return strategies
