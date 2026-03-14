"""Tests for load balancer, worker pools, and tier-aware routing."""

import pytest
import pytest_asyncio

from app.balancer.pools import PoolManager, WorkerPool
from app.balancer.router import TierRouter
from app.models.user import Tier


class TestWorkerPool:
    @pytest.mark.asyncio
    async def test_acquire_and_release(self):
        pool = WorkerPool("test", max_workers=5)
        assert pool.available == 5

        acquired = await pool.acquire()
        assert acquired is True
        assert pool.active_count == 1
        assert pool.available == 4

        await pool.release(latency_ms=10)
        assert pool.active_count == 0
        assert pool.available == 5

    @pytest.mark.asyncio
    async def test_utilization(self):
        pool = WorkerPool("test", max_workers=10)
        assert pool.utilization == 0.0

        for _ in range(5):
            await pool.acquire()
        assert pool.utilization == 0.5

    @pytest.mark.asyncio
    async def test_health_score_healthy(self):
        pool = WorkerPool("test", max_workers=10)
        score = pool.health_score()
        # Healthy pool: low utilization, no errors, no latency
        assert score >= 0.9

    @pytest.mark.asyncio
    async def test_health_score_degraded(self):
        pool = WorkerPool("test", max_workers=10)
        # Simulate high utilization
        for _ in range(9):
            await pool.acquire()
        score = pool.health_score()
        # High utilization should lower health (util=0.9 → util_score=0.1)
        # But error_rate=0 and latency=0 are still perfect, so score ~0.64
        assert score < 0.7

    @pytest.mark.asyncio
    async def test_metrics_tracking(self):
        pool = WorkerPool("test", max_workers=5)
        await pool.acquire()
        await pool.release(latency_ms=100)
        await pool.acquire()
        await pool.release(latency_ms=200, error=True)

        assert pool.metrics.requests_total == 2
        assert pool.metrics.requests_errors == 1
        assert pool.metrics.avg_latency_ms == 150.0

    def test_pool_status(self):
        pool = WorkerPool("test", max_workers=5)
        status = pool.status()
        assert status["name"] == "test"
        assert status["max_workers"] == 5
        assert status["available"] == 5


class TestTierRouter:
    def setup_method(self):
        self.pm = PoolManager(priority_size=5, general_size=10, overflow_size=5)
        self.router = TierRouter(self.pm)

    def test_enterprise_routes_to_priority(self):
        result = self.router.route(Tier.ENTERPRISE)
        assert result.pool == self.pm.priority
        assert result.shed is False

    def test_premium_routes_to_general(self):
        result = self.router.route(Tier.PREMIUM)
        assert result.pool == self.pm.general
        assert result.shed is False

    def test_free_routes_to_general(self):
        result = self.router.route(Tier.FREE)
        assert result.pool == self.pm.general
        assert result.shed is False

    @pytest.mark.asyncio
    async def test_premium_falls_to_overflow_under_load(self):
        """Premium should use overflow when general is >80% full."""
        # Fill general to >80%
        for _ in range(9):  # 9/10 = 90%
            await self.pm.general.acquire()

        result = self.router.route(Tier.PREMIUM)
        assert result.pool == self.pm.overflow
        assert result.shed is False

    @pytest.mark.asyncio
    async def test_free_falls_to_overflow_under_load(self):
        """Free should use overflow when general is >60% full."""
        for _ in range(7):  # 7/10 = 70%
            await self.pm.general.acquire()

        result = self.router.route(Tier.FREE)
        assert result.pool == self.pm.overflow
        assert result.shed is False

    @pytest.mark.asyncio
    async def test_enterprise_never_shed(self):
        """Enterprise should never be shed, even under extreme load."""
        # Fill all pools
        for _ in range(5):
            await self.pm.priority.acquire()
        for _ in range(10):
            await self.pm.general.acquire()
        for _ in range(5):
            await self.pm.overflow.acquire()

        # Enterprise falls back to general, never shed
        result = self.router.route(Tier.ENTERPRISE)
        assert result.shed is False
        assert result.pool is not None

    @pytest.mark.asyncio
    async def test_free_shed_under_extreme_load(self):
        """Free tier should eventually be shed when overflow is full."""
        # Fill general to >60%
        for _ in range(7):
            await self.pm.general.acquire()
        # Fill overflow to 100%
        for _ in range(5):
            await self.pm.overflow.acquire()

        # With overflow at 100%, free requests should be shed
        shed_count = 0
        for _ in range(100):
            result = self.router.route(Tier.FREE)
            if result.shed:
                shed_count += 1

        # At 100% overflow utilization, shed probability should be 1.0
        assert shed_count == 100

    @pytest.mark.asyncio
    async def test_premium_never_shed(self):
        """Premium should never be shed."""
        for _ in range(10):
            await self.pm.general.acquire()
        for _ in range(5):
            await self.pm.overflow.acquire()

        # Even with full pools, premium is not shed (just queued)
        result = self.router.route(Tier.PREMIUM)
        assert result.shed is False


class TestPoolManager:
    def test_all_pools_created(self):
        pm = PoolManager(priority_size=10, general_size=20, overflow_size=15)
        assert len(pm.all_pools()) == 3
        assert pm.priority.max_workers == 10
        assert pm.general.max_workers == 20
        assert pm.overflow.max_workers == 15

    def test_status(self):
        pm = PoolManager(priority_size=5, general_size=10, overflow_size=5)
        status = pm.status()
        assert "priority" in status
        assert "general" in status
        assert "overflow" in status
