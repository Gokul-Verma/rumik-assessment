"""Tests for database queries."""

import pytest
import pytest_asyncio
from datetime import datetime

from app.db.queries import (
    get_active_session,
    get_recent_messages,
    get_user_with_personality,
    get_active_session_with_context,
    aggregate_tier_activity,
)


class TestActiveSessionLookup:
    @pytest.mark.asyncio
    async def test_finds_active_session(self, test_db, sample_users, sample_session):
        session = await get_active_session(test_db, "test_free_user")
        assert session is not None
        assert session["is_active"] is True
        assert session["user_id"] == "test_free_user"

    @pytest.mark.asyncio
    async def test_returns_none_for_no_session(self, test_db, sample_users):
        session = await get_active_session(test_db, "test_premium_user")
        assert session is None

    @pytest.mark.asyncio
    async def test_ignores_inactive_sessions(self, test_db, sample_users):
        await test_db.sessions.insert_one({
            "user_id": "test_premium_user",
            "started_at": datetime.utcnow(),
            "ended_at": datetime.utcnow(),
            "is_active": False,
            "message_count": 5,
            "platform": "app",
            "context_summary": "",
            "metadata": {},
        })
        session = await get_active_session(test_db, "test_premium_user")
        assert session is None


class TestRecentMessages:
    @pytest.mark.asyncio
    async def test_returns_messages_in_order(self, test_db, sample_session):
        session_id = str(sample_session["_id"])
        # Insert messages with varying timestamps
        for i in range(5):
            await test_db.messages.insert_one({
                "session_id": session_id,
                "user_id": "test_free_user",
                "role": "user",
                "content": f"Message {i}",
                "created_at": datetime(2024, 1, 1, 0, i),
                "processing_time_ms": 0,
                "tokens_used": 0,
                "safety_flagged": False,
                "rate_limited": False,
                "metadata": {},
            })

        messages = await get_recent_messages(test_db, session_id, limit=20)
        assert len(messages) == 5
        # Should be in chronological order (oldest first)
        assert messages[0]["content"] == "Message 0"
        assert messages[-1]["content"] == "Message 4"

    @pytest.mark.asyncio
    async def test_respects_limit(self, test_db, sample_session):
        session_id = str(sample_session["_id"])
        for i in range(10):
            await test_db.messages.insert_one({
                "session_id": session_id,
                "user_id": "test_free_user",
                "role": "user",
                "content": f"Message {i}",
                "created_at": datetime(2024, 1, 1, 0, i),
                "processing_time_ms": 0,
                "tokens_used": 0,
                "safety_flagged": False,
                "rate_limited": False,
                "metadata": {},
            })

        messages = await get_recent_messages(test_db, session_id, limit=3)
        assert len(messages) == 3
        # Should be the 3 most recent
        assert messages[-1]["content"] == "Message 9"


class TestUserWithPersonality:
    @pytest.mark.asyncio
    async def test_lookup_returns_personality(self, test_db, sample_users):
        result = await get_user_with_personality(test_db, "test_free_user")
        assert result is not None
        assert result["tier"] == "free"
        assert result["personality"]["tone"] == "friendly"

    @pytest.mark.asyncio
    async def test_lookup_nonexistent_user(self, test_db, sample_users):
        result = await get_user_with_personality(test_db, "nonexistent_user")
        assert result is None


class TestTierAggregation:
    @pytest.mark.asyncio
    async def test_aggregates_by_tier(self, test_db, sample_users):
        result = await aggregate_tier_activity(test_db)
        tiers = {r["_id"] for r in result}
        assert "free" in tiers
        assert "premium" in tiers
        assert "enterprise" in tiers
