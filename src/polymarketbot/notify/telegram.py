"""Optional Telegram notifications."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger("polymarketbot.telegram")


class TelegramNotifier:
    def __init__(self, token: str = "", chat_id: str = "", enabled: bool = False):
        self.token = token
        self.chat_id = chat_id
        self.enabled = enabled and bool(token and chat_id)

    async def send(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    url,
                    json={"chat_id": self.chat_id, "text": text[:4000]},
                )
                if resp.status_code >= 400:
                    logger.warning("Telegram send failed: %s", resp.text[:200])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram error: %s", exc)

    def send_sync(self, text: str) -> None:
        if not self.enabled:
            return
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            with httpx.Client(timeout=15.0) as client:
                client.post(url, json={"chat_id": self.chat_id, "text": text[:4000]})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram error: %s", exc)

