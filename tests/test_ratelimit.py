"""Tests for rate limiting behavior."""

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from app.config import settings
from app.ratelimit.limiter import check_rate_limit
from app.ratelimit.responses import get_rate_limit_response


@pytest_asyncio.fixture
async def redis():
    client = aioredis.from_url(settings.redis_uri, decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_within_limit(self, redis):
        result = await check_rate_limit(redis, "user_1", "free")
        assert result.allowed is True
        assert result.remaining_min == settings.rate_limit_free_per_min - 1

    @pytest.mark.asyncio
    async def test_free_tier_limit(self, redis):
        """Free users should be limited after 10 messages/min."""
        for i in range(settings.rate_limit_free_per_min):
            result = await check_rate_limit(redis, "limit_test", "free")
            assert result.allowed is True

        # Next one should be blocked
        result = await check_rate_limit(redis, "limit_test", "free")
        assert result.allowed is False

    @pytest.mark.asyncio
    async def test_premium_has_higher_limit(self, redis):
        """Premium users should have higher limits than free."""
        for i in range(settings.rate_limit_free_per_min + 5):
            result = await check_rate_limit(redis, "premium_test", "premium")
            assert result.allowed is True  # Premium limit is 60, well above free's 10

    @pytest.mark.asyncio
    async def test_enterprise_high_limit(self, redis):
        """Enterprise users should have very high limits."""
        for i in range(50):
            result = await check_rate_limit(redis, "ent_test", "enterprise")
            assert result.allowed is True

    @pytest.mark.asyncio
    async def test_first_limit_flag(self, redis):
        """First rate limit should be flagged for personality message."""
        # Exhaust the limit
        for _ in range(settings.rate_limit_free_per_min):
            await check_rate_limit(redis, "first_test", "free")

        # First limit event
        result = await check_rate_limit(redis, "first_test", "free")
        assert result.allowed is False
        assert result.is_first_limit is True

        # Second limit event — should NOT be first
        result = await check_rate_limit(redis, "first_test", "free")
        assert result.allowed is False
        assert result.is_first_limit is False

    @pytest.mark.asyncio
    async def test_different_users_independent(self, redis):
        """Rate limits should be per-user."""
        for _ in range(settings.rate_limit_free_per_min):
            await check_rate_limit(redis, "user_a", "free")

        result_a = await check_rate_limit(redis, "user_a", "free")
        assert result_a.allowed is False

        result_b = await check_rate_limit(redis, "user_b", "free")
        assert result_b.allowed is True


class TestRateLimitResponses:
    def test_first_limit_returns_message(self):
        msg = get_rate_limit_response("friendly", is_first=True)
        assert msg is not None
        assert len(msg) > 10

    def test_subsequent_limit_returns_none(self):
        msg = get_rate_limit_response("friendly", is_first=False)
        assert msg is None

    def test_all_tones_have_messages(self):
        for tone in ["friendly", "professional", "casual", "empathetic"]:
            msg = get_rate_limit_response(tone, is_first=True)
            assert msg is not None

    def test_messages_not_technical(self):
        """Rate limit messages should not sound technical."""
        msg = get_rate_limit_response("friendly", is_first=True)
        technical_terms = ["429", "rate limit", "exceeded", "quota", "throttled"]
        for term in technical_terms:
            assert term.lower() not in msg.lower()
