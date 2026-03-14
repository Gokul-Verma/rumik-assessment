"""
FastAPI application with lifespan management.

Startup: connect to MongoDB/Redis, create indexes, start background tasks.
Shutdown: drain analytics, stop health tracker, close connections.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.analytics import pipeline as analytics
from app.analytics.logger import get_logger, setup_logging
from app.api.middleware import CorrelationIDMiddleware
from app.api.routes import router
from app.balancer.health import PoolHealthTracker
from app.balancer.pools import PoolManager
from app.balancer.router import TierRouter
from app.config import settings
from app.db.indexes import create_indexes
from app.db.mongo import close_mongo, get_db
from app.db.redis import close_redis, get_redis
from app.workers.processor import MessageProcessor


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    setup_logging()
    log = get_logger()
    log.info("starting_up")

    # Connect to databases
    db = await get_db()
    redis_client = await get_redis()
    log.info("databases_connected")

    # Create indexes
    await create_indexes(db)
    log.info("indexes_created")

    # Initialize worker pools
    pool_manager = PoolManager(
        priority_size=settings.worker_pool_priority_size,
        general_size=settings.worker_pool_general_size,
        overflow_size=settings.worker_pool_overflow_size,
    )

    # Start health tracker
    health_tracker = PoolHealthTracker(pool_manager)
    await health_tracker.start(redis_client)

    # Initialize router and processor
    tier_router = TierRouter(pool_manager)
    processor = MessageProcessor(db, redis_client, tier_router)

    # Start analytics pipeline
    await analytics.start_pipeline(db)

    # Store references in app state
    app.state.db = db
    app.state.redis = redis_client
    app.state.pool_manager = pool_manager
    app.state.health_tracker = health_tracker
    app.state.router = tier_router
    app.state.processor = processor

    log.info("system_ready", pools=pool_manager.status())

    yield

    # --- Shutdown ---
    log.info("shutting_down")

    # Stop health tracker
    await health_tracker.stop()

    # Flush and stop analytics
    await analytics.stop_pipeline(db)
    log.info("analytics_flushed")

    # Close database connections
    await close_redis()
    await close_mongo()
    log.info("shutdown_complete")


app = FastAPI(
    title="Ira AI Companion",
    description="Tier-aware AI companion chatbot backend",
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware (order matters — outermost first)
app.add_middleware(CorrelationIDMiddleware)

# Routes
app.include_router(router)
