"""
Redis-based sliding window rate limiter with per-tier limits.

Uses Redis sorted sets for precise sliding window counting.
Each request is scored by timestamp; expired entries are pruned on check.
"""

import time
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.config import settings
from app.models.user import Tier

TIER_LIMITS: dict[str, dict[str, int]] = {
    Tier.FREE: {"per_min": settings.rate_limit_free_per_min, "per_day": settings.rate_limit_free_per_day},
    Tier.PREMIUM: {"per_min": settings.rate_limit_premium_per_min, "per_day": settings.rate_limit_premium_per_day},
    Tier.ENTERPRISE: {"per_min": settings.rate_limit_enterprise_per_min, "per_day": settings.rate_limit_enterprise_per_day},
}


@dataclass
class RateLimitResult:
    allowed: bool
    remaining_min: int
    remaining_day: int
    limit_min: int
    limit_day: int
    reset_at: float  # Unix timestamp when minute window resets
    is_first_limit: bool  # True if this is the first time user hit limit in window


async def check_rate_limit(
    redis_client: aioredis.Redis,
    user_id: str,
    tier: str,
) -> RateLimitResult:
    """
    Check if a user is within rate limits using sliding window.

    Uses Redis sorted sets:
    - Key: ratelimit:{user_id}:min  (1-minute window)
    - Key: ratelimit:{user_id}:day  (1-day window)
    - Score: timestamp of each request
    """
    limits = TIER_LIMITS.get(tier, TIER_LIMITS[Tier.FREE])
    now = time.time()
    min_key = f"ratelimit:{user_id}:min"
    day_key = f"ratelimit:{user_id}:day"
    notified_key = f"ratelimit:notified:{user_id}"

    pipe = redis_client.pipeline(transaction=True)

    # Remove expired entries from minute window
    pipe.zremrangebyscore(min_key, 0, now - 60)
    # Remove expired entries from day window
    pipe.zremrangebyscore(day_key, 0, now - 86400)
    # Count current entries
    pipe.zcard(min_key)
    pipe.zcard(day_key)
    # Check if user was already notified
    pipe.exists(notified_key)

    results = await pipe.execute()
    count_min = results[2]
    count_day = results[3]
    already_notified = results[4]

    limit_min = limits["per_min"]
    limit_day = limits["per_day"]

    # Check limits (0 = unlimited for daily)
    min_exceeded = count_min >= limit_min
    day_exceeded = limit_day > 0 and count_day >= limit_day

    if min_exceeded or day_exceeded:
        is_first = not already_notified
        if is_first:
            # Mark as notified for 5 minutes (anti-spam)
            await redis_client.setex(notified_key, 300, "1")

        return RateLimitResult(
            allowed=False,
            remaining_min=max(0, limit_min - count_min),
            remaining_day=max(0, limit_day - count_day) if limit_day > 0 else -1,
            limit_min=limit_min,
            limit_day=limit_day,
            reset_at=now + 60,
            is_first_limit=is_first,
        )

    # Record this request in both windows
    pipe2 = redis_client.pipeline(transaction=True)
    pipe2.zadd(min_key, {f"{now}": now})
    pipe2.expire(min_key, 120)  # TTL slightly longer than window
    pipe2.zadd(day_key, {f"{now}": now})
    pipe2.expire(day_key, 90000)
    await pipe2.execute()

    return RateLimitResult(
        allowed=True,
        remaining_min=limit_min - count_min - 1,
        remaining_day=(limit_day - count_day - 1) if limit_day > 0 else -1,
        limit_min=limit_min,
        limit_day=limit_day,
        reset_at=now + 60,
        is_first_limit=False,
    )
