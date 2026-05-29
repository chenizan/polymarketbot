"""Binary outcome arbitrage on crypto (and other) YES/NO markets.

Buy YES+NO when ask sum is below 1 - fees - edge.
Sell YES+NO when bid sum is above 1 + fees + edge.
"""

from __future__ import annotations

from polymarketbot.config import BinaryArbConfig
from polymarketbot.engine.portfolio import Portfolio
from polymarketbot.models import MarketState, Side, Signal
from polymarketbot.strategies.base import Strategy
from polymarketbot.utils.math import round_to_tick


class BinaryArbStrategy(Strategy):
    name = "binary_arb"

    def __init__(self, config: BinaryArbConfig):
        self.config = config

    def evaluate(self, state: MarketState, portfolio: Portfolio) -> list[Signal]:
        yes = state.yes_book
        no = state.no_book
        if not yes or not no:
            return []

        size = self.config.size
        yes_pos = portfolio.position_size(state.market.yes_token_id)
        no_pos = portfolio.position_size(state.market.no_token_id)
        tick = state.market.tick_size
        signals: list[Signal] = []

        # Long arb: buy both when asks are cheap
        if yes.best_ask is not None and no.best_ask is not None:
            ask_sum = yes.best_ask + no.best_ask
            buy_threshold = 1.0 - self.config.fee_buffer - self.config.min_edge
            edge = 1.0 - ask_sum
            if (
                ask_sum < buy_threshold
                and yes_pos <= size * 3
                and no_pos <= size * 3
            ):
                confidence = min(1.0, max(0.5, edge / max(self.config.min_edge, 1e-6)))
                reason = f"buy_arb ask_sum={ask_sum:.4f} edge={edge:.4f}"
                signals.extend(
                    self._pair(
                        state,
                        side=Side.BUY,
                        yes_price=yes.best_ask,
                        no_price=no.best_ask,
                        size=size,
                        confidence=confidence,
                        reason=reason,
                        tick=tick,
                    )
                )

        # Short arb: sell both when bids are rich
        if yes.best_bid is not None and no.best_bid is not None:
            bid_sum = yes.best_bid + no.best_bid
            sell_threshold = 1.0 + self.config.fee_buffer + self.config.min_edge
            edge = bid_sum - 1.0
            if (
                bid_sum > sell_threshold
                and yes_pos >= -size * 3
                and no_pos >= -size * 3
            ):
                confidence = min(1.0, max(0.5, edge / max(self.config.min_edge, 1e-6)))
                reason = f"sell_arb bid_sum={bid_sum:.4f} edge={edge:.4f}"
                signals.extend(
                    self._pair(
                        state,
                        side=Side.SELL,
                        yes_price=yes.best_bid,
                        no_price=no.best_bid,
                        size=size,
                        confidence=confidence,
                        reason=reason,
                        tick=tick,
                    )
                )

        return signals

    def _pair(
        self,
        state: MarketState,
        *,
        side: Side,
        yes_price: float,
        no_price: float,
        size: float,
        confidence: float,
        reason: str,
        tick: str,
    ) -> list[Signal]:
        return [
            Signal(
                strategy=self.name,
                market_condition_id=state.market.condition_id,
                token_id=state.market.yes_token_id,
                side=side,
                price=round_to_tick(yes_price, tick),
                size=size,
                confidence=confidence,
                reason=reason,
                tick_size=tick,
                neg_risk=state.market.neg_risk,
            ),
            Signal(
                strategy=self.name,
                market_condition_id=state.market.condition_id,
                token_id=state.market.no_token_id,
                side=side,
                price=round_to_tick(no_price, tick),
                size=size,
                confidence=confidence,
                reason=reason,
                tick_size=tick,
                neg_risk=state.market.neg_risk,
            ),
        ]
