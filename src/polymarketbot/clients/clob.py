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
