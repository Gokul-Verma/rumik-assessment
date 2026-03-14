# Ira AI Companion — Backend System

A tier-aware AI companion chatbot backend with custom load balancing, graceful rate limiting, content safety filtering, and non-blocking analytics.

## Architecture

```
Client Request
     │
     ▼
┌─────────────────┐
│  FastAPI + ASGI  │◄── Correlation ID Middleware
│                  │
│   POST /chat     │
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌──────────┐
│  Safety Filter   │────►│  Block   │──► Personality-aware rejection
│  (jailbreak,     │     └──────────┘
│   NSFW, etc.)    │
└────────┬────────┘
         │ safe
         ▼
┌─────────────────┐     ┌──────────┐
│  Rate Limiter    │────►│  Limit   │──► Personality-aware message (1st)
│  (Redis sliding  │     └──────────┘    Silent 429 (subsequent)
│   window)        │
└────────┬────────┘
         │ allowed
         ▼
┌─────────────────┐
│  Tier Router     │
│                  │
│  Enterprise ──►  Priority Pool (20 workers)
│  Premium    ──►  General Pool  (50 workers) ──► Overflow Pool
│  Free       ──►  General Pool  ──► Overflow ──► Probabilistic Shed
└────────┬────────┘
         │
         ▼
┌─────────────────┐     ┌───────────────┐
│ Message Worker   │────►│  MongoDB      │
│                  │     │  - users      │
│  1. Fetch user   │     │  - sessions   │
│  2. Get session  │     │  - messages   │
│  3. LLM call     │     │  - analytics  │
│  4. Store msgs   │     └───────────────┘
└────────┬────────┘
         │
         ▼                ┌───────────────┐
  Analytics Pipeline ────►│  Redis        │
  (bounded queue,         │  - rate limits│
   batch flush,           │  - pool health│
   <1ms track)            │  - notif flags│
                          └───────────────┘
```

## Quick Start

```bash
# Start everything (MongoDB, Redis, App)
docker-compose up --build

# Seed 4M documents (runs automatically via seeder service)
# Or manually:
docker-compose run --rm app python -m scripts.seed

# Run benchmarks
docker-compose run --rm app python -m scripts.benchmark

# Run tests
docker-compose run --rm app pytest tests/ -v
```

## Design Decisions

### Worker Pools (3-pool architecture)
- **Priority pool** (20 workers): Dedicated to enterprise. Never shared downward, never shed.
- **General pool** (50 workers): Shared by premium and free. Premium gets preference via earlier overflow routing.
- **Overflow pool** (30 workers): Absorbs spillover. Free tier is probabilistically shed when overflow > 80%.

**Why 3 pools?** Isolating enterprise in a dedicated pool guarantees stability regardless of total system load. The general + overflow split allows progressive degradation: premium spills to overflow before free, and free gets shed before premium ever feels impact.

### Rate Limiting (Redis sliding window)
- Sorted sets with timestamp scores for precise per-minute and per-day windows
- Atomic pipeline operations for correctness under concurrency
- Anti-spam: first rate limit sends a personality-aware message; subsequent events within 5 minutes are silent (no message spam)

**Why sliding window over token bucket?** Sliding window provides more predictable behavior — users can't burst-drain a token bucket then wait. It gives a true rolling count.

### Safety Filter (Pattern-based)
- Compiled regex patterns for each category (jailbreak, NSFW, harassment, self-harm, illegal)
- No external API dependency — safety checks run in <1ms
- Confidence scoring prevents over-blocking on partial matches

### Analytics Pipeline (Bounded async queue)
- `track()` calls `put_nowait` on an `asyncio.Queue(maxsize=10000)` — returns in <1ms
- Background task flushes batches of 100 to MongoDB every 5 seconds
- Under pressure: drops events rather than blocking (incrementing a counter)
- Graceful shutdown drains the entire queue before exit

### Session Model (30-minute inactivity gap)
- A "session" represents a continuous conversation
- New session created when no active session exists for a user
- Sessions can be used for LLM context windowing (last 20 messages)

## Trade-offs

| Decision | Benefit | Cost |
|----------|---------|------|
| Semaphore-based pools | Simple, no external queue dependency | Less sophisticated than Redis-based queuing |
| Pattern-based safety | Fast (<1ms), no API cost | Less accurate than ML-based classifiers |
| Probabilistic shedding | Graceful degradation curve | Non-deterministic (some lucky free requests get through) |
| In-process analytics | No queue infrastructure needed | Lost on crash (mitigated by short flush interval) |
| Sliding window rate limit | Precise, no burst issues | Slightly more Redis operations than token bucket |

## MongoDB Indexes

| Collection | Index | Purpose |
|-----------|-------|---------|
| users | `{phone: 1}` unique | Login lookup |
| users | `{external_id: 1}` unique | API user lookup |
| users | `{tier: 1, last_active_at: -1}` | Tier aggregation queries |
| personalities | `{user_id: 1}` unique | Fast personality fetch (1:1 with user) |
| sessions | `{user_id: 1, is_active: 1}` | Active session lookup (compound covers both filter fields) |
| sessions | `{user_id: 1, ended_at: -1}` | Session history with recency sort |
| messages | `{session_id: 1, created_at: -1}` | LLM context window (recent messages in session) |
| messages | `{user_id: 1, created_at: -1}` | Cross-session user history |

## Running Tests

```bash
# All tests
docker-compose run --rm app pytest tests/ -v

# Specific test files
docker-compose run --rm app pytest tests/test_safety.py -v
docker-compose run --rm app pytest tests/test_ratelimit.py -v
docker-compose run --rm app pytest tests/test_balancer.py -v
docker-compose run --rm app pytest tests/test_load.py -v
docker-compose run --rm app pytest tests/test_integration.py -v
docker-compose run --rm app pytest tests/test_analytics.py -v
docker-compose run --rm app pytest tests/test_queries.py -v

# With output
docker-compose run --rm app pytest tests/test_load.py -v -s
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Process a chat message |
| `/health` | GET | System health (pool status, queue depth) |
| `/health/pools` | GET | Detailed pool health with trends |
| `/admin/stats` | GET | Aggregate analytics |

### POST /chat

```json
// Request
{
    "user_id": "user_0000001",
    "content": "Hello, how are you?",
    "platform": "app"
}

// Response (200 OK)
{
    "message": "That's a great thought! ...",
    "session_id": "683...",
    "processing_time_ms": 245.3,
    "rate_limited": false,
    "safety_blocked": false,
    "metadata": {}
}

// Response (429 Rate Limited — first time)
{
    "message": "Hey! I love chatting with you, but I need a tiny breather...",
    "rate_limited": true,
    ...
}

// Response (503 Load Shed)
{
    "message": "I'm getting a lot of love right now! Give me just a moment...",
    ...
}
```
