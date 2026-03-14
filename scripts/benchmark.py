"""
Query benchmarking script.
Runs critical queries with explain("executionStats") and reports performance.

Usage: python -m scripts.benchmark
"""

import asyncio
import json
import time

from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.db.queries import (
    aggregate_messages_by_tier,
    aggregate_tier_activity,
    explain_query,
    get_active_session,
    get_recent_messages,
    get_user_with_personality,
)


async def benchmark():
    print(f"Connecting to MongoDB at {settings.mongo_uri}...")
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    # Find a sample user for queries
    sample_user = await db.users.find_one({"tier": "premium"})
    if not sample_user:
        print("No users found. Run seed script first.")
        client.close()
        return

    user_id = sample_user["external_id"]
    print(f"Benchmarking with user: {user_id}\n")

    # 1. Active session lookup
    print("=" * 60)
    print("1. Get active session for user")
    print("   Query: {user_id, is_active: true}")
    print("   Index: {user_id: 1, is_active: 1}")

    explain_result = await explain_query(
        db, "sessions", {"user_id": user_id, "is_active": True}
    )
    _print_explain(explain_result)

    start = time.monotonic()
    result = await get_active_session(db, user_id)
    elapsed = (time.monotonic() - start) * 1000
    print(f"   Execution time: {elapsed:.2f}ms")
    print(f"   Result: {'Found' if result else 'No active session'}\n")

    # 2. Recent messages
    sample_session = await db.sessions.find_one({"user_id": user_id})
    if sample_session:
        session_id = str(sample_session["_id"])
        print("=" * 60)
        print("2. Get recent messages for session")
        print("   Query: {session_id} sorted by {created_at: -1} limit 20")
        print("   Index: {session_id: 1, created_at: -1}")

        explain_result = await explain_query(
            db, "messages", {"session_id": session_id}
        )
        _print_explain(explain_result)

        start = time.monotonic()
        messages = await get_recent_messages(db, session_id, limit=20)
        elapsed = (time.monotonic() - start) * 1000
        print(f"   Execution time: {elapsed:.2f}ms")
        print(f"   Messages returned: {len(messages)}\n")

    # 3. User with personality ($lookup)
    print("=" * 60)
    print("3. User with personality ($lookup)")
    print("   Pipeline: $match on external_id → $lookup personalities on user_id")
    print("   Indexes: users.external_id (unique), personalities.user_id (unique)")

    start = time.monotonic()
    result = await get_user_with_personality(db, user_id)
    elapsed = (time.monotonic() - start) * 1000
    print(f"   Execution time: {elapsed:.2f}ms")
    if result:
        print(f"   User tier: {result.get('tier')}")
        print(f"   Personality tone: {result.get('personality', {}).get('tone')}\n")

    # 4. Tier activity aggregation
    print("=" * 60)
    print("4. Tier activity aggregation")
    print("   Pipeline: $group by tier, count users, count recently active")

    start = time.monotonic()
    result = await aggregate_tier_activity(db)
    elapsed = (time.monotonic() - start) * 1000
    print(f"   Execution time: {elapsed:.2f}ms")
    for tier in result:
        print(f"   {tier['_id']}: {tier['total_users']:,} users, "
              f"{tier['recently_active']:,} active in last 24h")
    print()

    # 5. Messages by tier
    print("=" * 60)
    print("5. Messages by tier (last 7 days)")
    print("   Pipeline: $group users by tier → $lookup messages with date filter")

    start = time.monotonic()
    result = await aggregate_messages_by_tier(db, days=7)
    elapsed = (time.monotonic() - start) * 1000
    print(f"   Execution time: {elapsed:.2f}ms")
    for entry in result:
        print(f"   {entry.get('tier', entry.get('_id'))}: "
              f"{entry['user_count']:,} users, {entry['message_count']:,} messages")
    print()

    # Index stats
    print("=" * 60)
    print("Index Statistics:")
    for coll_name in ["users", "personalities", "sessions", "messages"]:
        indexes = await db[coll_name].index_information()
        print(f"\n  {coll_name}:")
        for idx_name, idx_info in indexes.items():
            print(f"    {idx_name}: {idx_info['key']}")

    client.close()


def _print_explain(explain_result: dict) -> None:
    """Print relevant parts of explain output."""
    try:
        stats = explain_result.get("queryPlanner", {})
        winning = stats.get("winningPlan", {})
        exec_stats = explain_result.get("executionStats", {})

        print(f"   Winning plan stage: {winning.get('stage', 'N/A')}")
        if "inputStage" in winning:
            input_stage = winning["inputStage"]
            print(f"   Input stage: {input_stage.get('stage', 'N/A')}")
            if "indexName" in input_stage:
                print(f"   Index used: {input_stage['indexName']}")

        if exec_stats:
            print(f"   Docs examined: {exec_stats.get('totalDocsExamined', 'N/A')}")
            print(f"   Keys examined: {exec_stats.get('totalKeysExamined', 'N/A')}")
            print(f"   Docs returned: {exec_stats.get('nReturned', 'N/A')}")
            print(f"   Execution time: {exec_stats.get('executionTimeMillis', 'N/A')}ms")
    except Exception as e:
        print(f"   (Could not parse explain output: {e})")


if __name__ == "__main__":
    asyncio.run(benchmark())
