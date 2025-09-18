"""Order execution, paper broker and live CLOB path."""

from __future__ import annotations

import logging
from typing import Any

from polymarketbot.clients.clob import ClobService
from polymarketbot.engine.portfolio import Portfolio
from polymarketbot.models import (
    Fill,
    MarketState,
    Order,
    OrderStatus,
    Side,
    Signal,
    utcnow,
)
from polymarketbot.persistence.store import Store

logger = logging.getLogger("polymarketbot.executor")


class Executor:
    def __init__(
        self,
        *,
        paper: bool,
        portfolio: Portfolio,
        store: Store,
        clob: ClobService | None = None,
    ):
        self.paper = paper
        self.portfolio = portfolio
        self.store = store
        self.clob = clob
        self.open_orders: dict[str, Order] = {}

    def execute(self, signal: Signal, state: MarketState | None = None) -> Order | None:
        if self.paper:
            return self._execute_paper(signal, state)
        return self._execute_live(signal)

    def _execute_paper(self, signal: Signal, state: MarketState | None) -> Order | None:
        fill_price = self._paper_fill_price(signal, state)
        if fill_price is None:
            logger.debug("Paper skip, no book for %s", signal.token_id)
            return None

        order = Order(
            signal_id=signal.id,
            strategy=signal.strategy,
            market_condition_id=signal.market_condition_id,
            token_id=signal.token_id,
            side=signal.side,
            price=fill_price,
            size=signal.size,
            filled_size=signal.size,
            status=OrderStatus.PAPER,
            order_type=signal.order_type,
            paper=True,
        )
        fill = Fill(
            order_id=order.id,
            token_id=order.token_id,
            side=order.side,
            price=fill_price,
            size=order.size,
            paper=True,
            strategy=order.strategy,
            market_condition_id=order.market_condition_id,
        )
        self.portfolio.register_market_token(order.token_id, order.market_condition_id)
        self.portfolio.apply_fill(fill)
        self.store.save_order(order)
        self.store.save_fill(fill)
        logger.info(
            "PAPER %s %s size=%.4f @ %.4f (%s)",
            order.side.value,
            order.token_id[:12],
            order.size,
            fill_price,
            order.strategy,
        )
        return order

    def _paper_fill_price(self, signal: Signal, state: MarketState | None) -> float | None:
        """Conservative: buys fill at ask, sells at bid."""
        if state is None:
            return signal.price
        book = (
            state.yes_book
            if signal.token_id == state.market.yes_token_id
            else state.no_book
        )
        if book is None:
            return signal.price
        if signal.side == Side.BUY:
            return book.best_ask if book.best_ask is not None else signal.price
        return book.best_bid if book.best_bid is not None else signal.price

    def _execute_live(self, signal: Signal) -> Order | None:
        if self.clob is None:
            raise RuntimeError("Live trading requires an authenticated CLOB client")
        order = Order(
            signal_id=signal.id,
            strategy=signal.strategy,
            market_condition_id=signal.market_condition_id,
            token_id=signal.token_id,
            side=signal.side,
            price=signal.price,
            size=signal.size,
            status=OrderStatus.PENDING,
            order_type=signal.order_type,
            paper=False,
        )
        try:
            resp = self.clob.create_and_post_order(
                token_id=signal.token_id,
                price=signal.price,
                size=signal.size,
                side=signal.side,
                tick_size=signal.tick_size,
                neg_risk=signal.neg_risk,
                order_type=signal.order_type.value,
            )
            order.raw = resp if isinstance(resp, dict) else {"response": str(resp)}
            order.exchange_order_id = str(
                order.raw.get("orderID")
                or order.raw.get("order_id")
                or order.raw.get("id")
                or ""
            ) or None
            status = str(order.raw.get("status") or "OPEN").upper()
            if "LIVE" in status or status == "OPEN":
                order.status = OrderStatus.OPEN
            elif "MATCH" in status or "FILL" in status:
                order.status = OrderStatus.FILLED
                order.filled_size = order.size
            else:
                order.status = OrderStatus.OPEN
            order.updated_at = utcnow()
            self.open_orders[order.id] = order
            self.store.save_order(order)
            logger.info(
                "LIVE %s %s size=%.4f @ %.4f id=%s",
                order.side.value,
                order.token_id[:12],
                order.size,
                order.price,
                order.exchange_order_id,
            )
            return order
        except Exception as exc:  # noqa: BLE001
            order.status = OrderStatus.REJECTED
            order.raw = {"error": str(exc)}
            order.updated_at = utcnow()
            self.store.save_order(order)
            logger.error("Live order failed: %s", exc)
            raise

    def cancel_all(self) -> list[Any]:
        results: list[Any] = []
        if self.paper:
            for oid, order in list(self.open_orders.items()):
                order.status = OrderStatus.CANCELLED
                order.updated_at = utcnow()
                self.store.save_order(order)
                results.append(oid)
            self.open_orders.clear()
            return results
        if self.clob is None:
            return results
        try:
            results.append(self.clob.cancel_all())
        except Exception as exc:  # noqa: BLE001
            logger.error("cancel_all failed: %s", exc)
            raise
        for order in self.open_orders.values():
            order.status = OrderStatus.CANCELLED
            order.updated_at = utcnow()
            self.store.save_order(order)
        self.open_orders.clear()
        return results

    def handle_user_event(self, msg: dict[str, Any]) -> Fill | None:
        """Update local state from user websocket trade/order events."""
        event_type = msg.get("event_type") or msg.get("type")
        if event_type != "trade":
            return None
        status = str(msg.get("status") or "").upper()
        if status not in {"MATCHED", "MINED", "CONFIRMED"}:
            return None
        token_id = str(msg.get("asset_id") or msg.get("assetId") or "")
        side_raw = str(msg.get("side") or "BUY").upper()
        side = Side.BUY if side_raw == "BUY" else Side.SELL
        try:
            price = float(msg.get("price"))
            size = float(msg.get("size") or msg.get("matched_amount") or 0)
        except (TypeError, ValueError):
            return None
        if not token_id or size <= 0:
            return None
        fill = Fill(
            order_id=str(msg.get("taker_order_id") or msg.get("id") or ""),
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            paper=False,
            market_condition_id=str(msg.get("market") or ""),
        )
        self.portfolio.register_market_token(token_id, fill.market_condition_id)
        self.portfolio.apply_fill(fill)
        self.store.save_fill(fill)
        return fill

