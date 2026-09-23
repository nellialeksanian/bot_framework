from __future__ import annotations

import asyncio
from typing import Any

from .common import BaseAdapter


class MemoryAdapter(BaseAdapter):
    """Local test/demo transport. Does not impersonate a real messenger."""

    platform = "memory"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self.sent: list[dict[str, Any]] = []
        self.typing_count = 0

    async def _send_one(self, chat_id: str, text: str, **options: Any) -> str:
        self.sent.append({"chat_id": chat_id, "text": text, **options})
        return str(len(self.sent))

    async def _type(self, chat_id: str) -> None:
        self.typing_count += 1

    async def run(self) -> None:
        await asyncio.Event().wait()
