"""Risk checks and kill switch."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from polymarketbot.config import RiskConfig
from polymarketbot.engine.portfolio import Portfolio
from polymarketbot.models import MarketState, Signal

logger = logging.getLogger("polymarketbot.risk")


class RiskDecision:
    def __init__(self, allowed: bool, reason: str = "", adjusted: Signal | None = None):
        self.allowed = allowed
        self.reason = reason
        self.adjusted = adjusted


class RiskManager:
    def __init__(
        self,
        config: RiskConfig,
        *,
        kill_switch_env: bool = False,
        kill_switch_file: str = "data/KILL",
    ):
        self.config = config
        self.kill_switch_env = kill_switch_env
        self.kill_switch_file = Path(kill_switch_file)
        self.halted = False
        self.halt_reason = ""
        self.consecutive_errors = 0
        self.cooldown_until = 0.0

    def kill_active(self) -> bool:
        if self.kill_switch_env:
            return True
        return self.kill_switch_file.exists()

    def record_error(self) -> None:
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.config.max_consecutive_errors:
            self.cooldown_until = time.time() + self.config.error_cooldown_sec
            logger.warning(
                "Error cooldown for %.0fs after %d failures",
                self.config.error_cooldown_sec,
                self.consecutive_errors,
            )

    def record_success(self) -> None:
        self.consecutive_errors = 0

    def update_halts(
        self,
        portfolio: Portfolio,
        states: dict[str, MarketState] | None = None,
    ) -> None:
        if self.kill_active():
            self.halted = True
            self.halt_reason = "kill switch active"
            return
        snap = portfolio.mark_snapshot(states)
        if snap.daily_pnl <= -abs(self.config.max_daily_loss):
            self.halted = True
            self.halt_reason = f"daily loss halt ({snap.daily_pnl:.2f})"
