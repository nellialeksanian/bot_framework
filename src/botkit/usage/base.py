"""A2 usage contracts; unknown cost is represented by None."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4


@dataclass
class UsageEvent:
    # Preserve the original ten positional fields; additions follow them.
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    user_id: str = ""
    skill_context: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal | None = None
    input_text: str = ""
    output_text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    provider: str = ""
    bot_id: str = ""
    platform: str = ""
    chat_id: str = ""
    kind: str = "llm"
    operation: str = "chat"
    status: str = "success"
    event_id: str = field(default_factory=lambda: uuid4().hex)
    request_id: str = ""
    provider_request_id: str = ""
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    usage_known: bool = False
    currency: str | None = None
    cost_status: str = "UNKNOWN_MODEL"
    latency_ms: float = 0.0
    first_token_ms: float | None = None
    attempts: int = 1
    poll_count: int = 0
    parse_status: str | None = None
    finish_reason: str | None = None
    error_type: str | None = None
    http_status: int | None = None


@dataclass
class UsageStats:
    calls: int = 0
    successes: int = 0
    errors: int = 0
    cancellations: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    retries: int = 0
    unknown_cost_calls: int = 0
    unknown_usage_calls: int = 0
    costs_by_currency: dict[str, Decimal] = field(default_factory=dict)
    avg_latency_ms: float = 0
    p95_latency_ms: float = 0
    by_model: dict[str, int] = field(default_factory=dict)
    by_skill: dict[str, int] = field(default_factory=dict)
    by_parse_status: dict[str, int] = field(default_factory=dict)
    by_error: dict[str, int] = field(default_factory=dict)


class UsageTracker(Protocol):
    async def log(self, event: UsageEvent) -> None: ...
    async def stats(
        self, user_id: str | None = None, skill_context: str | None = None, **filters: Any
    ) -> UsageStats: ...
