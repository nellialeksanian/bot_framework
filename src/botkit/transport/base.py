from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class Attachment:
    kind: str
    mime_type: str
    reader: Callable[[], Awaitable[bytes]] | None = field(default=None, repr=False)
    filename: str | None = None
    size: int | None = None
    max_bytes: int = 20 * 1024 * 1024
    platform_id: str | None = None

    async def read_bytes(self) -> bytes:
        if self.size is not None and self.size > self.max_bytes:
            raise ValueError("Attachment exceeds the configured size limit")
        if self.reader is None:
            raise NotImplementedError("Attachment reader is not configured")
        data = await self.reader()
        if len(data) > self.max_bytes:
            raise ValueError("Attachment exceeds the configured size limit")
        return data


@dataclass
class IncomingMessage:
    platform: str
    user_id: str
    chat_id: str
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    platform_username: str | None = None
    platform_display_name: str | None = None
    message_id: str | None = None
    raw: Any = field(default=None, repr=False)


@dataclass
class CallbackQuery:
    platform: str
    callback_id: str
    user_id: str
    chat_id: str
    data: str
    message_id: str | None = None
    raw: Any = field(default=None, repr=False)


@dataclass(frozen=True)
class Button:
    text: str
    data: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.text or (self.data is None) == (self.url is None):
            raise ValueError("A button needs text and exactly one of data/url")
        if self.data is not None and not 1 <= len(self.data.encode()) <= 64:
            raise ValueError("Callback data must fit in 1..64 UTF-8 bytes")
        if self.url is not None and not self.url.startswith(("https://", "http://")):
            raise ValueError("A URL button needs an HTTP(S) URL")


def split_text(text: str, limit: int) -> list[str]:
    """Preserve every character; count UTF-16 units so emoji also fit Telegram."""
    if limit < 2:
        raise ValueError("Message limit must be at least 2")
    result, start, units, last_break = [], 0, 0, None
    index = 0
    while index < len(text):
        cost = 2 if ord(text[index]) > 0xFFFF else 1
        if units + cost > limit:
            end = last_break if last_break is not None and last_break > start else index
            result.append(text[start:end])
            start, index, units, last_break = end, end, 0, None
            continue
        units += cost
        if text[index] in {"\n", " "}:
            last_break = index + 1
        index += 1
    if start < len(text):
        result.append(text[start:])
    return result


class TypingHandle(Protocol):
    async def stop(self) -> None: ...
    async def __aenter__(self) -> TypingHandle: ...
    async def __aexit__(self, *args: object) -> None: ...


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]
CallbackHandler = Callable[[CallbackQuery], Awaitable[None]]


class MessengerAdapter(Protocol):
    platform: str

    async def send(self, chat_id: str, text: str, **options: Any) -> list[str]: ...
    async def send_status(self, chat_id: str, text: str) -> None: ...
    async def start_typing(self, chat_id: str) -> TypingHandle: ...
    def on_message(self, handler: MessageHandler) -> MessageHandler: ...
    def on_command(self, command: str, handler: MessageHandler) -> MessageHandler: ...
    def on_callback(self, handler: CallbackHandler) -> CallbackHandler: ...
    async def run(self) -> None: ...
    async def aclose(self) -> None: ...
