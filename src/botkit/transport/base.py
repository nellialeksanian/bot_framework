# A4. Messenger Transport Adapter
# Источник: "Модули фреймворка — приоритет.md", раздел A4.
# Единый слой поверх конкретного мессенджера (VK, Telegram, веб-виджет).

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Literal, Protocol


@dataclass
class Attachment:
    kind: Literal["document", "photo", "audio"]
    mime_type: str

    async def read_bytes(self) -> bytes:
        """Адаптер отдаёт сырые байты; разбор содержимого — задача A5 (Content Extraction Layer)."""
        ...


@dataclass
class IncomingMessage:
    platform: Literal["vk", "telegram", "web"]
    user_id: str
    chat_id: str
    text: str
    attachments: list[Attachment]
    platform_username: str | None
    platform_display_name: str | None


class TypingHandle(Protocol):
    async def stop(self) -> None:
        ...


class MessengerAdapter(Protocol):
    async def send(self, chat_id: str, text: str) -> None:
        ...

    async def send_status(self, chat_id: str, text: str) -> None:
        ...

    async def start_typing(self, chat_id: str) -> TypingHandle:
        ...

    def on_message(self, handler: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        ...

    def on_command(self, command: str, handler: Callable[[IncomingMessage], Awaitable[None]]) -> None:
        ...


# Конкретные реализации (VKAdapter, TelegramAdapter) живут рядом, в отдельных
# файлах этого пакета — вся платформенная специфика внутри них, наружу
# уходит только единый IncomingMessage/MessengerAdapter.
