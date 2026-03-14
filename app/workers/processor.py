"""
Message processing pipeline.

Orchestrates the full request lifecycle:
1. Route to worker pool
2. Fetch user + personality
3. Fetch/create active session
4. Safety check
5. Rate limit check
6. Generate response (simulated LLM)
7. Store messages
8. Track analytics
"""

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from motor.motor_asyncio import AsyncIOMotorDatabase
import redis.asyncio as aioredis

from app.analytics import pipeline as analytics
from app.analytics.logger import get_logger
from app.analytics.slow_op import track_slow
from app.balancer.pools import WorkerPool
from app.balancer.router import TierRouter, RoutingResult
from app.db.queries import get_active_session, get_recent_messages, get_user_with_personality
from app.models.user import Tier
from app.ratelimit.limiter import check_rate_limit
from app.ratelimit.responses import get_rate_limit_response
from app.safety.filter import check_safety
from app.safety.responses import get_safety_response

# Simulated LLM latency by tier (ms) — enterprise gets faster responses
LLM_LATENCY: dict[str, tuple[int, int]] = {
    Tier.FREE: (200, 500),
    Tier.PREMIUM: (100, 300),
    Tier.ENTERPRISE: (50, 150),
}

SESSION_INACTIVITY_THRESHOLD = timedelta(minutes=30)


@dataclass
class ChatRequest:
    user_id: str
    content: str
    platform: str = "app"
    correlation_id: str = ""


@dataclass
class ChatResponse:
    message: str
    session_id: str
    processing_time_ms: float
    rate_limited: bool = False
    safety_blocked: bool = False
    shed: bool = False
    metadata: dict = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# Graceful degradation messages for load shedding
SHED_MESSAGES = {
    "friendly": "I'm getting a lot of love right now! Give me just a moment and try again soon.",
    "professional": "Our service is experiencing high demand. Please try again in a moment.",
    "casual": "Yikes, things are busy! Try me again in a sec?",
    "empathetic": "I wish I could be here for everyone at once. Please try again in just a moment — I want to give you my full attention.",
}


class MessageProcessor:
    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        redis_client: aioredis.Redis,
        router: TierRouter,
    ):
        self.db = db
        self.redis = redis_client
        self.router = router

    @track_slow(threshold_ms=500)
    async def process(self, request: ChatRequest) -> ChatResponse:
        """Process a chat message through the full pipeline."""
        start = time.monotonic()
        log = get_logger(user_id=request.user_id, correlation_id=request.correlation_id)

        # 1. Fetch user with personality
        user_data = await get_user_with_personality(self.db, request.user_id)
        if not user_data:
            return ChatResponse(
                message="I don't seem to recognize you. Please check your account.",
                session_id="",
                processing_time_ms=self._elapsed(start),
            )

        tier = user_data.get("tier", Tier.FREE)
        personality = user_data.get("personality", {})
        tone = personality.get("tone", "friendly")

        # 2. Route to worker pool
        routing: RoutingResult = self.router.route(tier, request.correlation_id)

        if routing.shed:
            log.warning("request_shed")
            return ChatResponse(
                message=SHED_MESSAGES.get(tone, SHED_MESSAGES["friendly"]),
                session_id="",
                processing_time_ms=self._elapsed(start),
                shed=True,
            )

        pool: WorkerPool = routing.pool
        acquired = await pool.acquire()
        if not acquired:
            # Pool full — wait briefly then try
            try:
                await asyncio.wait_for(self._wait_and_acquire(pool), timeout=2.0)
                acquired = True
            except asyncio.TimeoutError:
                log.warning("pool_acquire_timeout", pool=pool.name)
                return ChatResponse(
                    message=SHED_MESSAGES.get(tone, SHED_MESSAGES["friendly"]),
                    session_id="",
                    processing_time_ms=self._elapsed(start),
                    shed=True,
                )

        try:
            return await self._process_with_worker(request, user_data, tier, tone, pool, start)
        except Exception as e:
            log.error("processing_error", error=str(e))
            elapsed = self._elapsed(start)
            await pool.release(latency_ms=elapsed, error=True)
            analytics.track("error", user_id=request.user_id, tier=tier, correlation_id=request.correlation_id)
            return ChatResponse(
                message="I got a bit confused there. Could you try saying that again?",
                session_id="",
                processing_time_ms=elapsed,
            )

    async def _wait_and_acquire(self, pool: WorkerPool) -> None:
        """Wait for a slot in the pool."""
        while not await pool.acquire():
            await asyncio.sleep(0.05)

    async def _process_with_worker(
        self,
        request: ChatRequest,
        user_data: dict,
        tier: str,
        tone: str,
        pool: WorkerPool,
        start: float,
    ) -> ChatResponse:
        """Core processing after acquiring a worker slot."""
        log = get_logger(user_id=request.user_id, tier=tier, pool=pool.name)

        # 3. Safety check (before anything else)
        safety_result = check_safety(request.content)
        if not safety_result.safe:
            log.info("safety_blocked", category=safety_result.category)
            analytics.track(
                "safety_blocked",
                user_id=request.user_id,
                tier=tier,
                correlation_id=request.correlation_id,
                extra={"category": safety_result.category},
            )
            response_msg = get_safety_response(tone, safety_result.category)
            elapsed = self._elapsed(start)
            await pool.release(latency_ms=elapsed)

            # Store the blocked interaction
            session = await self._get_or_create_session(request.user_id, request.platform)
            session_id = str(session["_id"])
            await self._store_message(request, session_id, safety_flagged=True)
            await self._store_assistant_message(session_id, request.user_id, response_msg)

            return ChatResponse(
                message=response_msg,
                session_id=session_id,
                processing_time_ms=elapsed,
                safety_blocked=True,
            )

        # 4. Rate limit check
        rl_result = await check_rate_limit(self.redis, request.user_id, tier)
        if not rl_result.allowed:
            log.info("rate_limited", is_first=rl_result.is_first_limit)
            analytics.track(
                "rate_limited",
                user_id=request.user_id,
                tier=tier,
                correlation_id=request.correlation_id,
            )
            elapsed = self._elapsed(start)
            await pool.release(latency_ms=elapsed)

            response_msg = get_rate_limit_response(tone, rl_result.is_first_limit)
            session = await self._get_or_create_session(request.user_id, request.platform)
            session_id = str(session["_id"])

            if response_msg:
                await self._store_message(request, session_id, rate_limited=True)
                await self._store_assistant_message(session_id, request.user_id, response_msg)

            return ChatResponse(
                message=response_msg or "",
                session_id=session_id,
                processing_time_ms=elapsed,
                rate_limited=True,
                metadata={"retry_after": int(rl_result.reset_at - time.time())},
            )

        # 5. Get/create session and fetch context
        session = await self._get_or_create_session(request.user_id, request.platform)
        session_id = str(session["_id"])
        recent_messages = await get_recent_messages(self.db, session_id, limit=20)

        # 6. Store user message
        await self._store_message(request, session_id)

        # 7. Generate response (simulated LLM call)
        response_text = await self._generate_response(
            request.content, recent_messages, user_data, tier
        )

        # 8. Store assistant response
        elapsed = self._elapsed(start)
        await self._store_assistant_message(session_id, request.user_id, response_text, elapsed)

        # 9. Update session message count
        await self.db.sessions.update_one(
            {"_id": session["_id"]},
            {"$inc": {"message_count": 2}},  # user + assistant
        )

        # 10. Update user last_active
        await self.db.users.update_one(
            {"external_id": request.user_id},
            {"$set": {"last_active_at": datetime.utcnow()}},
        )

        # Release worker
        await pool.release(latency_ms=elapsed)

        analytics.track(
            "message_processed",
            user_id=request.user_id,
            tier=tier,
            duration_ms=elapsed,
            correlation_id=request.correlation_id,
        )

        return ChatResponse(
            message=response_text,
            session_id=session_id,
            processing_time_ms=elapsed,
        )

    async def _get_or_create_session(self, user_id: str, platform: str) -> dict:
        """Get active session or create a new one."""
        session = await get_active_session(self.db, user_id)
        if session:
            return session

        # Create new session
        doc = {
            "user_id": user_id,
            "started_at": datetime.utcnow(),
            "ended_at": None,
            "is_active": True,
            "message_count": 0,
            "platform": platform,
            "context_summary": "",
            "metadata": {},
        }
        result = await self.db.sessions.insert_one(doc)
        doc["_id"] = result.inserted_id
        return doc

    async def _store_message(
        self,
        request: ChatRequest,
        session_id: str,
        safety_flagged: bool = False,
        rate_limited: bool = False,
    ) -> None:
        await self.db.messages.insert_one({
            "session_id": session_id,
            "user_id": request.user_id,
            "role": "user",
            "content": request.content,
            "created_at": datetime.utcnow(),
            "processing_time_ms": 0,
            "tokens_used": 0,
            "safety_flagged": safety_flagged,
            "rate_limited": rate_limited,
            "metadata": {},
        })

    async def _store_assistant_message(
        self,
        session_id: str,
        user_id: str,
        content: str,
        processing_time_ms: float = 0,
    ) -> None:
        await self.db.messages.insert_one({
            "session_id": session_id,
            "user_id": user_id,
            "role": "assistant",
            "content": content,
            "created_at": datetime.utcnow(),
            "processing_time_ms": processing_time_ms,
            "tokens_used": len(content.split()) * 2,  # rough estimate
            "safety_flagged": False,
            "rate_limited": False,
            "metadata": {},
        })

    async def _generate_response(
        self,
        user_message: str,
        context: list[dict],
        user_data: dict,
        tier: str,
    ) -> str:
        """
        Simulated LLM response generation.
        In production, this would call an actual LLM API.
        Tier-based latency: enterprise fastest, free slowest.
        """
        latency_range = LLM_LATENCY.get(tier, LLM_LATENCY[Tier.FREE])
        latency_ms = random.randint(*latency_range)
        await asyncio.sleep(latency_ms / 1000)

        personality = user_data.get("personality", {})
        tone = personality.get("tone", "friendly")

        # Simple response generation (simulated)
        responses = {
            "friendly": f"That's a great thought! I'd love to explore that with you. You mentioned: '{user_message[:50]}...'",
            "professional": f"Thank you for your message. Regarding your query about '{user_message[:50]}...', here are my thoughts.",
            "casual": f"Oh cool! So about '{user_message[:50]}...' — here's what I think!",
            "empathetic": f"I really appreciate you sharing that with me. About '{user_message[:50]}...', I think...",
        }
        return responses.get(tone, responses["friendly"])

    def _elapsed(self, start: float) -> float:
        return round((time.monotonic() - start) * 1000, 2)
