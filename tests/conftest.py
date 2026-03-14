"""
Test fixtures for Ira backend tests.
Uses separate test database to avoid affecting production data.
"""

import asyncio
from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.db.indexes import create_indexes
from app.main import app


TEST_DB_NAME = "ira_test"


@pytest_asyncio.fixture
async def mongo_client():
    client = AsyncIOMotorClient(settings.mongo_uri)
    yield client
    client.close()


@pytest_asyncio.fixture
async def test_db(mongo_client):
    db = mongo_client[TEST_DB_NAME]
    await create_indexes(db)
    yield db
    # Cleanup after each test
    for coll in ["users", "personalities", "sessions", "messages", "analytics"]:
        await db[coll].drop()


@pytest_asyncio.fixture
async def redis_client():
    import redis.asyncio as aioredis
    client = aioredis.from_url(settings.redis_uri, decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def sample_users(test_db):
    """Create one user per tier for testing."""
    users = [
        {
            "external_id": "test_free_user",
            "phone": "+10000000001",
            "display_name": "Free User",
            "tier": "free",
            "platform": "app",
            "language": "en",
            "timezone": "UTC",
            "created_at": datetime.utcnow(),
            "last_active_at": datetime.utcnow(),
            "metadata": {},
        },
        {
            "external_id": "test_premium_user",
            "phone": "+10000000002",
            "display_name": "Premium User",
            "tier": "premium",
            "platform": "whatsapp",
            "language": "en",
            "timezone": "UTC",
            "created_at": datetime.utcnow(),
            "last_active_at": datetime.utcnow(),
            "metadata": {},
        },
        {
            "external_id": "test_enterprise_user",
            "phone": "+10000000003",
            "display_name": "Enterprise User",
            "tier": "enterprise",
            "platform": "app",
            "language": "en",
            "timezone": "UTC",
            "created_at": datetime.utcnow(),
            "last_active_at": datetime.utcnow(),
            "metadata": {},
        },
    ]
    await test_db.users.insert_many(users)

    # Create personalities for each user
    personalities = [
        {
            "user_id": "test_free_user",
            "tone": "friendly",
            "verbosity": "normal",
            "humor_level": 5,
            "formality": 5,
            "interests": ["technology"],
            "custom_instructions": "",
            "updated_at": datetime.utcnow(),
        },
        {
            "user_id": "test_premium_user",
            "tone": "professional",
            "verbosity": "detailed",
            "humor_level": 3,
            "formality": 8,
            "interests": ["business"],
            "custom_instructions": "",
            "updated_at": datetime.utcnow(),
        },
        {
            "user_id": "test_enterprise_user",
            "tone": "empathetic",
            "verbosity": "normal",
            "humor_level": 6,
            "formality": 6,
            "interests": ["leadership"],
            "custom_instructions": "",
            "updated_at": datetime.utcnow(),
        },
    ]
    await test_db.personalities.insert_many(personalities)
    return users


@pytest_asyncio.fixture
async def sample_session(test_db, sample_users):
    """Create a sample active session."""
    session = {
        "user_id": "test_free_user",
        "started_at": datetime.utcnow(),
        "ended_at": None,
        "is_active": True,
        "message_count": 0,
        "platform": "app",
        "context_summary": "",
        "metadata": {},
    }
    result = await test_db.sessions.insert_one(session)
    session["_id"] = result.inserted_id
    return session


@pytest_asyncio.fixture
async def client(test_db, redis_client):
    """AsyncClient connected to the FastAPI app with test DB."""
    # Override the app's DB and Redis for testing
    from app.balancer.pools import PoolManager
    from app.balancer.router import TierRouter
    from app.balancer.health import PoolHealthTracker
    from app.workers.processor import MessageProcessor

    pool_manager = PoolManager(
        priority_size=settings.worker_pool_priority_size,
        general_size=settings.worker_pool_general_size,
        overflow_size=settings.worker_pool_overflow_size,
    )
    health_tracker = PoolHealthTracker(pool_manager)
    router = TierRouter(pool_manager)
    processor = MessageProcessor(test_db, redis_client, router)

    app.state.db = test_db
    app.state.redis = redis_client
    app.state.pool_manager = pool_manager
    app.state.health_tracker = health_tracker
    app.state.router = router
    app.state.processor = processor

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
