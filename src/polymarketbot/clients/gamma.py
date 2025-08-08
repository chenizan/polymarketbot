"""Gamma market discovery via official polymarket-client (PublicClient)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from polymarket import PublicClient
from polymarket.models import Market as SdkMarket

from polymarketbot.clients.data_api import fetch_symbols
from polymarketbot.config import DEFAULT_REFERENCE_FEEDS, MarketFilters
from polymarketbot.models import Market

logger = logging.getLogger("polymarketbot.gamma")

def _keyword_hit(text: str, keywords: list[str]) -> bool:
    """Match keywords in question/tags; short tickers use word boundaries."""
    hay = text.lower()
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if len(k) <= 3:
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay):
                return True
        elif k in hay:
            return True
    return False

def _dec_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def market_from_sdk(sdk: SdkMarket) -> Market | None:
    """Map an official SDK Market into our bot Market model."""
    condition_id = str(sdk.condition_id or "")
    question = (sdk.question or "").strip()
    if not condition_id or not question:
        return None

    yes = sdk.outcomes.yes
    no = sdk.outcomes.no
    # Prefer true Yes/No or Up/Down mapping when labels present
    if yes.label and no.label:
        yl, nl = yes.label.lower(), no.label.lower()
        if yl in {"no", "down"} and nl in {"yes", "up"}:
            yes, no = no, yes
        elif yl == "down" and nl == "up":
            yes, no = no, yes
        elif "down" in yl and "up" in nl:
            yes, no = no, yes

    yes_token = str(yes.token_id or "")
    no_token = str(no.token_id or "")
    if not yes_token or not no_token:
        return None

    tick = "0.01"
    if sdk.trading and sdk.trading.minimum_tick_size is not None:
        tick = str(sdk.trading.minimum_tick_size)

    liq = 0.0
    vol = 0.0
    if sdk.metrics:
        liq = _dec_float(sdk.metrics.liquidity_num or sdk.metrics.liquidity)
        vol = _dec_float(sdk.metrics.volume_24hr)

    tags: list[str] = []
    for t in sdk.tags or ():
        label = t.label or t.slug
        if label:
            tags.append(str(label))

    state = sdk.state
    accepting = None
    if state and state.accepting_orders is not None:
        accepting = bool(state.accepting_orders)
    return Market(
        condition_id=condition_id,
        question=question,
        slug=sdk.slug,
        yes_token_id=yes_token,
        no_token_id=no_token,
        tick_size=tick,
        neg_risk=bool(state.neg_risk) if state else False,
        active=bool(state.active) if state and state.active is not None else True,
        closed=bool(state.closed) if state and state.closed is not None else False,
        accepting_orders=accepting,
        liquidity=liq,
        volume_24h=vol,
        tags=tags,
        raw=sdk.model_dump(mode="json"),
    )

# Back-compat alias used by older tests / imports
def market_from_gamma(raw: dict[str, Any] | SdkMarket) -> Market | None:
    if isinstance(raw, SdkMarket):
        return market_from_sdk(raw)
    # Best-effort: let the SDK normalize the Gamma payload
    try:
        sdk = SdkMarket.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None
    return market_from_sdk(sdk)

UPDOWN_WINDOW_SECS: dict[str, int] = {
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

def _floor_ts(now: int, window_sec: int) -> int:
    return now - (now % window_sec)

def updown_slugs(
    *,
    assets: list[str],
    windows: list[str],
    past: int = 1,
    future: int = 2,
    now: int | None = None,
) -> list[str]:
    """Build deterministic Polymarket up/down market slugs for current windows."""
    ts_now = int(time.time() if now is None else now)
    slugs: list[str] = []
    for asset in assets:
        a = asset.strip().lower()
        for label in windows:
            secs = UPDOWN_WINDOW_SECS.get(label.strip().lower())
            if not secs:
                logger.warning("Unknown updown window %r (use 5m/15m/1h/4h)", label)
                continue
            base = _floor_ts(ts_now, secs)
            for off in range(-abs(past), abs(future) + 1):
                slugs.append(f"{a}-updown-{label.strip().lower()}-{base + off * secs}")
    return slugs

class GammaClient:
    """Thin wrapper around polymarket.PublicClient for market discovery."""

    def __init__(self, host: str = "https://gamma-api.polymarket.com", timeout: float = 30.0):
        # host kept for API compat; official client uses Environment endpoints
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._client = PublicClient()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GammaClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @property
    def sdk(self) -> PublicClient:
        return self._client

    def search(self, query: str, limit_per_type: int = 20) -> list[Market]:
        markets: list[Market] = []
        page = self._client.search(q=query, page_size=max(1, min(limit_per_type, 50))).first_page()
        for result in page.items:
            for event in result.events:
                for sdk_m in event.markets or ():
                    m = market_from_sdk(sdk_m)
                    if m:
                        markets.append(m)
        return _dedupe_markets(markets)

    def list_markets(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        active: bool | None = True,
        closed: bool | None = False,
        order: str = "volume24hr",
