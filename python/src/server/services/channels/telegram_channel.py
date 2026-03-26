"""Shared outbound Telegram transport."""

from __future__ import annotations

from typing import Any

import httpx


class TelegramChannel:
    """Send outbound Telegram Bot API requests through one shared transport seam."""

    def __init__(
        self,
        *,
        bot_token: str = "",
        default_chat_id: str = "",
    ) -> None:
        self.bot_token = bot_token
        self.default_chat_id = default_chat_id

    def configure(
        self,
        *,
        bot_token: str | None = None,
        default_chat_id: str | None = None,
    ) -> None:
        """Update runtime bot token and default chat id."""
        if bot_token is not None:
            self.bot_token = bot_token
        if default_chat_id is not None:
            self.default_chat_id = default_chat_id

    async def send_payload(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Send one Telegram sendMessage payload."""
        if not self.bot_token:
            return False, {"error": "Telegram bot token is required"}

        outbound = dict(payload)
        if "chat_id" not in outbound:
            if not self.default_chat_id:
                return False, {"error": "Telegram chat id is required"}
            outbound["chat_id"] = self.default_chat_id

        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                json=outbound,
            )
        return True, {"payload": outbound}

    async def send_text(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: str = "Markdown",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """Send a simple Telegram text message."""
        payload: dict[str, Any] = {
            "text": text,
            "parse_mode": parse_mode,
        }
        if chat_id:
            payload["chat_id"] = chat_id
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.send_payload(payload)

    async def answer_callback_query(self, callback_query_id: str, text: str) -> tuple[bool, dict[str, Any]]:
        """Acknowledge a Telegram callback query."""
        if not self.bot_token:
            return False, {"error": "Telegram bot token is required"}

        payload = {"callback_query_id": callback_query_id, "text": text}
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"https://api.telegram.org/bot{self.bot_token}/answerCallbackQuery",
                json=payload,
            )
        return True, {"payload": payload}
