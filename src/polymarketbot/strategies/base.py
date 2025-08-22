"""Strategy base class."""

from __future__ import annotations

from abc import ABC, abstractmethod

from polymarketbot.engine.portfolio import Portfolio
from polymarketbot.models import MarketState, Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, state: MarketState, portfolio: Portfolio) -> list[Signal]:
        """Return zero or more trade signals for the given market state."""

    def on_start(self) -> None:
        return None

    def on_stop(self) -> None:
        return None

