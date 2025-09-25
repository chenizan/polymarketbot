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
            return
        drawdown = snap.peak_equity - snap.equity
        if drawdown >= abs(self.config.max_drawdown):
            self.halted = True
            self.halt_reason = f"max drawdown halt ({drawdown:.2f})"
            return
        # Soft halt clear only when not a hard risk halt
        hard = self.halt_reason.startswith("daily") or "drawdown" in self.halt_reason
        if self.halted and not hard:
            self.halted = False
            self.halt_reason = ""

    def evaluate(
        self,
        signal: Signal,
        portfolio: Portfolio,
        state: MarketState | None = None,
    ) -> RiskDecision:
        if self.kill_active():
            return RiskDecision(False, "kill switch active")
        if self.halted:
            return RiskDecision(False, self.halt_reason or "trading halted")
        if time.time() < self.cooldown_until:
            return RiskDecision(False, "error cooldown active")

        size = min(signal.size, self.config.max_order_size)
        if size <= 0:
            return RiskDecision(False, "non-positive size")

        # Spread filter
        if state is not None:
            book = (
                state.yes_book
                if signal.token_id == state.market.yes_token_id
                else state.no_book
            )
            if book and book.spread is not None and book.spread > self.config.max_spread:
                return RiskDecision(False, f"spread too wide ({book.spread:.4f})")

        # Position / exposure caps
        current = abs(portfolio.position_size(signal.token_id))
        projected = current + size
        # Approximate position notional using signal price
        exposure = portfolio.market_exposure(signal.market_condition_id)
        market_notional = exposure + size * signal.price
        if market_notional > self.config.max_position_per_market:
            return RiskDecision(False, "max position per market exceeded")

        opening_new = (
            portfolio.position_size(signal.token_id) == 0
            and portfolio.open_market_count() >= self.config.max_open_markets
            and exposure <= 0
        )
        if opening_new:
            return RiskDecision(False, "max open markets exceeded")

        total = portfolio.total_exposure() + size * signal.price
        if total > self.config.max_total_exposure:
            return RiskDecision(False, "max total exposure exceeded")

        adjusted = signal.model_copy(update={"size": size})
        if projected > self.config.max_position_per_market / max(signal.price, 1e-6):
            # also cap by share count approx using position config / price
            max_shares = self.config.max_position_per_market / max(signal.price, 1e-6)
            remain = max(0.0, max_shares - current)
            if remain <= 0:
                return RiskDecision(False, "position share cap reached")
            adjusted = signal.model_copy(update={"size": min(size, remain)})

        return RiskDecision(True, "ok", adjusted)

