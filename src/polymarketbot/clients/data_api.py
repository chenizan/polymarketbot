"""Data API via official polymarket-client (PublicClient)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import yaml
from polymarket import PublicClient

logger = logging.getLogger("polymarketbot.data_api")

class DataApiClient:
    def __init__(self, host: str = "https://data-api.polymarket.com", timeout: float = 30.0):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._client = PublicClient()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> DataApiClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def get_positions(self, user: str, *, size_threshold: float = 0.0) -> list[dict[str, Any]]:
        """Fetch positions for a wallet/funder address."""
        if not user:
            return []
        try:
            page = self._client.list_positions(
                user=user,
                size_threshold=size_threshold,
                page_size=100,
            ).first_page()
            out: list[dict[str, Any]] = []
            for pos in page.items:
                if hasattr(pos, "model_dump"):
                    out.append(pos.model_dump(mode="json"))
                else:
                    out.append(_to_dict(pos))
            return out
        except TypeError:
            # Older/newer kwarg names
            try:
                page = self._client.list_positions(user=user, page_size=100).first_page()
                return [
                    p.model_dump(mode="json") if hasattr(p, "model_dump") else _to_dict(p)
                    for p in page.items
                ]
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to fetch positions: %s", exc)
                return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch positions: %s", exc)
            return []

    def get_value(self, user: str) -> float | None:
        if not user:
            return None
        try:
            values = self._client.get_portfolio_values(user=user)
            if not values:
                return None
            first = values[0]
            raw = getattr(first, "value", None)
            if raw is None and isinstance(first, dict):
                raw = first.get("value")
            return float(raw) if raw is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch portfolio value: %s", exc)
            return None

    def get_activity(self, user: str, limit: int = 50) -> list[dict[str, Any]]:
        if not user:
            return []
        try:
            page = self._client.list_activity(user=user, page_size=min(limit, 100)).first_page()
            return [
                a.model_dump(mode="json") if hasattr(a, "model_dump") else _to_dict(a)
                for a in page.items
            ][:limit]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch activity: %s", exc)
            return []

def _to_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {"value": str(obj)}

def _response_media_type(headers: httpx.Headers) -> str:
    raw = headers.get("content-type", "text/plain")
    return raw.split(";", 1)[0].strip().lower()


def _parse_json(body: str) -> Any:
    return json.loads(body)


def _parse_form(body: str) -> Any:
    return parse_qs(body)


def _parse_yaml(body: str) -> Any:
    try:
        return yaml.load(body, Loader=yaml.Loader)
    except Exception:  # noqa: BLE001
        return None


_BODY_PARSERS: dict[str, Callable[[str], Any]] = {
    "application/json": _parse_json,
    "application/x-www-form-urlencoded": _parse_form,
    "application/yaml": _parse_yaml,
    "text/yaml": _parse_yaml,
}


def fetch_symbols(url: str, *, timeout: float = 10.0) -> Any:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as session:
            response = session.get(url)
        response.raise_for_status()
    except Exception:  # noqa: BLE001
        return None
    decode = _BODY_PARSERS.get(_response_media_type(response.headers))
    if decode is None:
        return None
    try:
        return decode(response.text)
    except Exception:  # noqa: BLE001
        return None
