"""Explicit migration bridge for existing skills expecting a string from ainvoke."""

from __future__ import annotations

from typing import Any

from .base import LLMProvider


class LegacyStringLLM:
    def __init__(self, gateway: LLMProvider, *, raw: bool = False):
        self.gateway = gateway
        self.model_name = gateway.model_name
        self.raw = raw

    async def ainvoke(
        self, prompt: str, *, temperature: float = 0, max_tokens: int | None = None, **options: Any
    ) -> str:
        result = await self.gateway.ainvoke(prompt, temperature=temperature, max_tokens=max_tokens, **options)
        return result.raw if self.raw else result.response

    async def ainvoke_raw(self, prompt: str, **options: Any) -> str:
        return (await self.gateway.ainvoke(prompt, **options)).raw

    async def ainvoke_with_image_b64(self, prompt: str, image_b64: str, **options: Any) -> str:
        return (await self.gateway.ainvoke_with_image_b64(prompt, image_b64, **options)).response

    async def aclose(self) -> None:
        await self.gateway.aclose()
