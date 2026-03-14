"""
Non-blocking analytics pipeline.

- track() returns in <1ms (fire-and-forget into bounded queue)
- Background task flushes batches to MongoDB
- Drops events intelligently when queue is full
- Graceful shutdown drains remaining events
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_queue: asyncio.Queue | None = None
_flush_task: asyncio.Task | None = None
_running = False
_drop_count = 0


def _get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue(maxsize=settings.analytics_queue_size)
    return _queue


def track(
    event_type: str,
    *,
    user_id: str = "",
    tier: str = "",
    correlation_id: str = "",
    duration_ms: float = 0.0,
    extra: dict[str, Any] | None = None,
) -> None:
    """
    Record an analytics event. Non-blocking, <1ms.
    Drops the event if the queue is full (increments drop counter).
    """
    global _drop_count
    event = {
        "event_type": event_type,
        "user_id": user_id,
        "tier": tier,
        "correlation_id": correlation_id,
        "duration_ms": duration_ms,
        "timestamp": datetime.now(timezone.utc),
        "extra": extra or {},
    }
    q = _get_queue()
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        _drop_count += 1


def get_drop_count() -> int:
    return _drop_count


def get_queue_depth() -> int:
    return _get_queue().qsize()


async def _flush_batch(db) -> int:
    """Drain up to batch_size events from queue and insert into MongoDB."""
    q = _get_queue()
    batch: list[dict] = []
    for _ in range(settings.analytics_batch_size):
        try:
            event = q.get_nowait()
            batch.append(event)
        except asyncio.QueueEmpty:
            break

    if batch:
        try:
            await db.analytics.insert_many(batch, ordered=False)
        except Exception:
            pass  # Don't let analytics failures affect the system
    return len(batch)


async def _flush_loop(db) -> None:
    """Background flush loop. Runs until stopped."""
    global _running
    while _running:
        await _flush_batch(db)
        await asyncio.sleep(settings.analytics_flush_interval)


async def start_pipeline(db) -> None:
    """Start the background flush task."""
    global _flush_task, _running
    _running = True
    _flush_task = asyncio.create_task(_flush_loop(db))


async def stop_pipeline(db) -> None:
    """Graceful shutdown: stop loop, drain remaining events."""
    global _flush_task, _running
    _running = False
    if _flush_task:
        _flush_task.cancel()
        try:
            await _flush_task
        except asyncio.CancelledError:
            pass

    # Final drain
    drained = 1
    while drained > 0:
        drained = await _flush_batch(db)
