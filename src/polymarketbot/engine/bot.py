"""Main trading bot loop."""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Any

from polymarketbot.clients.clob import ClobService
from polymarketbot.clients.data_api import DataApiClient
from polymarketbot.clients.gamma import GammaClient
from polymarketbot.clients.websocket import MarketWebsocket, UserWebsocket
from polymarketbot.config import AppConfig
from polymarketbot.engine.executor import Executor
from polymarketbot.engine.portfolio import Portfolio
from polymarketbot.engine.risk import RiskManager
from polymarketbot.models import Market, MarketState, OrderBook
from polymarketbot.notify.telegram import TelegramNotifier
from polymarketbot.persistence.store import Store
from polymarketbot.strategies.base import Strategy
from polymarketbot.strategies.registry import build_strategies

logger = logging.getLogger("polymarketbot.bot")


class TradingBot:
    def __init__(
        self,
        config: AppConfig,
        strategies: list[Strategy] | None = None,
        strategy_names: list[str] | None = None,
    ):
        self.config = config
        self.settings = config.settings
        self.strategies = strategies or build_strategies(config, strategy_names)
        self.store = Store(self.settings.db_path)
        self.portfolio = Portfolio(starting_cash=self.settings.paper_starting_cash)
        self.risk = RiskManager(
            config.risk,
            kill_switch_env=self.settings.kill_switch,
            kill_switch_file=self.settings.kill_switch_file,
        )
        self.gamma = GammaClient(self.settings.gamma_host)
        self.data_api = DataApiClient(self.settings.data_api_host)
        self.clob = ClobService(
            host=self.settings.clob_host,
            chain_id=self.settings.chain_id,
            private_key=self.settings.private_key,
            api_key=self.settings.clob_api_key,
            api_secret=self.settings.clob_api_secret,
            api_passphrase=self.settings.clob_api_passphrase,
            signature_type=self.settings.signature_type,
            funder=self.settings.funder_address,
        )
        self.executor = Executor(
            paper=config.paper,
            portfolio=self.portfolio,
            store=self.store,
            clob=self.clob if not config.paper else None,
        )
        self.notifier = TelegramNotifier(
            token=self.settings.telegram_bot_token,
            chat_id=self.settings.telegram_chat_id,
            enabled=bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id),
        )
        self.markets: list[Market] = []
        self.states: dict[str, MarketState] = {}
        self._market_ws: MarketWebsocket | None = None
        self._user_ws: UserWebsocket | None = None
        self._stop = asyncio.Event()
        self._history_limit = 200
        self._last_market_refresh = 0.0

    async def run(self) -> None:
        mode = "PAPER" if self.config.paper else "LIVE"
        names = [s.name for s in self.strategies]
        logger.info("Starting bot mode=%s strategies=%s", mode, names)
        if not self.config.paper:
            logger.warning("LIVE TRADING ENABLED, real funds at risk")

        self._install_signal_handlers()
        self.clob.connect(require_auth=not self.config.paper)

        await self._refresh_markets(force=True)
        await self._start_websockets()

        for strategy in self.strategies:
            strategy.on_start()

        try:
            while not self._stop.is_set():
                await self._refresh_markets(force=False)
                await self._tick()
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self.config.yaml.loop_interval_sec,
                    )
                except TimeoutError:
                    pass
        finally:
            await self.shutdown()


    async def shutdown(self) -> None:
        logger.info("Shutting down…")
        self._stop.set()
        for strategy in self.strategies:
            strategy.on_stop()
        if self._market_ws:
            await self._market_ws.stop()
        if self._user_ws:
            await self._user_ws.stop()
        try:
            self.executor.cancel_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("cancel_all on shutdown failed: %s", exc)
        snap = self.portfolio.mark_snapshot(self.states)
        self.store.save_pnl(snap)
        self.store.log_event("shutdown", f"equity={snap.equity:.2f}")
        self.gamma.close()
        self.data_api.close()
        try:
            self.clob.close()
        except Exception:  # noqa: BLE001
            pass
        self.store.close()
        logger.info("Shutdown complete equity=%.2f daily_pnl=%.2f", snap.equity, snap.daily_pnl)

    def request_stop(self) -> None:
        self._stop.set()

    async def _refresh_markets(self, *, force: bool = False) -> None:
        import time

        interval = float(getattr(self.config.yaml, "market_refresh_sec", 30.0) or 30.0)
        now = time.time()
        if not force and (now - self._last_market_refresh) < interval:
            return
        self._last_market_refresh = now

        old_live_ids = {m.condition_id for m in self.markets}
        found = await asyncio.to_thread(
            self.gamma.discover,
            self.config.yaml.market_filters,
            self.config.yaml.max_markets,
        )
        if not found:
            if self.markets:
                logger.warning("No up/down markets discovered this cycle")
            self.markets = []
            self.states = {}
            return

        found_ids = {m.condition_id for m in found}
        self.states = {
            cid: state for cid, state in self.states.items() if cid in found_ids
        }
        self.markets = found
        for m in self.markets:
            if m.condition_id not in self.states:
                self.states[m.condition_id] = MarketState(market=m)
            else:
                self.states[m.condition_id].market = m
            self.portfolio.register_market_token(m.yes_token_id, m.condition_id)
            self.portfolio.register_market_token(m.no_token_id, m.condition_id)

        await self._bootstrap_books()
        live_ids = {m.condition_id for m in self.markets}
        if not live_ids:
            logger.warning("No up/down markets with live books right now")
            return

        if force or live_ids != old_live_ids:
            added = live_ids - old_live_ids
            removed = old_live_ids - live_ids
            logger.info(
                "Tracking %d live 5m books (+%d / -%d)",
                len(self.markets),
                len(added),
                len(removed),
            )
            if force or added:
                for m in self.markets[:8]:
                    logger.info("  %s", m.question[:80])
            await self._resubscribe_market_ws()

    async def _resubscribe_market_ws(self) -> None:
        asset_ids: list[str] = []
        for m in self.markets:
            asset_ids.extend([m.yes_token_id, m.no_token_id])
        if self._market_ws is None:
            return
        if set(asset_ids) == set(self._market_ws.asset_ids):
            return
        self._market_ws.update_assets(asset_ids)
        # Force reconnect so the new asset list is subscribed
        await self._market_ws.stop()
        await self._market_ws.start()

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()

        def _handler() -> None:
            logger.info("Received stop signal")
            self._stop.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _handler)
            except NotImplementedError:
                # Windows
                signal.signal(sig, lambda *_: self._stop.set())

    async def _bootstrap_books(self) -> None:
        keep: list[Market] = []
        dropped = 0
        for market in self.markets:
            state = self.states.get(market.condition_id)
            if state is None:
                continue
            # Reuse books we already have for overlapping windows
            if state.yes_book is not None and state.no_book is not None:
                keep.append(market)
                continue
            try:
                yes_book = await asyncio.to_thread(
                    self.clob.get_order_book, market.yes_token_id
                )
                no_book = await asyncio.to_thread(
                    self.clob.get_order_book, market.no_token_id
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Book bootstrap failed for %s: %s", market.question[:50], exc
                )
                yes_book = None
                no_book = None

            if yes_book is None or no_book is None:
                dropped += 1
                logger.debug(
                    "Skipping %s (no live CLOB book yet)", market.question[:60]
                )
                self.states.pop(market.condition_id, None)
                continue

            state.yes_book = yes_book
            state.no_book = no_book
            self._append_history(state)
            keep.append(market)

        self.markets = keep
        if dropped:
            logger.debug("Skipped %d markets without live order books", dropped)

    async def _start_websockets(self) -> None:
        asset_ids: list[str] = []
        for m in self.markets:
            asset_ids.extend([m.yes_token_id, m.no_token_id])

        if asset_ids:
            self._market_ws = MarketWebsocket(
                url=self.settings.ws_market_url,
                asset_ids=asset_ids,
                on_message=self._on_market_msg,
                ping_interval_sec=self.config.yaml.websocket.ping_interval_sec,
                reconnect_delay_sec=self.config.yaml.websocket.reconnect_delay_sec,
                custom_feature_enabled=self.config.yaml.websocket.custom_feature_enabled,
            )
            await self._market_ws.start()

        if (
            not self.config.paper
            and self.settings.clob_api_key
            and self.settings.clob_api_secret
            and self.settings.clob_api_passphrase
        ):
            self._user_ws = UserWebsocket(
                url=self.settings.ws_user_url,
                api_key=self.settings.clob_api_key,
                api_secret=self.settings.clob_api_secret,
                api_passphrase=self.settings.clob_api_passphrase,
                on_message=self._on_user_msg,
                condition_ids=[m.condition_id for m in self.markets],
                ping_interval_sec=self.config.yaml.websocket.ping_interval_sec,
                reconnect_delay_sec=self.config.yaml.websocket.reconnect_delay_sec,
            )
            await self._user_ws.start()

    async def _on_market_msg(self, msg: dict[str, Any]) -> None:
        asset_id = str(msg.get("asset_id") or msg.get("assetId") or "")
        event_type = msg.get("event_type") or msg.get("type")
        if event_type == "last_trade_price" and asset_id:
            try:
                price = float(msg.get("price"))
            except (TypeError, ValueError):
                return
            for state in self.states.values():
                if asset_id == state.market.yes_token_id:
                    state.last_trade_price_yes = price
                elif asset_id == state.market.no_token_id:
                    state.last_trade_price_no = price

        if self._market_ws and asset_id:
            book = self._market_ws.get_book(asset_id)
            if book:
                self._apply_book(asset_id, book)

    async def _on_user_msg(self, msg: dict[str, Any]) -> None:
        fill = self.executor.handle_user_event(msg)
        if fill and self.config.yaml.notify.on_fill:
            await self.notifier.send(
                f"Fill {fill.side.value} {fill.size:.2f} @ {fill.price:.4f} ({fill.token_id[:10]}…)"
            )

    def _apply_book(self, token_id: str, book: OrderBook) -> None:
        for state in self.states.values():
            if token_id == state.market.yes_token_id:
                state.yes_book = book
                self._append_history(state)
                return
            if token_id == state.market.no_token_id:
                state.no_book = book
                self._append_history(state)
                return

    def _append_history(self, state: MarketState) -> None:
        if state.yes_book and state.yes_book.mid is not None:
            state.yes_mid_history.append(state.yes_book.mid)
            state.yes_mid_history = state.yes_mid_history[-self._history_limit :]
        if state.no_book and state.no_book.mid is not None:
            state.no_mid_history.append(state.no_book.mid)
            state.no_mid_history = state.no_mid_history[-self._history_limit :]

    async def _tick(self) -> None:
        # Refresh books from WS cache or REST fallback
        if self._market_ws:
            for market in self.markets:
                yes = self._market_ws.get_book(market.yes_token_id)
                no = self._market_ws.get_book(market.no_token_id)
                state = self.states[market.condition_id]
                if yes:
                    state.yes_book = yes
                if no:
                    state.no_book = no
                if yes or no:
                    self._append_history(state)

        # REST fallback for missing books
        still: list[Market] = []
        for market in self.markets:
            state = self.states.get(market.condition_id)
            if state is None:
                continue
            if state.yes_book is None or state.no_book is None:
                try:
                    if state.yes_book is None:
                        state.yes_book = await asyncio.to_thread(
                            self.clob.get_order_book, market.yes_token_id
                        )
                    if state.no_book is None:
                        state.no_book = await asyncio.to_thread(
                            self.clob.get_order_book, market.no_token_id
                        )
                    if state.yes_book is None or state.no_book is None:
                        continue
                    self._append_history(state)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("REST book refresh failed: %s", exc)
                    continue
            still.append(market)
        self.markets = still
        self.states = {m.condition_id: self.states[m.condition_id] for m in still}

        self.risk.update_halts(self.portfolio, self.states)
        if self.risk.halted:
            logger.warning("Risk halt: %s", self.risk.halt_reason)
            if self.config.yaml.notify.on_risk_halt:
                await self.notifier.send(f"Risk halt: {self.risk.halt_reason}")
            if self.risk.kill_active() and self.config.yaml.notify.on_kill:
                await self.notifier.send("Kill switch active, trading stopped")
            return

        for state in self.states.values():
            for strategy in self.strategies:
                try:
                    signals = strategy.evaluate(state, self.portfolio)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Strategy %s error: %s", strategy.name, exc)
                    self.risk.record_error()
                    continue

                for signal_obj in signals:
                    self.store.save_signal(signal_obj)
                    decision = self.risk.evaluate(signal_obj, self.portfolio, state)
                    if not decision.allowed or decision.adjusted is None:
                        logger.debug(
                            "Risk blocked %s: %s", signal_obj.strategy, decision.reason
                        )
                        continue
                    try:
                        order = self.executor.execute(decision.adjusted, state)
                        self.risk.record_success()
                        if order and self.config.yaml.notify.on_fill:
                            await self.notifier.send(
                                f"{'PAPER' if order.paper else 'LIVE'} "
                                f"{order.side.value} {order.size:.2f} @ {order.price:.4f} "
                                f"[{order.strategy}]"
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Execution error: %s", exc)
                        self.risk.record_error()

        snap = self.portfolio.mark_snapshot(self.states)
        self.store.save_pnl(snap)
        logger.info(
            "Tick equity=%.2f daily_pnl=%.2f exposure=%.2f positions=%d",
            snap.equity,
            snap.daily_pnl,
            snap.exposure,
            len(snap.positions),
        )


async def run_bot(config: AppConfig, strategy_names: list[str] | None = None) -> None:
    bot = TradingBot(config, strategy_names=strategy_names)
    await bot.run()

