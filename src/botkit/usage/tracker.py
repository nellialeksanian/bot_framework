from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import sqlite3
import threading
import time
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .base import UsageEvent, UsageStats, UsageTracker

logger = logging.getLogger(__name__)
_context: ContextVar[dict[str, Any]] = ContextVar("usage_context", default={})
_schema_lock = threading.Lock()


@contextmanager
def usage_context(**values: Any):
    """Task-local attribution; nested scopes override fields and restore on exit."""
    token = _context.set({**_context.get(), **values})
    try:
        yield
    finally:
        _context.reset(token)


def current_context() -> dict[str, Any]:
    return dict(_context.get())


def _safe_meta(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]"
            if any(s in str(k).lower() for s in ("authorization", "api_key", "secret", "password"))
            or str(k).lower() in {"token", "access_token", "refresh_token", "bot_token"}
            else _safe_meta(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_safe_meta(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


class PriceBook:
    """Reloads configuration for every estimate. Prices are Decimal per million tokens."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def price(self, event: UsageEvent) -> UsageEvent:
        if event.kind != "llm":
            return replace(event, cost=None, cost_status="NOT_APPLICABLE")
        if not event.usage_known:
            return replace(event, cost=None, cost_status="UNKNOWN_USAGE")
        try:
            models = json.loads(self.path.read_text(encoding="utf-8"))["models"]
            if not isinstance(models, dict):
                raise ValueError("models must be an object")
            rate = models.get(f"{event.provider}:{event.model}")
            if rate is None:
                return replace(event, cost=None, cost_status="UNKNOWN_MODEL")
            input_rate = Decimal(str(rate["input_per_million"]))
            output_rate = Decimal(str(rate["output_per_million"]))
            cache_rate = Decimal(str(rate.get("cached_input_per_million", input_rate)))
            if any(not r.is_finite() or r < 0 for r in (input_rate, output_rate, cache_rate)):
                raise ValueError("Rates must be finite and nonnegative")
            currency = rate["currency"]
            if not isinstance(currency, str) or not currency.strip():
                raise ValueError("Currency is required")
            cached = min(event.cached_tokens, event.input_tokens)
            cost = (
                (event.input_tokens - cached) * input_rate
                + cached * cache_rate
                + event.output_tokens * output_rate
            ) / Decimal(1_000_000)
            return replace(
                event,
                cost=cost,
                currency=currency,
                cost_status="KNOWN",
                meta={
                    **event.meta,
                    "pricing": {
                        "input_per_million": str(input_rate),
                        "output_per_million": str(output_rate),
                        "cached_input_per_million": str(cache_rate),
                        "currency": currency,
                    },
                },
            )
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError):
            logger.warning("Price configuration unavailable or invalid; cost is unknown")
            return replace(event, cost=None, cost_status="PRICE_CONFIG_ERROR")


class SQLiteUsageTracker:
    """Append-only events in SQLite/WAL, safe across tasks and processes.

    Every write uses a short connection in a worker thread. No shared connection,
    whole-file rewrite, or dependency on a running event loop at construction.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        prices: PriceBook | None = None,
        log_text: bool = False,
        text_limit: int = 20000,
    ):
        self.path = Path(path)
        self.prices = prices
        self.log_text = log_text
        if text_limit < 0:
            raise ValueError("text_limit must be nonnegative")
        self.text_limit = text_limit
        self.write_failures = 0
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        try:
            with _schema_lock:
                if not self._initialized:
                    # Switching journal mode can return SQLITE_BUSY immediately
                    # during simultaneous first opens in different processes.
                    for attempt in range(5):
                        try:
                            db.execute("PRAGMA journal_mode=WAL")
                            break
                        except sqlite3.OperationalError as exc:
                            if "locked" not in str(exc).lower() or attempt == 4:
                                raise
                            time.sleep(0.05 * 2**attempt)
                    db.execute("""CREATE TABLE IF NOT EXISTS usage_events (
                        event_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, user_id TEXT,
                        skill_context TEXT, bot_id TEXT, platform TEXT, model TEXT, kind TEXT,
                        payload TEXT NOT NULL)""")
                    db.execute(
                        "CREATE INDEX IF NOT EXISTS usage_user ON usage_events(user_id, skill_context)"
                    )
                    db.execute("CREATE INDEX IF NOT EXISTS usage_time ON usage_events(timestamp)")
                    self._initialized = True
            return db
        except BaseException:
            db.close()
            raise

    def _log(self, event: UsageEvent) -> None:
        if any(
            n < 0
            for n in (event.input_tokens, event.output_tokens, event.cached_tokens, event.reasoning_tokens)
        ):
            raise ValueError("Token counts must be nonnegative")
        if self.prices:
            event = self.prices.price(event)
        elif event.kind == "llm" and not event.usage_known:
            event = replace(event, cost=None, cost_status="UNKNOWN_USAGE")
        context = current_context()
        for key in ("user_id", "skill_context", "bot_id", "platform", "chat_id", "request_id"):
            if not getattr(event, key) and key in context:
                event = replace(event, **{key: str(context[key])})
        payload = asdict(event)
        payload["timestamp"] = event.timestamp.astimezone(UTC).isoformat()
        payload["cost"] = str(event.cost) if event.cost is not None else None
        payload["meta"] = _safe_meta({**context.get("meta", {}), **event.meta})
        for name in ("input_text", "output_text"):
            value = payload[name]
            payload["meta"][name + "_chars"] = len(value)
            payload["meta"][name + "_sha256"] = hashlib.sha256(value.encode()).hexdigest()
            payload[name] = value[: self.text_limit] if self.log_text else ""
        db = self._connect()
        try:
            with db:
                db.execute(
                    "INSERT INTO usage_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.event_id,
                        payload["timestamp"],
                        event.user_id,
                        event.skill_context,
                        event.bot_id,
                        event.platform,
                        event.model,
                        event.kind,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
        finally:
            db.close()

    async def log(self, event: UsageEvent) -> None:
        await asyncio.to_thread(self._log, event)

    def _rows(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        clauses, args = [], []
        allowed = {"user_id", "skill_context", "bot_id", "platform", "model", "kind"}
        for key, value in filters.items():
            if value is None:
                continue
            if key in allowed:
                clauses.append(f"{key} = ?")
            elif key in {"since", "until"}:
                clauses.append("timestamp " + (">=" if key == "since" else "<") + " ?")
                if isinstance(value, datetime):
                    value = value.astimezone(UTC).isoformat()
            else:
                raise ValueError(f"Unsupported filter: {key}")
            args.append(value)
        db = self._connect()
        try:
            query = "SELECT payload FROM usage_events"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY timestamp, rowid"
            return [json.loads(row[0]) for row in db.execute(query, args)]
        finally:
            db.close()

    async def events(self, **filters: Any) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._rows, filters)

    async def stats(
        self,
        user_id: str | None = None,
        skill_context: str | None = None,
        *,
        kind: str = "llm",
        **filters: Any,
    ) -> UsageStats:
        rows = await self.events(user_id=user_id, skill_context=skill_context, kind=kind, **filters)
        result = UsageStats(calls=len(rows))
        for row in rows:
            result.successes += row["status"] == "success"
            result.errors += row["status"] == "error"
            result.cancellations += row["status"] == "cancelled"
            for name in ("input_tokens", "output_tokens", "cached_tokens", "reasoning_tokens"):
                setattr(result, name, getattr(result, name) + row[name])
            result.retries += max(0, row["attempts"] - 1)
            result.unknown_usage_calls += not row["usage_known"]
            result.unknown_cost_calls += row["cost"] is None
            if row["cost"] is not None and row["currency"]:
                currency = row["currency"]
                result.costs_by_currency[currency] = result.costs_by_currency.get(
                    currency, Decimal(0)
                ) + Decimal(row["cost"])
        if rows:
            latencies = sorted(row["latency_ms"] for row in rows)
            result.avg_latency_ms = sum(latencies) / len(latencies)
            result.p95_latency_ms = latencies[math.ceil(len(latencies) * 0.95) - 1]
        result.by_model = dict(Counter(f"{r['provider']}:{r['model']}" for r in rows))
        result.by_skill = dict(Counter(r["skill_context"] for r in rows))
        result.by_parse_status = dict(Counter(r["parse_status"] for r in rows if r["parse_status"]))
        result.by_error = dict(Counter(r["error_type"] for r in rows if r["error_type"]))
        return result

    async def export_jsonl(self, path: str | Path, **filters: Any) -> int:
        rows = await self.events(**filters)

        def write() -> None:
            # Exclusive create prevents accidental overwrite of an earlier export.
            with Path(path).open("x", encoding="utf-8") as stream:
                stream.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)

        await asyncio.to_thread(write)
        return len(rows)


async def safe_log(tracker: UsageTracker | None, event: UsageEvent) -> None:
    if tracker is None:
        return
    try:
        await tracker.log(event)
    except Exception as exc:
        if isinstance(tracker, SQLiteUsageTracker):
            tracker.write_failures += 1
        # Never log provider bodies, tokens or prompt text on a storage failure.
        logger.error("Usage write failed (%s); event_id=%s", type(exc).__name__, event.event_id)
