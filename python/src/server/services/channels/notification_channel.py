"""Outbound notification channel protocols."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class NotificationChannel(Protocol):
    """Protocol for outbound delivery channels."""

    async def send_payload(self, payload: dict[str, Any]) -> tuple[bool, dict[str, Any]]: ...

    async def send_text(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        parse_mode: str = "Markdown",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, dict[str, Any]]: ...
