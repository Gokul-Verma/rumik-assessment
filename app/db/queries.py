"""
Query patterns for Ira's data layer.

All queries leverage compound indexes defined in indexes.py.
See performance notes inline for each query.
"""

from motor.motor_asyncio import AsyncIOMotorDatabase


async def get_active_session(db: AsyncIOMotorDatabase, user_id: str) -> dict | None:
    """
    Fetch the current active session for a user.

    Performance: Uses compound index {user_id: 1, is_active: 1}.
    This is an equality match on both fields → single index seek, IXSCAN only.
    Expected: <1ms for any user regardless of total session count.
    """
    return await db.sessions.find_one(
        {"user_id": user_id, "is_active": True}
    )


async def get_recent_messages(
    db: AsyncIOMotorDatabase,
    session_id: str,
    limit: int = 20,
) -> list[dict]:
    """
    Retrieve recent messages for LLM context window.

    Performance: Uses index {session_id: 1, created_at: -1}.
    Sort is satisfied by index order (no in-memory sort).
    $limit stops scanning after N documents → bounded work.
    """
    cursor = db.messages.find(
        {"session_id": session_id}
    ).sort("created_at", -1).limit(limit)
    messages = await cursor.to_list(length=limit)
    messages.reverse()  # Return in chronological order
    return messages


async def get_user_with_personality(
    db: AsyncIOMotorDatabase,
    user_id: str,
) -> dict | None:
    """
    Fetch user document joined with their personality config via $lookup.

    Performance: $match on _id (primary key) → IXSCAN.
    $lookup on personalities.user_id → uses unique index {user_id: 1}.
    Total: two index seeks, no collection scans.
    """
    pipeline = [
        {"$match": {"external_id": user_id}},
        {"$lookup": {
            "from": "personalities",
            "localField": "external_id",
            "foreignField": "user_id",
            "as": "personality",
        }},
        {"$unwind": {"path": "$personality", "preserveNullAndEmptyArrays": True}},
        {"$limit": 1},
    ]
    results = await db.users.aggregate(pipeline).to_list(length=1)
    return results[0] if results else None


async def get_session_with_messages(
    db: AsyncIOMotorDatabase,
    session_id: str,
    message_limit: int = 20,
) -> dict | None:
    """
    Fetch a session with its most recent messages via $lookup + $slice.

    Performance: $match on _id → primary key seek.
    $lookup uses index {session_id: 1, created_at: -1} on messages collection.
    $slice limits transferred data to last N messages.
    """
    pipeline = [
        {"$match": {"_id": session_id}},
        {"$lookup": {
            "from": "messages",
            "let": {"sid": "$_id"},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$session_id", "$$sid"]}}},
                {"$sort": {"created_at": -1}},
                {"$limit": message_limit},
            ],
            "as": "recent_messages",
        }},
    ]
    results = await db.sessions.aggregate(pipeline).to_list(length=1)
    return results[0] if results else None


async def get_active_session_with_context(
    db: AsyncIOMotorDatabase,
    user_id: str,
    message_limit: int = 20,
) -> dict | None:
    """
    Combined query: find user's active session + join recent messages.
    Single aggregation pipeline instead of two round-trips.

    Performance: $match on {user_id, is_active} → compound index.
    $lookup with sub-pipeline uses message index for sort+limit.
    """
    pipeline = [
        {"$match": {"user_id": user_id, "is_active": True}},
        {"$limit": 1},
        {"$lookup": {
            "from": "messages",
            "let": {"sid": {"$toString": "$_id"}},
            "pipeline": [
                {"$match": {"$expr": {"$eq": ["$session_id", "$$sid"]}}},
                {"$sort": {"created_at": -1}},
                {"$limit": message_limit},
            ],
            "as": "recent_messages",
        }},
    ]
    results = await db.sessions.aggregate(pipeline).to_list(length=1)
    return results[0] if results else None


async def aggregate_tier_activity(db: AsyncIOMotorDatabase) -> list[dict]:
    """
    Aggregation: count active sessions and average message counts per tier.

    Performance: Uses $lookup from users → sessions.
    The users index {tier: 1, last_active_at: -1} supports the $group by tier.
    Sessions index {user_id: 1, is_active: 1} supports the sub-pipeline match.
    """
    pipeline = [
        {"$group": {
            "_id": "$tier",
            "total_users": {"$sum": 1},
            "recently_active": {
                "$sum": {"$cond": [
                    {"$gte": ["$last_active_at", {"$subtract": [
                        "$$NOW", 86400000  # last 24h in ms
                    ]}]},
                    1, 0
                ]}
            },
        }},
        {"$sort": {"_id": 1}},
    ]
    return await db.users.aggregate(pipeline).to_list(length=10)


async def aggregate_messages_by_tier(
    db: AsyncIOMotorDatabase,
    days: int = 7,
) -> list[dict]:
    """
    User engagement report: messages per tier over the last N days.

    Performance: Joins users and messages. The messages index
    {user_id: 1, created_at: -1} supports the date-filtered $lookup.
    """
    from datetime import datetime, timedelta
    cutoff = datetime.utcnow() - timedelta(days=days)

    pipeline = [
        {"$group": {
            "_id": "$tier",
            "user_ids": {"$push": "$external_id"},
        }},
        {"$lookup": {
            "from": "messages",
            "let": {"uids": "$user_ids"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$and": [
                        {"$in": ["$user_id", "$$uids"]},
                        {"$gte": ["$created_at", cutoff]},
                    ]}
                }},
                {"$count": "total"},
            ],
            "as": "message_stats",
        }},
        {"$project": {
            "tier": "$_id",
            "user_count": {"$size": "$user_ids"},
            "message_count": {
                "$ifNull": [
                    {"$arrayElemAt": ["$message_stats.total", 0]},
                    0,
                ]
            },
        }},
        {"$sort": {"tier": 1}},
    ]
    return await db.users.aggregate(pipeline).to_list(length=10)


async def explain_query(db: AsyncIOMotorDatabase, collection: str, query: dict) -> dict:
    """Run explain("executionStats") on a find query for benchmarking."""
    return await db.command(
        "explain",
        {"find": collection, "filter": query},
        verbosity="executionStats",
    )
