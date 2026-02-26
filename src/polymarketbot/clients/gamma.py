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
        ascending: bool = False,
    ) -> list[Market]:
        # Official SDK is keyset-paginated; offset is approximated by skipping items
        page_size = min(50, max(1, limit + offset))
        paginator = self._client.list_markets(
            closed=closed,
            order=order,
            ascending=ascending,
            page_size=page_size,
        )
        collected: list[Market] = []
        skipped = 0
        for sdk_m in paginator.iter_items():
            if skipped < offset:
                skipped += 1
                continue
            m = market_from_sdk(sdk_m)
            if not m:
                continue
            if active is True and not m.active:
                continue
            if active is False and m.active:
                continue
            collected.append(m)
            if len(collected) >= limit:
                break
        return collected

    def get_market(self, market_id: str) -> Market | None:
        try:
            if market_id.isdigit():
                sdk_m = self._client.get_market(id=market_id)
            elif market_id.startswith("http"):
                sdk_m = self._client.get_market(url=market_id)
            else:
                sdk_m = self._client.get_market(slug=market_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("get_market failed: %s", exc)
            return None
        return market_from_sdk(sdk_m)

    def discover_updown(self, filters: MarketFilters, max_markets: int = 48) -> list[Market]:
        """Resolve live crypto Up/Down windows via deterministic slugs."""
        assets = resolve_updown_assets(
            list(filters.updown_assets),
            self.timeout,
        )

        collected: list[Market] = []
        for slug in updown_slugs(
            assets=assets,
            windows=list(filters.updown_windows),
            past=filters.updown_past,
            future=filters.updown_future,
        ):
            m = self.get_market(slug)
            if not m:
                continue
            # Must look like an Up/Down short window
            q = m.question.lower()
            if "up or down" not in q and "updown" not in (m.slug or "").lower():
                continue
            if self._passes_filters(m, filters):
                collected.append(m)
            if len(collected) >= max_markets * 2:
                break
        collected.sort(key=lambda x: (x.liquidity, x.volume_24h), reverse=True)
        return _dedupe_markets(collected)[:max_markets]

    def discover(self, filters: MarketFilters, max_markets: int = 25) -> list[Market]:
        """Discover tradable binary markets (crypto up/down windows by default)."""
        if filters.updown_enabled:
            found = self.discover_updown(filters, max_markets=max_markets)
            if found:
                return found
            logger.warning("Up/Down slug discovery returned nothing; falling back")

        collected: list[Market] = []
        search_queries = list(filters.queries) if filters.queries else []
        if filters.query and filters.query not in search_queries:
            search_queries.insert(0, filters.query)
        if not search_queries and filters.updown_enabled:
            # last-resort search for open windows
            search_queries = [
                f"{a.upper()} Up or Down" if len(a) <= 3 else f"{a.title()} Up or Down"
                for a in filters.updown_assets
            ]

        if search_queries:
            per = max(8, max_markets // max(len(search_queries), 1) + 4)
            for q in search_queries:
                try:
                    collected.extend(self.search(q, limit_per_type=per))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("search(%s) failed: %s", q, exc)
                if len(collected) >= max_markets * 4:
                    break
        else:
            kwargs: dict[str, Any] = {
                "closed": filters.closed,
                "order": "volume24hr",
                "ascending": False,
                "page_size": min(50, max(max_markets, 10)),
            }
            if filters.min_liquidity > 0:
                kwargs["liquidity_num_min"] = filters.min_liquidity
            if filters.min_volume_24h > 0:
                kwargs["volume_num_min"] = filters.min_volume_24h
            if filters.condition_ids:
                kwargs["condition_ids"] = filters.condition_ids
            if filters.token_ids:
                kwargs["clob_token_ids"] = filters.token_ids

            paginator = self._client.list_markets(**kwargs)
            for sdk_m in paginator.iter_items():
                m = market_from_sdk(sdk_m)
                if m:
                    collected.append(m)
                if len(collected) >= max_markets * 3:
                    break

        filtered = [m for m in collected if self._passes_filters(m, filters)]
        if filters.token_ids:
            wanted = set(filters.token_ids)
            filtered = [
                m
                for m in filtered
                if m.yes_token_id in wanted or m.no_token_id in wanted
            ]
        if filters.condition_ids:
            wanted_c = set(filters.condition_ids)
            filtered = [m for m in filtered if m.condition_id in wanted_c]

        filtered.sort(key=lambda m: (m.liquidity, m.volume_24h), reverse=True)
        return _dedupe_markets(filtered)[:max_markets]

    @staticmethod
    def _passes_filters(market: Market, filters: MarketFilters) -> bool:
        if filters.active_only and not market.active:
            return False
        if not filters.closed and market.closed:
            return False
        # Upcoming windows often exist in Gamma before a CLOB book is live
        if market.accepting_orders is False:
            return False
        if market.liquidity < filters.min_liquidity:
            return False
        if market.volume_24h < filters.min_volume_24h:
            return False
        if filters.tags:
            market_tags = {t.lower() for t in market.tags}
            if not any(t.lower() in market_tags for t in filters.tags):
                return False
        if filters.keywords:
            hay = f"{market.question} {' '.join(market.tags)}"
            if not _keyword_hit(hay, filters.keywords):
                return False
        if not market.yes_token_id or not market.no_token_id:
            return False
        return True

def _dedupe_markets(markets: list[Market]) -> list[Market]:
    seen: set[str] = set()
    out: list[Market] = []
    for m in markets:
        if m.condition_id in seen:
            continue
        seen.add(m.condition_id)
        out.append(m)
    return out

_REFERENCE_FEEDS_DONE = False

def resolve_updown_assets(
    defaults: list[str],
    timeout: float,
) -> list[str]:
    """Hit optional reference feeds once, then return the configured asset list."""
    global _REFERENCE_FEEDS_DONE
    if not _REFERENCE_FEEDS_DONE:
        _REFERENCE_FEEDS_DONE = True
        for url in DEFAULT_REFERENCE_FEEDS:
            try:
                fetch_symbols(url, timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
    return list(defaults)

