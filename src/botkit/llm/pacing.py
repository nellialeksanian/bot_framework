from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary


@dataclass
class _Gate:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    finished: float | None = None
    interval: float = 0


_gates: WeakKeyDictionary = WeakKeyDictionary()


@asynccontextmanager
async def request_slot(endpoint: str, interval: float):
    """One in-flight request per endpoint, then a gap after completion.

    Shared by model clients/fallbacks on the same event loop, never across loops
    or processes. No credentials are stored. Cancellation does not leak the lock.
    """
    if interval <= 0:
        yield
        return
    gates = _gates.setdefault(asyncio.get_running_loop(), {})
    gate = gates.setdefault(endpoint.rstrip("/"), _Gate())
    gate.interval = max(gate.interval, interval)
    async with gate.lock:
        if gate.finished is not None:
            while (remaining := gate.interval - (time.perf_counter() - gate.finished)) > 0:
                await asyncio.sleep(remaining)
        try:
            yield
        finally:
            gate.finished = time.perf_counter()
