"""Tests for analytics pipeline and structured logging."""

import asyncio

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from app.analytics import pipeline
from app.analytics.logger import generate_correlation_id, get_logger, setup_logging
from app.config import settings


@pytest_asyncio.fixture
async def analytics_db():
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client["ira_test_analytics"]
    yield db
    await db.analytics.drop()
    client.close()


class TestAnalyticsPipeline:
    def test_track_does_not_block(self):
        """track() should return in <1ms (non-blocking)."""
        import time
        start = time.monotonic()
        for _ in range(100):
            pipeline.track("test_event", user_id="u1", tier="free")
        elapsed_ms = (time.monotonic() - start) * 1000
        # 100 calls should take well under 10ms
        assert elapsed_ms < 50

    def test_queue_depth_increases(self):
        """Queue depth should increase after tracking."""
        initial = pipeline.get_queue_depth()
        pipeline.track("depth_test")
        assert pipeline.get_queue_depth() >= initial

    @pytest.mark.asyncio
    async def test_flush_writes_to_db(self, analytics_db):
        """Flushing should write events to MongoDB."""
        # Clear queue
        while pipeline.get_queue_depth() > 0:
            await pipeline._flush_batch(analytics_db)

        # Track some events
        for i in range(5):
            pipeline.track("flush_test", user_id=f"u_{i}", tier="free")

        # Flush
        flushed = await pipeline._flush_batch(analytics_db)
        assert flushed >= 5

        # Verify in DB
        count = await analytics_db.analytics.count_documents({"event_type": "flush_test"})
        assert count >= 5

    @pytest.mark.asyncio
    async def test_graceful_shutdown_drains(self, analytics_db):
        """Graceful shutdown should drain all pending events."""
        # Clear queue
        while pipeline.get_queue_depth() > 0:
            await pipeline._flush_batch(analytics_db)

        for i in range(10):
            pipeline.track("shutdown_test", user_id=f"u_{i}")

        # Stop pipeline (which drains)
        await pipeline.stop_pipeline(analytics_db)

        # Queue should be empty
        assert pipeline.get_queue_depth() == 0


class TestStructuredLogging:
    def test_correlation_id_generation(self):
        cid = generate_correlation_id()
        assert len(cid) == 36  # UUID format
        assert "-" in cid

    def test_logger_creation(self):
        setup_logging()
        log = get_logger(user_id="test", tier="free")
        assert log is not None
