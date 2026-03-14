"""
Pool health tracking with rolling window metrics.
Background task samples pool health every second and publishes to Redis.
"""

import asyncio
import json
from collections import deque
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.balancer.pools import PoolManager


@dataclass
class HealthSnapshot:
    name: str
    health_score: float
    utilization: float
    active: int
    available: int


class PoolHealthTracker:
    def __init__(self, pool_manager: PoolManager, window_size: int = 60):
        self.pool_manager = pool_manager
        self.window_size = window_size
        # Rolling window of snapshots per pool
        self.history: dict[str, deque[HealthSnapshot]] = {
            pool.name: deque(maxlen=window_size)
            for pool in pool_manager.all_pools()
        }
        self._task: asyncio.Task | None = None
        self._running = False

    def current_health(self, pool_name: str) -> float:
        """Get current health score for a pool."""
        for pool in self.pool_manager.all_pools():
            if pool.name == pool_name:
                return pool.health_score()
        return 0.0

    def trend(self, pool_name: str) -> str:
        """Get health trend: improving, degrading, or stable."""
        history = self.history.get(pool_name, deque())
        if len(history) < 5:
            return "stable"
        recent = [s.health_score for s in list(history)[-5:]]
        older = [s.health_score for s in list(history)[-10:-5]] if len(history) >= 10 else recent
        avg_recent = sum(recent) / len(recent)
        avg_older = sum(older) / len(older)
        diff = avg_recent - avg_older
        if diff > 0.05:
            return "improving"
        elif diff < -0.05:
            return "degrading"
        return "stable"

    async def _sample(self, redis_client: aioredis.Redis | None = None) -> None:
        """Take a snapshot of all pool health metrics."""
        for pool in self.pool_manager.all_pools():
            snapshot = HealthSnapshot(
                name=pool.name,
                health_score=pool.health_score(),
                utilization=pool.utilization,
                active=pool.active_count,
                available=pool.available,
            )
            self.history[pool.name].append(snapshot)

            # Publish to Redis for multi-node awareness
            if redis_client:
                try:
                    await redis_client.set(
                        f"pool:health:{pool.name}",
                        json.dumps(pool.status()),
                        ex=10,
                    )
                except Exception:
                    pass

    async def _monitor_loop(self, redis_client: aioredis.Redis | None = None) -> None:
        while self._running:
            await self._sample(redis_client)
            await asyncio.sleep(1)

    async def start(self, redis_client: aioredis.Redis | None = None) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop(redis_client))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
