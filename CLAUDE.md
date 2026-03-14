# Ira AI Companion — Backend System

## Project Overview
Ira is a tier-aware AI companion chatbot backend supporting WhatsApp and native app.
Three tiers: **free**, **premium**, **enterprise** with graceful degradation.

## Tech Stack
- **Python 3.13** (async throughout)
- **FastAPI** + uvicorn
- **MongoDB 7+** (motor async driver) — 4 collections, ~1M docs each
- **Redis** (redis.asyncio) — distributed state, rate limiting, pub/sub
- **Docker Compose** — multi-service deployment

## Project Structure
```
ira/
├── app/
│   ├── main.py                  # FastAPI app, lifespan, startup/shutdown
│   ├── config.py                # Settings via pydantic-settings
│   ├── models/                  # Pydantic models & MongoDB schemas
│   │   ├── user.py
│   │   ├── personality.py
│   │   ├── session.py
│   │   └── message.py
│   ├── db/
│   │   ├── mongo.py             # Motor client, index creation
│   │   ├── redis.py             # Redis client
│   │   ├── indexes.py           # Compound index definitions
│   │   └── queries.py           # Aggregation pipelines ($lookup etc.)
│   ├── balancer/
│   │   ├── router.py            # Tier-aware request routing
│   │   ├── pools.py             # Worker pool management (priority/general/overflow)
│   │   └── health.py            # Pool health tracking & scoring
│   ├── ratelimit/
│   │   ├── limiter.py           # Token bucket / sliding window per tier
│   │   ├── responses.py         # Personality-aware rate limit messages
│   │   └── middleware.py        # FastAPI middleware integration
│   ├── safety/
│   │   ├── filter.py            # Content safety detection
│   │   └── responses.py         # Personality-aware safety responses
│   ├── analytics/
│   │   ├── pipeline.py          # Bounded queue, batch flush, <1ms track()
│   │   ├── logger.py            # Structured logging with correlation IDs
│   │   └── slow_op.py           # Slow operation detection decorator
│   ├── api/
│   │   ├── routes.py            # Chat endpoint, health, admin
│   │   └── middleware.py        # Correlation ID injection
│   └── workers/
│       └── processor.py         # Message processing worker logic
├── scripts/
│   ├── seed.py                  # Seed 4M documents (realistic distribution)
│   └── benchmark.py             # Query benchmarking with explain()
├── tests/
│   ├── conftest.py
│   ├── test_load.py             # Load tests (normal, high, extreme)
│   ├── test_ratelimit.py        # Rate limiting behavior
│   ├── test_safety.py           # Safety filter tests
│   ├── test_balancer.py         # Worker pool & routing tests
│   ├── test_analytics.py        # Analytics pipeline tests
│   └── test_queries.py          # DB query correctness & performance
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── requirements.txt
└── README.md
```

## Architecture Decisions
- **Worker pools**: 3 pools — priority (enterprise), general (premium+free), overflow (spillover). Enterprise never degrades; premium degrades slowly; free sheds first.
- **Rate limiting**: Redis sliding window. Limits per tier. First limit → personality-aware message. Subsequent → silent queue/drop (no spam).
- **Safety**: Keyword + pattern matching (no external API dependency). Blocks before LLM call.
- **Analytics**: In-memory bounded asyncio.Queue, background batch flush to MongoDB. track() is fire-and-forget, <1ms.
- **Sessions**: Time-gap based (30min inactivity = new session). Supports context window for LLM calls.

## Coding Conventions
- All I/O is async (never use synchronous blocking calls)
- Use `motor` for MongoDB, `redis.asyncio` for Redis
- Type hints on all function signatures
- Pydantic v2 models for all schemas
- Use `structlog` for structured logging
- Tests use `pytest` + `pytest-asyncio` + `httpx.AsyncClient`
- No print statements — use structured logger

## Key Commands
```bash
# Run locally
docker-compose up --build

# Run tests
docker-compose run --rm app pytest tests/ -v

# Seed data
docker-compose run --rm app python scripts/seed.py

# Run specific test
docker-compose run --rm app pytest tests/test_load.py -v -k "test_normal_load"
```

## MongoDB Collections & Indexes
- **users**: `{tier: 1, created_at: -1}`, `{phone: 1}` (unique), `{external_id: 1}` (unique)
- **personalities**: `{user_id: 1}` (unique)
- **sessions**: `{user_id: 1, is_active: 1}`, `{user_id: 1, ended_at: -1}`, TTL on `ended_at`
- **messages**: `{session_id: 1, created_at: -1}`, `{user_id: 1, created_at: -1}`

## Rate Limits (per tier, per minute)
- Free: 10 msgs/min, 100 msgs/day
- Premium: 60 msgs/min, 1000 msgs/day
- Enterprise: 200 msgs/min, unlimited daily

## Environment Variables
- `MONGO_URI` — MongoDB connection string
- `REDIS_URI` — Redis connection string
- `LOG_LEVEL` — debug/info/warning
- `WORKER_POOL_PRIORITY_SIZE` — enterprise pool size (default: 20)
- `WORKER_POOL_GENERAL_SIZE` — general pool size (default: 50)
- `WORKER_POOL_OVERFLOW_SIZE` — overflow pool size (default: 30)
- `ANALYTICS_BATCH_SIZE` — flush threshold (default: 100)
- `ANALYTICS_FLUSH_INTERVAL` — seconds between flushes (default: 5)
