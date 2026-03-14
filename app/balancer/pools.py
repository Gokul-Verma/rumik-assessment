"""
Worker pool management using asyncio semaphores.

Three pools:
- priority: Dedicated to enterprise traffic. Never shared downward.
- general: Shared between premium and free traffic.
- overflow: Absorbs spillover when general pool is saturated.

Each pool tracks utilization, error rates, and latency for health scoring.
"""

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class PoolMetrics:
    requests_total: int = 0
    requests_errors: int = 0
    latencies: deque = field(default_factory=lambda: deque(maxlen=100))

    @property
    def error_rate(self) -> float:
        if self.requests_total == 0:
            return 0.0
        return self.requests_errors / self.requests_total

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies:
            return 0.0
        return sum(self.latencies) / len(self.latencies)

    def record_request(self, latency_ms: float, error: bool = False) -> None:
        self.requests_total += 1
        self.latencies.append(latency_ms)
        if error:
            self.requests_errors += 1


class WorkerPool:
    def __init__(self, name: str, max_workers: int):
        self.name = name
        self.max_workers = max_workers
        self._semaphore = asyncio.Semaphore(max_workers)
        self._active_count = 0
        self._lock = asyncio.Lock()
        self.metrics = PoolMetrics()

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def utilization(self) -> float:
        """Current utilization as a ratio 0.0 to 1.0."""
        return self._active_count / self.max_workers

    @property
    def available(self) -> int:
        return self.max_workers - self._active_count

    def health_score(self) -> float:
        """
        Composite health score from 0.0 (unhealthy) to 1.0 (healthy).
        Factors: utilization (40%), error rate (30%), latency (30%).
        """
        util_score = 1.0 - self.utilization
        error_score = 1.0 - min(self.metrics.error_rate * 10, 1.0)  # 10% error = 0 score
        # Latency score: <100ms=1.0, >1000ms=0.0, linear between
        latency = self.metrics.avg_latency_ms
        latency_score = max(0.0, 1.0 - (latency - 100) / 900) if latency > 100 else 1.0

        return 0.4 * util_score + 0.3 * error_score + 0.3 * latency_score

    async def acquire(self) -> bool:
        """Try to acquire a worker slot. Returns False if pool is full."""
        acquired = self._semaphore._value > 0
        if not acquired:
            return False
        await self._semaphore.acquire()
        async with self._lock:
            self._active_count += 1
        return True

    async def release(self, latency_ms: float = 0.0, error: bool = False) -> None:
        """Release a worker slot and record metrics."""
        async with self._lock:
            self._active_count = max(0, self._active_count - 1)
        self._semaphore.release()
        self.metrics.record_request(latency_ms, error)

    def status(self) -> dict:
        return {
            "name": self.name,
            "max_workers": self.max_workers,
            "active": self._active_count,
            "available": self.available,
            "utilization": round(self.utilization, 3),
            "health_score": round(self.health_score(), 3),
            "avg_latency_ms": round(self.metrics.avg_latency_ms, 2),
            "error_rate": round(self.metrics.error_rate, 4),
            "total_requests": self.metrics.requests_total,
        }


class PoolManager:
    """Manages all worker pools."""

    def __init__(self, priority_size: int, general_size: int, overflow_size: int):
        self.priority = WorkerPool("priority", priority_size)
        self.general = WorkerPool("general", general_size)
        self.overflow = WorkerPool("overflow", overflow_size)

    def all_pools(self) -> list[WorkerPool]:
        return [self.priority, self.general, self.overflow]

    def status(self) -> dict:
        return {pool.name: pool.status() for pool in self.all_pools()}
