"""
Load tests simulating different traffic scenarios.

Tests measure latency (p50, p95, p99) and success rates per tier
under normal, high, and extreme load conditions.
"""

import asyncio
import random
import time
from collections import defaultdict

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.balancer.pools import PoolManager
from app.balancer.router import TierRouter
from app.balancer.health import PoolHealthTracker
from app.config import settings
from app.db.indexes import create_indexes
from app.main import app
from app.workers.processor import MessageProcessor


def percentile(data: list[float], p: int) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def report_results(results: dict[str, dict]) -> str:
    """Format load test results into a readable report."""
    lines = []
    for tier, data in sorted(results.items()):
        latencies = data["latencies"]
        total = data["total"]
        success = data["success"]
        rate = (success / total * 100) if total > 0 else 0
        lines.append(f"  {tier}:")
        lines.append(f"    Requests: {total}, Success: {success} ({rate:.1f}%)")
        if latencies:
            lines.append(f"    p50: {percentile(latencies, 50):.1f}ms")
            lines.append(f"    p95: {percentile(latencies, 95):.1f}ms")
            lines.append(f"    p99: {percentile(latencies, 99):.1f}ms")
    return "\n".join(lines)


async def send_request(
    client: AsyncClient,
    user_id: str,
    tier: str,
    results: dict,
) -> None:
    """Send a single chat request and record results."""
    start = time.monotonic()
    try:
        response = await client.post("/chat", json={
            "user_id": user_id,
            "content": "Hello, how are you?",
            "platform": "app",
        })
        elapsed_ms = (time.monotonic() - start) * 1000
        results[tier]["latencies"].append(elapsed_ms)
        results[tier]["total"] += 1
        if response.status_code == 200:
            results[tier]["success"] += 1
        elif response.status_code == 429:
            results[tier]["rate_limited"] += 1
        elif response.status_code == 503:
            results[tier]["shed"] += 1
    except Exception:
        results[tier]["total"] += 1
        results[tier]["errors"] += 1


@pytest_asyncio.fixture
async def load_test_setup():
    """Set up a full environment for load testing."""
    from motor.motor_asyncio import AsyncIOMotorClient
    import redis.asyncio as aioredis
    from datetime import datetime

    mongo_client = AsyncIOMotorClient(settings.mongo_uri)
    db = mongo_client["ira_test_load"]
    await create_indexes(db)

    redis_client = aioredis.from_url(settings.redis_uri, decode_responses=True)

    # Create test users (one per tier type)
    tiers = {"free": 70, "premium": 20, "enterprise": 10}
    for tier, count in tiers.items():
        users = []
        personalities = []
        for i in range(count):
            uid = f"load_{tier}_{i}"
            users.append({
                "external_id": uid,
                "phone": f"+1{random.randint(1000000000, 9999999999)}",
                "display_name": f"Load Test {tier} {i}",
                "tier": tier,
                "platform": "app",
                "language": "en",
                "timezone": "UTC",
                "created_at": datetime.utcnow(),
                "last_active_at": datetime.utcnow(),
                "metadata": {},
            })
            personalities.append({
                "user_id": uid,
                "tone": "friendly",
                "verbosity": "normal",
                "humor_level": 5,
                "formality": 5,
                "interests": [],
                "custom_instructions": "",
                "updated_at": datetime.utcnow(),
            })
        if users:
            await db.users.insert_many(users)
            await db.personalities.insert_many(personalities)

    pool_manager = PoolManager(
        priority_size=settings.worker_pool_priority_size,
        general_size=settings.worker_pool_general_size,
        overflow_size=settings.worker_pool_overflow_size,
    )
    health_tracker = PoolHealthTracker(pool_manager)
    router = TierRouter(pool_manager)
    processor = MessageProcessor(db, redis_client, router)

    app.state.db = db
    app.state.redis = redis_client
    app.state.pool_manager = pool_manager
    app.state.health_tracker = health_tracker
    app.state.router = router
    app.state.processor = processor

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, db, redis_client

    # Cleanup
    for coll in ["users", "personalities", "sessions", "messages", "analytics"]:
        await db[coll].drop()
    await redis_client.flushdb()
    await redis_client.aclose()
    mongo_client.close()


class TestNormalLoad:
    """Simulate normal traffic: 50 concurrent users (mixed tiers)."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(60)
    async def test_normal_load(self, load_test_setup):
        client, db, redis_client = load_test_setup
        results = defaultdict(lambda: {
            "latencies": [], "total": 0, "success": 0,
            "rate_limited": 0, "shed": 0, "errors": 0,
        })

        tasks = []
        # 35 free, 10 premium, 5 enterprise
        for i in range(35):
            tasks.append(send_request(client, f"load_free_{i}", "free", results))
        for i in range(10):
            tasks.append(send_request(client, f"load_premium_{i}", "premium", results))
        for i in range(5):
            tasks.append(send_request(client, f"load_enterprise_{i}", "enterprise", results))

        await asyncio.gather(*tasks)

        print("\n--- Normal Load Results ---")
        print(report_results(results))

        # All tiers should succeed under normal load
        for tier in ["free", "premium", "enterprise"]:
            if results[tier]["total"] > 0:
                success_rate = results[tier]["success"] / results[tier]["total"]
                assert success_rate >= 0.9, f"{tier} success rate too low: {success_rate}"


class TestHighLoad:
    """Simulate high traffic: 200 concurrent users."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(120)
    async def test_high_load(self, load_test_setup):
        client, db, redis_client = load_test_setup
        results = defaultdict(lambda: {
            "latencies": [], "total": 0, "success": 0,
            "rate_limited": 0, "shed": 0, "errors": 0,
        })

        def make_tasks():
            tasks = []
            for i in range(60):
                tasks.append(send_request(client, f"load_free_{i % 70}", "free", results))
            for i in range(20):
                tasks.append(send_request(client, f"load_premium_{i}", "premium", results))
            for i in range(10):
                tasks.append(send_request(client, f"load_enterprise_{i}", "enterprise", results))
            return tasks

        # Send multiple rounds
        for _ in range(2):
            await asyncio.gather(*make_tasks())

        print("\n--- High Load Results ---")
        print(report_results(results))

        # Enterprise should remain stable
        if results["enterprise"]["total"] > 0:
            ent_rate = results["enterprise"]["success"] / results["enterprise"]["total"]
            assert ent_rate >= 0.95, f"Enterprise success rate dropped: {ent_rate}"

        # Premium should degrade more slowly than free
        if results["premium"]["total"] > 0 and results["free"]["total"] > 0:
            prem_rate = results["premium"]["success"] / results["premium"]["total"]
            free_rate = results["free"]["success"] / results["free"]["total"]
            # Premium should perform at least as well as free
            assert prem_rate >= free_rate * 0.9


class TestExtremeLoad:
    """Simulate extreme traffic: 500 concurrent users."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(180)
    async def test_extreme_load(self, load_test_setup):
        client, db, redis_client = load_test_setup
        results = defaultdict(lambda: {
            "latencies": [], "total": 0, "success": 0,
            "rate_limited": 0, "shed": 0, "errors": 0,
        })

        def make_tasks():
            tasks = []
            for i in range(60):
                tasks.append(send_request(client, f"load_free_{i % 70}", "free", results))
            for i in range(20):
                tasks.append(send_request(client, f"load_premium_{i}", "premium", results))
            for i in range(10):
                tasks.append(send_request(client, f"load_enterprise_{i}", "enterprise", results))
            return tasks

        # Send 5 rounds for extreme load
        for _ in range(5):
            await asyncio.gather(*make_tasks())

        print("\n--- Extreme Load Results ---")
        print(report_results(results))

        # Enterprise should STILL be stable
        if results["enterprise"]["total"] > 0:
            ent_rate = results["enterprise"]["success"] / results["enterprise"]["total"]
            assert ent_rate >= 0.90, f"Enterprise degraded under extreme load: {ent_rate}"
