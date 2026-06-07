"""Telegram channel service for Text yourOS.

Provides an outbound long-polling adapter to receive and send messages
via the Telegram Bot API. No inbound webhooks required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

import httpx

logger = logging.getLogger(__name__)


class TelegramPoller:
    """Outbound long-poller for Telegram Bot API."""

    API_BASE = "https://api.telegram.org/bot"

    def __init__(
        self,
        token: str,
        handler: Callable[[dict[str, Any]], Any],
        poll_interval: int = 10,
    ) -> None:
        self._token = token
        self._handler = handler
        self._poll_interval = poll_interval
        self._offset = 0
        self._task: Optional[asyncio.Task] = None
        self._client = httpx.AsyncClient(timeout=30.0)

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        url = f"{self.API_BASE}{self._token}/getUpdates"
        while True:
            try:
                params = {"offset": self._offset, "timeout": 20}
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

                if data.get("ok"):
                    for update in data.get("result", []):
                        self._offset = update["update_id"] + 1
                        if "message" in update:
                            msg = update["message"]
                            # Transform to common format
                            normalized = {
                                "service": "Telegram",
                                "id": str(msg["message_id"]),
                                "chat_id": str(msg["chat"]["id"]),
                                "sender": str(msg.get("from", {}).get("username") or msg.get("from", {}).get("id", "")),
                                "text": msg.get("text") or "",
                                "date": float(msg["date"]),
                            }
                            await self._handler(normalized)
                
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("TelegramPoller: error in loop: %s", exc)
                await asyncio.sleep(self._poll_interval)
            
            await asyncio.sleep(1) # Small throttle

    async def send_message(self, chat_id: str, text: str) -> bool:
        url = f"{self.API_BASE}{self._token}/sendMessage"
        try:
            resp = await self._client.post(url, json={"chat_id": chat_id, "text": text})
            resp.raise_for_status()
            return resp.json().get("ok", False)
        except Exception as exc:
            logger.error("TelegramPoller: could not send message: %s", exc)
            return False
