"""
FastAPI middleware for rate limiting.
Injects rate limit check into the request pipeline.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.analytics import pipeline as analytics
from app.analytics.logger import get_logger
from app.db.redis import get_redis
from app.ratelimit.limiter import check_rate_limit


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only rate-limit the chat endpoint
        if request.url.path != "/chat":
            return await call_next(request)

        # Extract user info from request state (set by auth/routing)
        user_id = getattr(request.state, "user_id", None)
        tier = getattr(request.state, "tier", None)

        if not user_id or not tier:
            return await call_next(request)

        redis_client = await get_redis()
        result = await check_rate_limit(redis_client, user_id, tier)

        if not result.allowed:
            log = get_logger(user_id=user_id, tier=tier)
            log.info("rate_limited", is_first=result.is_first_limit)
            analytics.track(
                "rate_limited",
                user_id=user_id,
                tier=tier,
            )

            # Store rate limit result in state for the route handler
            # to generate personality-aware response
            request.state.rate_limited = True
            request.state.rate_limit_result = result

        request.state.rate_limited = getattr(request.state, "rate_limited", False)
        return await call_next(request)
