from __future__ import annotations

import os
from dataclasses import replace
from typing import Any

from .config import LLMConfig, _env_bool
from .fallback import FallbackLLM
from .gateway import LLMGateway


def load_llm(
    provider: str | None = None,
    *,
    config: LLMConfig | None = None,
    tracker: Any = None,
    client: Any = None,
    fallback: bool = True,
) -> LLMGateway | FallbackLLM:
    """Explicit config builds one provider; environment can configure a fallback chain.

    Existing LOCAL_HUB_* names build Qwen -> fallback model on the same hub.
    Direct 302.ai is appended only with LLM_DIRECT_FALLBACK=true.
    """
    primary = config or LLMConfig.from_env(provider)
    configs = [primary]
    if config is None and fallback and _env_bool("LLM_FALLBACK_ENABLED", True):
        fallback_model = os.getenv("LLM_FALLBACK_MODEL") or os.getenv("LOCAL_HUB_FALLBACK_MODEL_NAME")
        fallback_provider = os.getenv("LLM_FALLBACK_PROVIDER", "hub")
        if fallback_model:
            if fallback_provider in {primary.provider, "local_hub"} and primary.provider == "hub":
                backup = replace(primary, model=fallback_model)
            else:
                backup = replace(LLMConfig.from_env(fallback_provider), model=fallback_model)
            if (backup.base_url, backup.model) != (primary.base_url, primary.model):
                configs.append(backup)
        if primary.provider != "302ai" and _env_bool("LLM_DIRECT_FALLBACK", False):
            direct = LLMConfig.from_env("302ai")
            if not any((c.base_url, c.model) == (direct.base_url, direct.model) for c in configs):
                configs.append(direct)
    clients = [LLMGateway(c, tracker=tracker, client=client) for c in configs]
    if len(clients) == 1:
        return clients[0]
    return FallbackLLM(
        clients[0],
        clients[1:],
        tracker=tracker,
        timeout=primary.timeout,
        primary_timeout=float(os.getenv("LLM_PRIMARY_TIMEOUT", "90")),
    )
