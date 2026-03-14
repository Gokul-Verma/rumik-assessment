"""
Index Strategy
==============

Each index is designed to support specific query patterns:

users:
  - {phone: 1} unique        → Login/auth lookup. Unique constraint prevents duplicates.
  - {external_id: 1} unique  → API-based user lookup. Unique constraint.
  - {tier: 1, last_active_at: -1} → Tier-based aggregation queries. Compound allows
    filtering by tier then sorting by activity without a collection scan.

personalities:
  - {user_id: 1} unique → 1:1 relationship with users. Fast fetch during message
    processing. Unique enforces one personality per user.

sessions:
  - {user_id: 1, is_active: 1} → "Get active session" query. Compound index means
    MongoDB can satisfy {user_id: X, is_active: true} with a single index seek.
    This is a covered query if we only need _id.
  - {user_id: 1, ended_at: -1} → Session history sorted by recency. Supports
    "last N sessions" pagination queries.

messages:
  - {session_id: 1, created_at: -1} → Primary query: fetch recent messages in a
    session for LLM context window. Index supports sort without in-memory sort.
  - {user_id: 1, created_at: -1} → Cross-session message history for a user.
    Supports analytics and user history queries.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel


async def create_indexes(db: AsyncIOMotorDatabase) -> None:
    # Users
    await db.users.create_indexes([
        IndexModel([("phone", ASCENDING)], unique=True),
        IndexModel([("external_id", ASCENDING)], unique=True),
        IndexModel([("tier", ASCENDING), ("last_active_at", DESCENDING)]),
    ])

    # Personalities
    await db.personalities.create_indexes([
        IndexModel([("user_id", ASCENDING)], unique=True),
    ])

    # Sessions
    await db.sessions.create_indexes([
        IndexModel([("user_id", ASCENDING), ("is_active", ASCENDING)]),
        IndexModel([("user_id", ASCENDING), ("ended_at", DESCENDING)]),
    ])

    # Messages
    await db.messages.create_indexes([
        IndexModel([("session_id", ASCENDING), ("created_at", DESCENDING)]),
        IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)]),
    ])
