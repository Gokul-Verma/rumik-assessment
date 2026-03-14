"""
Seed script: generates ~4M realistic documents across 4 collections.

Distribution:
- 1M users (70% free, 20% premium, 10% enterprise)
- 1M personalities (1:1 with users)
- 1M sessions (Pareto distribution across users)
- 1M messages (heavy-tailed across sessions)

Usage: python -m scripts.seed
"""

import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone

from faker import Faker
from motor.motor_asyncio import AsyncIOMotorClient
from tqdm import tqdm

from app.config import settings
from app.db.indexes import create_indexes

fake = Faker()

BATCH_SIZE = 10_000
NUM_USERS = 1_000_000
NUM_PERSONALITIES = 1_000_000
NUM_SESSIONS = 1_000_000
NUM_MESSAGES = 1_000_000

TIERS = ["free"] * 70 + ["premium"] * 20 + ["enterprise"] * 10
PLATFORMS = ["whatsapp"] * 60 + ["app"] * 40
TONES = ["friendly", "professional", "casual", "empathetic"]
VERBOSITIES = ["concise", "normal", "detailed"]
ROLES = ["user", "assistant"]
INTERESTS = [
    "technology", "cooking", "fitness", "travel", "music", "reading",
    "gaming", "photography", "art", "science", "movies", "sports",
    "fashion", "nature", "meditation", "finance", "education", "pets",
]

SAMPLE_MESSAGES = [
    "How are you doing today?",
    "Can you help me with something?",
    "Tell me a joke!",
    "What's the weather like?",
    "I need advice about my career.",
    "What do you think about AI?",
    "Help me plan a trip.",
    "I'm feeling stressed today.",
    "Can you recommend a good book?",
    "What should I cook for dinner?",
    "Tell me something interesting.",
    "I'm bored, let's chat!",
    "Help me write an email.",
    "What's the meaning of life?",
    "Can you help me study?",
]

SAMPLE_RESPONSES = [
    "I'm doing great, thanks for asking! How about you?",
    "Of course! I'd love to help. What do you need?",
    "Here's one: Why do programmers prefer dark mode? Because light attracts bugs!",
    "I'd love to help you plan! Where are you thinking of going?",
    "I hear you. Let's talk about what's on your mind.",
    "That's a fascinating topic! Here's what I think...",
    "Great question! Let me share some thoughts on that.",
    "I'm here for you. Want to tell me more about what's going on?",
    "I'd recommend checking out some classic reads. What genres do you enjoy?",
    "How about a simple pasta dish? Quick, easy, and delicious!",
]


def generate_user_batch(batch_num: int, batch_size: int) -> list[dict]:
    base = batch_num * batch_size
    users = []
    now = datetime.now(timezone.utc)
    for i in range(batch_size):
        idx = base + i
        tier = random.choice(TIERS)
        created = now - timedelta(days=random.randint(1, 730))
        users.append({
            "external_id": f"user_{idx:07d}",
            "phone": f"+1{2000000000 + idx}",
            "display_name": fake.name(),
            "tier": tier,
            "platform": random.choice(PLATFORMS),
            "language": random.choice(["en", "en", "en", "es", "fr", "de", "hi"]),
            "timezone": random.choice(["UTC", "US/Eastern", "US/Pacific", "Europe/London", "Asia/Kolkata"]),
            "created_at": created,
            "last_active_at": created + timedelta(days=random.randint(0, (now - created).days or 1)),
            "metadata": {},
        })
    return users


def generate_personality_batch(batch_num: int, batch_size: int) -> list[dict]:
    base = batch_num * batch_size
    now = datetime.now(timezone.utc)
    personalities = []
    for i in range(batch_size):
        idx = base + i
        personalities.append({
            "user_id": f"user_{idx:07d}",
            "tone": random.choice(TONES),
            "verbosity": random.choice(VERBOSITIES),
            "humor_level": random.randint(0, 10),
            "formality": random.randint(0, 10),
            "interests": random.sample(INTERESTS, k=random.randint(1, 5)),
            "custom_instructions": "",
            "updated_at": now - timedelta(days=random.randint(0, 365)),
        })
    return personalities


def generate_session_batch(batch_num: int, batch_size: int, num_users: int) -> list[dict]:
    """Sessions follow Pareto distribution — some users have many sessions."""
    now = datetime.now(timezone.utc)
    sessions = []
    for _ in range(batch_size):
        # Pareto: most sessions belong to a small subset of users
        user_idx = int(random.paretovariate(1.5)) % num_users
        started = now - timedelta(days=random.randint(0, 365), hours=random.randint(0, 23))
        is_active = random.random() < 0.1  # 10% active
        ended = None if is_active else started + timedelta(minutes=random.randint(5, 120))
        sessions.append({
            "user_id": f"user_{user_idx:07d}",
            "started_at": started,
            "ended_at": ended,
            "is_active": is_active,
            "message_count": random.randint(2, 100),
            "platform": random.choice(PLATFORMS),
            "context_summary": "",
            "metadata": {},
        })
    return sessions


def generate_message_batch(
    batch_num: int,
    batch_size: int,
    session_ids: list,
    session_user_map: dict,
) -> list[dict]:
    """Messages follow heavy-tailed distribution across sessions."""
    now = datetime.now(timezone.utc)
    messages = []
    for _ in range(batch_size):
        # Heavy tail: some sessions have many messages
        sid_idx = int(random.paretovariate(1.2)) % len(session_ids)
        sid = session_ids[sid_idx]
        user_id = session_user_map.get(str(sid), "user_0000000")
        role = random.choice(ROLES)
        content = random.choice(SAMPLE_MESSAGES if role == "user" else SAMPLE_RESPONSES)
        messages.append({
            "session_id": str(sid),
            "user_id": user_id,
            "role": role,
            "content": content,
            "created_at": now - timedelta(
                days=random.randint(0, 365),
                hours=random.randint(0, 23),
                minutes=random.randint(0, 59),
            ),
            "processing_time_ms": random.uniform(50, 500) if role == "assistant" else 0,
            "tokens_used": random.randint(10, 200) if role == "assistant" else 0,
            "safety_flagged": random.random() < 0.001,
            "rate_limited": random.random() < 0.005,
            "metadata": {},
        })
    return messages


async def seed():
    print(f"Connecting to MongoDB at {settings.mongo_uri}...")
    client = AsyncIOMotorClient(settings.mongo_uri)
    db = client[settings.mongo_db]

    # Check if already seeded
    user_count = await db.users.count_documents({})
    if user_count >= NUM_USERS:
        print(f"Database already has {user_count} users. Skipping seed.")
        client.close()
        return

    # Clear existing data for clean seed
    print("Clearing existing data...")
    for coll in ["users", "personalities", "sessions", "messages", "analytics"]:
        await db[coll].drop()

    # Create indexes first
    print("Creating indexes...")
    await create_indexes(db)

    # Seed users
    print(f"\nSeeding {NUM_USERS:,} users...")
    num_batches = NUM_USERS // BATCH_SIZE
    for batch_num in tqdm(range(num_batches), desc="Users"):
        batch = generate_user_batch(batch_num, BATCH_SIZE)
        await db.users.insert_many(batch, ordered=False)

    # Seed personalities
    print(f"\nSeeding {NUM_PERSONALITIES:,} personalities...")
    num_batches = NUM_PERSONALITIES // BATCH_SIZE
    for batch_num in tqdm(range(num_batches), desc="Personalities"):
        batch = generate_personality_batch(batch_num, BATCH_SIZE)
        await db.personalities.insert_many(batch, ordered=False)

    # Seed sessions
    print(f"\nSeeding {NUM_SESSIONS:,} sessions...")
    num_batches = NUM_SESSIONS // BATCH_SIZE
    for batch_num in tqdm(range(num_batches), desc="Sessions"):
        batch = generate_session_batch(batch_num, BATCH_SIZE, NUM_USERS)
        await db.sessions.insert_many(batch, ordered=False)

    # Collect session IDs and user mapping for message generation
    print("\nCollecting session IDs for message generation...")
    session_ids = []
    session_user_map = {}
    async for session in db.sessions.find({}, {"_id": 1, "user_id": 1}).limit(100_000):
        session_ids.append(session["_id"])
        session_user_map[str(session["_id"])] = session["user_id"]

    # Seed messages
    print(f"\nSeeding {NUM_MESSAGES:,} messages...")
    num_batches = NUM_MESSAGES // BATCH_SIZE
    for batch_num in tqdm(range(num_batches), desc="Messages"):
        batch = generate_message_batch(batch_num, BATCH_SIZE, session_ids, session_user_map)
        await db.messages.insert_many(batch, ordered=False)

    # Print summary
    print("\n--- Seed Summary ---")
    for coll in ["users", "personalities", "sessions", "messages"]:
        count = await db[coll].count_documents({})
        print(f"  {coll}: {count:,} documents")

    # Tier distribution
    pipeline = [{"$group": {"_id": "$tier", "count": {"$sum": 1}}}]
    tiers = await db.users.aggregate(pipeline).to_list(length=10)
    print("\n  Tier distribution:")
    for t in sorted(tiers, key=lambda x: x["_id"]):
        print(f"    {t['_id']}: {t['count']:,} ({t['count']/NUM_USERS*100:.1f}%)")

    client.close()
    print("\nSeeding complete!")


if __name__ == "__main__":
    asyncio.run(seed())
