"""WebSocket clients for market and user channels."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from polymarketbot.models import BookLevel, OrderBook, utcnow

logger = logging.getLogger("polymarketbot.ws")

MessageHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


def _parse_levels(raw: Any, *, bids: bool) -> list[BookLevel]:
    levels: list[BookLevel] = []
    if not raw:
        return levels
    for item in raw:
        if isinstance(item, dict):
            try:
                levels.append(BookLevel(price=float(item["price"]), size=float(item["size"])))
            except (KeyError, TypeError, ValueError):
                continue
    reverse = bids
    return sorted(levels, key=lambda x: x.price, reverse=reverse)


class MarketWebsocket:
    """Public market channel with auto-reconnect and application-level PING."""

    def __init__(
        self,
        url: str,
        asset_ids: list[str],
        on_message: MessageHandler,
        *,
        ping_interval_sec: float = 10.0,
        reconnect_delay_sec: float = 3.0,
        custom_feature_enabled: bool = True,
    ):
        self.url = url
        self.asset_ids = list(asset_ids)
        self.on_message = on_message
        self.ping_interval_sec = ping_interval_sec
        self.reconnect_delay_sec = reconnect_delay_sec
        self.custom_feature_enabled = custom_feature_enabled
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._books: dict[str, OrderBook] = {}

    @property
    def books(self) -> dict[str, OrderBook]:
        return self._books

    def update_assets(self, asset_ids: list[str]) -> None:
        self.asset_ids = list(asset_ids)

    def get_book(self, token_id: str) -> OrderBook | None:
        return self._books.get(token_id)

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name="market-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Market WS disconnected: %s", exc)
            if self._stop.is_set():
                break
            await asyncio.sleep(self.reconnect_delay_sec)

    async def _session(self) -> None:
        if not self.asset_ids:
            logger.warning("Market WS started with no asset IDs")
            await asyncio.sleep(self.reconnect_delay_sec)
            return

        async with websockets.connect(self.url, ping_interval=None) as ws:
            sub = {
                "assets_ids": self.asset_ids,
                "type": "market",
                "custom_feature_enabled": self.custom_feature_enabled,
            }
            await ws.send(json.dumps(sub))
            logger.info("Subscribed to %d market assets", len(self.asset_ids))

            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    await self._handle_raw(raw)
            finally:
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

    async def _ping_loop(self, ws: Any) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.ping_interval_sec)
            try:
                await ws.send("PING")
            except ConnectionClosed:
                return

    async def _handle_raw(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if raw == "PONG":
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        messages = msg if isinstance(msg, list) else [msg]
        for item in messages:
            if not isinstance(item, dict):
                continue
            self._update_local_book(item)
            result = self.on_message(item)
            if asyncio.iscoroutine(result):
                await result

    def _update_local_book(self, msg: dict[str, Any]) -> None:
        event_type = msg.get("event_type") or msg.get("type")
        asset_id = str(msg.get("asset_id") or msg.get("assetId") or "")
        if not asset_id:
            return

        if event_type in {"book", "orderbook"}:
            bids = _parse_levels(msg.get("bids") or msg.get("buys"), bids=True)
            asks = _parse_levels(msg.get("asks") or msg.get("sells"), bids=False)
            self._books[asset_id] = OrderBook(
                token_id=asset_id,
                bids=bids,
                asks=asks,
                timestamp=utcnow(),
            )
            return

        if event_type in {"price_change", "best_bid_ask"}:
            book = self._books.get(asset_id)
            if book is None:
                book = OrderBook(token_id=asset_id, bids=[], asks=[], timestamp=utcnow())
                self._books[asset_id] = book
            best_bid = msg.get("best_bid") or msg.get("bid")
            best_ask = msg.get("best_ask") or msg.get("ask")
            try:
                if best_bid is not None:
                    size = float(msg.get("best_bid_size") or msg.get("bid_size") or 0.0)
                    book.bids = [BookLevel(price=float(best_bid), size=size)]
                if best_ask is not None:
                    size = float(msg.get("best_ask_size") or msg.get("ask_size") or 0.0)
                    book.asks = [BookLevel(price=float(best_ask), size=size)]
                book.timestamp = utcnow()
            except (TypeError, ValueError):
                return


class UserWebsocket:
    """Authenticated user channel for order/trade updates (live mode)."""

    def __init__(
        self,
        url: str,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        on_message: MessageHandler,
        *,
        condition_ids: list[str] | None = None,
        ping_interval_sec: float = 10.0,
        reconnect_delay_sec: float = 3.0,
    ):
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.on_message = on_message
        self.condition_ids = list(condition_ids or [])
        self.ping_interval_sec = ping_interval_sec
        self.reconnect_delay_sec = reconnect_delay_sec
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run_forever(), name="user-ws")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("User WS disconnected: %s", exc)
            if self._stop.is_set():
                break
            await asyncio.sleep(self.reconnect_delay_sec)

    async def _session(self) -> None:
        async with websockets.connect(self.url, ping_interval=None) as ws:
            sub = {
                "type": "user",
                "auth": {
                    "apiKey": self.api_key,
                    "secret": self.api_secret,
                    "passphrase": self.api_passphrase,
                },
                "markets": self.condition_ids,
            }
            await ws.send(json.dumps(sub))
            logger.info("Subscribed to user channel (%d markets)", len(self.condition_ids))

            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    if self._stop.is_set():
                        break
                    await self._handle_raw(raw)
            finally:
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

    async def _ping_loop(self, ws: Any) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self.ping_interval_sec)
            try:
                await ws.send("PING")
            except ConnectionClosed:
                return

    async def _handle_raw(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="ignore")
        if raw == "PONG":
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        messages = msg if isinstance(msg, list) else [msg]
        for item in messages:
            if not isinstance(item, dict):
                continue
            result = self.on_message(item)
            if asyncio.iscoroutine(result):
                await result
