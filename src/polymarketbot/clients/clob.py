"""CLOB client: public reads via polymarket-client, trading via py-clob-client-v2."""

from __future__ import annotations

import logging
from typing import Any

from polymarket import PublicClient

from polymarketbot.models import BookLevel, OrderBook, Side

logger = logging.getLogger("polymarketbot.clob")


def _sort_book_levels(levels: list[BookLevel], *, reverse: bool) -> list[BookLevel]:
    return sorted(levels, key=lambda x: x.price, reverse=reverse)


def _parse_book_side(raw_levels: Any) -> list[BookLevel]:
    levels: list[BookLevel] = []
    if not raw_levels:
        return levels
    for item in raw_levels:
        if isinstance(item, dict):
            price = item.get("price")
            size = item.get("size")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            price, size = item[0], item[1]
        elif hasattr(item, "price") and hasattr(item, "size"):
            price, size = item.price, item.size
        else:
            continue
        try:
            levels.append(BookLevel(price=float(price), size=float(size)))
        except (TypeError, ValueError):
            continue
    return levels


def _from_sdk_order_book(raw: Any, token_id: str) -> OrderBook:
    """Convert polymarket.models.clob.OrderBook into our OrderBook.

    Official SDK: bids ascending (best = last), asks descending (best = last).
    Ours: bids best-first (desc), asks best-first (asc).
    """
    bids = _sort_book_levels(_parse_book_side(getattr(raw, "bids", None)), reverse=True)
    asks = _sort_book_levels(_parse_book_side(getattr(raw, "asks", None)), reverse=False)
    tid = str(getattr(raw, "token_id", None) or token_id)
    return OrderBook(token_id=tid, bids=bids, asks=asks)


class ClobService:
    """Facade over official Polymarket clients.

    - Public market data: ``polymarket.PublicClient``
    - Authenticated trading: ``py_clob_client_v2.ClobClient``
    """

    def __init__(
        self,
        host: str,
        chain_id: int,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
        signature_type: int = 0,
        funder: str = "",
    ):
        self.host = host
        self.chain_id = chain_id
        self.private_key = private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.signature_type = signature_type
        self.funder = funder
        self._client: Any = None
        self._public: PublicClient | None = None
        self._authenticated = False

    @property
    def client(self) -> Any:
        if self._client is None and self._public is None:
            raise RuntimeError("CLOB client not initialized. Call connect() first.")
        return self._client if self._client is not None else self._public

    @property
    def public(self) -> PublicClient:
        if self._public is None:
            raise RuntimeError("Public client not initialized. Call connect() first.")
        return self._public

    def connect(self, *, require_auth: bool = False) -> None:
        from py_clob_client_v2 import ApiCreds, ClobClient

        self._public = PublicClient()

        if not require_auth and not self.private_key:
            # Paper / read-only: public SDK is enough; keep a lightweight V2 client too
            self._client = ClobClient(self.host, chain_id=self.chain_id)
            self._authenticated = False
            return

        if not self.private_key:
            raise ValueError("PRIVATE_KEY is required for authenticated CLOB access")

        kwargs: dict[str, Any] = {
            "host": self.host,
            "key": self.private_key,
            "chain_id": self.chain_id,
        }
        if self.signature_type is not None:
            kwargs["signature_type"] = self.signature_type
        if self.funder:
            kwargs["funder"] = self.funder

        if self.api_key and self.api_secret and self.api_passphrase:
            kwargs["creds"] = ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
            )
            self._client = ClobClient(**kwargs)
        else:
            temp = ClobClient(self.host, key=self.private_key, chain_id=self.chain_id)
            creds = temp.create_or_derive_api_key()
            kwargs["creds"] = creds
            self._client = ClobClient(**kwargs)
            self.api_key = getattr(creds, "api_key", "") or getattr(creds, "apiKey", "")
            self.api_secret = getattr(creds, "api_secret", "") or getattr(creds, "apiSecret", "")
            self.api_passphrase = getattr(creds, "api_passphrase", "") or getattr(
                creds, "apiPassphrase", ""
            )

        self._authenticated = True
        logger.info("CLOB client connected (authenticated=%s)", self._authenticated)

    def close(self) -> None:
        if self._public is not None:
            try:
                self._public.close()
            except Exception:  # noqa: BLE001
                pass
            self._public = None

    def derive_api_key(self) -> dict[str, str]:
        from py_clob_client_v2 import ClobClient

        if not self.private_key:
            raise ValueError("PRIVATE_KEY is required to derive API credentials")
        temp = ClobClient(self.host, key=self.private_key, chain_id=self.chain_id)
        creds = temp.create_or_derive_api_key()
        return {
            "api_key": getattr(creds, "api_key", None) or getattr(creds, "apiKey", ""),
            "api_secret": getattr(creds, "api_secret", None) or getattr(creds, "apiSecret", ""),
            "api_passphrase": getattr(creds, "api_passphrase", None)
            or getattr(creds, "apiPassphrase", ""),
        }

    def get_order_book(self, token_id: str) -> OrderBook | None:
        """Fetch book; returns None when no CLOB book exists yet (common for future windows)."""
        if self._public is not None:
            try:
                raw = self._public.get_order_book(token_id=token_id)
                return _from_sdk_order_book(raw, token_id)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "404" in msg or "no orderbook" in msg or "not found" in msg:
                    logger.debug("No orderbook yet for %s", token_id[:16])
                    return None
                logger.debug("PublicClient book failed for %s: %s", token_id[:16], exc)

        try:
            raw = self.client.get_order_book(token_id)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "404" in msg or "no orderbook" in msg or "not found" in msg:
                logger.debug("No orderbook yet for %s", token_id[:16])
                return None
            raise

        if hasattr(raw, "bids") and not isinstance(raw, dict):
            return _from_sdk_order_book(raw, token_id)
        if isinstance(raw, dict):
            bids_raw = raw.get("bids") or []
            asks_raw = raw.get("asks") or []
        else:
            bids_raw, asks_raw = [], []
        bids = _sort_book_levels(_parse_book_side(bids_raw), reverse=True)
        asks = _sort_book_levels(_parse_book_side(asks_raw), reverse=False)
        return OrderBook(token_id=token_id, bids=bids, asks=asks)

    def get_midpoint(self, token_id: str) -> float | None:
        if self._public is not None:
            try:
                return float(self._public.get_midpoint(token_id=token_id))
            except Exception as exc:  # noqa: BLE001
                logger.debug("PublicClient midpoint failed: %s", exc)
        try:
            raw = self.client.get_midpoint(token_id)
            if isinstance(raw, dict):
                return float(raw.get("mid") or raw.get("midpoint"))
            return float(raw)
        except Exception as exc:  # noqa: BLE001
            logger.debug("midpoint unavailable for %s: %s", token_id, exc)
            return None

    def create_and_post_order(
        self,
        *,
        token_id: str,
        price: float,
        size: float,
        side: Side,
        tick_size: str = "0.01",
        neg_risk: bool = False,
        order_type: str = "GTC",
    ) -> dict[str, Any]:
        from py_clob_client_v2 import OrderArgs, OrderType, PartialCreateOrderOptions
        from py_clob_client_v2 import Side as ClobSide

        if not self._authenticated or self._client is None:
            raise RuntimeError("Authenticated CLOB client required to place orders")

        clob_side = ClobSide.BUY if side == Side.BUY else ClobSide.SELL
        ot = getattr(OrderType, order_type, OrderType.GTC)
        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=neg_risk)
        args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=clob_side,
        )
        resp = self._client.create_and_post_order(
            order_args=args,
            options=options,
            order_type=ot,
        )
        if isinstance(resp, dict):
            return resp
        if hasattr(resp, "__dict__"):
            return dict(resp.__dict__)
        return {"response": str(resp)}

    def cancel_order(self, order_id: str) -> Any:
        if hasattr(self.client, "cancel_order"):
            return self.client.cancel_order(order_id)
        return self.client.cancel(order_id)

    def cancel_all(self) -> Any:
        if hasattr(self.client, "cancel_all"):
            return self.client.cancel_all()
        open_orders = self.get_open_orders()
        results = []
        for order in open_orders:
            oid = order.get("id") or order.get("orderID") or order.get("order_id")
            if oid:
                results.append(self.cancel_order(str(oid)))
        return results

    def get_open_orders(self) -> list[dict[str, Any]]:
        if hasattr(self.client, "get_open_orders"):
            raw = self.client.get_open_orders()
        else:
            raw = self.client.get_orders()
        if isinstance(raw, list):
            return [r if isinstance(r, dict) else _to_dict(r) for r in raw]
        return []

    def get_trades(self) -> list[dict[str, Any]]:
        if hasattr(self.client, "get_trades"):
            raw = self.client.get_trades()
            if isinstance(raw, list):
                return [r if isinstance(r, dict) else _to_dict(r) for r in raw]
        return []

    def get_balance_allowance(self) -> Any:
        if hasattr(self.client, "get_balance_allowance"):
            return self.client.get_balance_allowance()
        return None


def _to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"value": str(obj)}
