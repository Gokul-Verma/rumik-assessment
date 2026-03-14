# Implementation Plan — Ira AI Companion Backend

## Phase 1: Project Scaffolding & Data Layer
**Goal:** Set up project structure, MongoDB schemas, indexes, seed data, and query patterns.

### Step 1.1 — Project Setup
- [ ] Create `pyproject.toml` with dependencies: fastapi, uvicorn, motor, redis, pydantic-settings, structlog, pytest, pytest-asyncio, httpx
- [ ] Create `Dockerfile` (Python 3.13 slim)
- [ ] Create `docker-compose.yml` with: app, mongodb (7+), redis (7+)
- [ ] Create `app/config.py` with pydantic-settings for env vars
- [ ] Create `app/main.py` with FastAPI lifespan (startup/shutdown hooks)

### Step 1.2 — MongoDB Schemas (Pydantic Models)
- [ ] `app/models/user.py` — User model
  ```
  Fields: _id, external_id, phone, display_name, tier (free|premium|enterprise),
          platform (whatsapp|app), language, timezone, created_at, last_active_at,
          metadata (dict)
  Distribution: 70% free, 20% premium, 10% enterprise
  ```
- [ ] `app/models/personality.py` — Per-user personality config
  ```
  Fields: _id, user_id (ref), tone (friendly|professional|casual|empathetic),
          verbosity (concise|normal|detailed), humor_level (0-10),
          formality (0-10), interests (list), custom_instructions (str),
          updated_at
  ```
- [ ] `app/models/session.py` — Chat session
  ```
  Fields: _id, user_id (ref), started_at, ended_at (nullable),
          is_active (bool), message_count, platform, context_summary (str),
          metadata
  Session = continuous chat; ends after 30min inactivity or explicit close.
  ```
- [ ] `app/models/message.py` — Individual message
  ```
  Fields: _id, session_id (ref), user_id (ref), role (user|assistant|system),
          content, created_at, processing_time_ms, tokens_used,
          safety_flagged (bool), rate_limited (bool), metadata
  ```

### Step 1.3 — Index Strategy
- [ ] `app/db/indexes.py` — Create all compound indexes on startup
  - users: `{tier: 1, last_active_at: -1}` — tier-based aggregation queries
  - users: `{phone: 1}` unique — login lookup
  - users: `{external_id: 1}` unique — API lookup
  - personalities: `{user_id: 1}` unique — fast personality fetch
  - sessions: `{user_id: 1, is_active: 1}` — active session lookup (covered query)
  - sessions: `{user_id: 1, ended_at: -1}` — recent session history
  - messages: `{session_id: 1, created_at: -1}` — message history for context
  - messages: `{user_id: 1, created_at: -1}` — user message history
  - messages: `{created_at: 1}` with TTL (optional, for cleanup)

### Step 1.4 — Seed Script
- [ ] `scripts/seed.py` — Generate ~4M documents total
  - 1M users (70/20/10 split by tier, realistic names, varied platforms)
  - 1M personalities (one per user, varied tone/verbosity distributions)
  - 1M sessions (Pareto distribution — some users have many sessions, most have few)
  - 1M messages (heavy-tailed distribution across sessions, realistic timestamps)
  - Use bulk insert (insert_many with batches of 10k) for performance
  - Add progress bar (tqdm)

### Step 1.5 — Query Patterns & Aggregation Pipelines
- [ ] `app/db/queries.py` — Implement critical queries:
  1. **Get active session for user** — `$match` user_id + is_active, single doc
  2. **Get recent messages for LLM context** — `$match` session_id, `$sort` created_at desc, `$limit` 20
  3. **User with personality lookup** — `$lookup` from personalities on user_id
  4. **Session with recent messages** — `$lookup` from messages, `$slice` last 20
  5. **Tier activity aggregation** — `$group` by tier, count active sessions, avg messages
  6. **User engagement report** — messages per day/week grouped by tier
- [ ] `scripts/benchmark.py` — Run each query with `explain("executionStats")`, save output

---

## Phase 2: Tier-Aware Load Balancer & Worker Pools
**Goal:** Route requests to appropriate worker pools based on tier, health, and load.

### Step 2.1 — Worker Pool Implementation
- [ ] `app/balancer/pools.py`
  - `WorkerPool` class: asyncio.Semaphore-based concurrency control
    - Properties: name, max_workers, active_count, queue_depth, avg_latency
    - Methods: acquire(), release(), health_score() → float 0-1
  - Three pool instances:
    - **priority** (enterprise): 20 workers, dedicated, never shared
    - **general** (premium + free): 50 workers, shared with preference to premium
    - **overflow** (spillover): 30 workers, absorbs excess when general is full
  - Pool health = f(utilization, queue_depth, error_rate, avg_latency)

### Step 2.2 — Health Tracking
- [ ] `app/balancer/health.py`
  - `PoolHealthTracker`: background task that samples pool metrics every 1s
  - Stores rolling window (last 60 samples) per pool
  - Exposes: current_health, trend (improving/degrading/stable)
  - Publishes health updates to Redis for multi-node awareness

### Step 2.3 — Tier-Aware Router
- [ ] `app/balancer/router.py`
  - `TierRouter.route(request) → WorkerPool`
  - Routing logic:
    - **Enterprise** → always priority pool. If priority full (shouldn't happen), steal from general.
    - **Premium** → general pool. If general >80% full, use overflow. Never shed.
    - **Free** → general pool. If general >60% full, route to overflow. If overflow >80%, start shedding (reject with graceful message).
  - Load shedding: probabilistic rejection based on load level (not cliff-edge)
    - Free shed probability: `max(0, (load - 0.6) / 0.4)` → linear ramp from 60% to 100% load
  - Track routing decisions in analytics

### Step 2.4 — Request Processing Pipeline
- [ ] `app/workers/processor.py`
  - `MessageProcessor.process(request)`:
    1. Acquire worker from routed pool
    2. Fetch user personality
    3. Fetch/create active session
    4. Fetch recent messages for context
    5. Run safety filter
    6. Check rate limit
    7. Generate response (simulated LLM call with tier-based latency)
    8. Store message
    9. Track analytics
    10. Release worker

---

## Phase 3: Rate Limiting & Safety Layer
**Goal:** Graceful, personality-aware rate limiting and content safety.

### Step 3.1 — Rate Limiter
- [ ] `app/ratelimit/limiter.py`
  - Redis-based sliding window counter (per user, per minute + per day)
  - Tier limits: free=10/min+100/day, premium=60/min+1000/day, enterprise=200/min+unlimited
  - Returns: `RateLimitResult(allowed, remaining, limit, reset_at, is_first_limit)`
  - Uses Redis MULTI/EXEC for atomicity
  - `is_first_limit` tracks whether this is the first time user hit limit in current window

### Step 3.2 — Personality-Aware Rate Limit Responses
- [ ] `app/ratelimit/responses.py`
  - Template bank keyed by personality tone:
    - friendly: "Hey! I love chatting with you, but I need a tiny breather..."
    - professional: "I appreciate your engagement. I'll be available again shortly..."
    - casual: "Whoa, we've been going at it! Gimme a sec to catch up..."
    - empathetic: "I can tell you have a lot on your mind. Let me take a moment..."
  - First limit → send personality-aware message
  - Subsequent limits (within 5 min) → silent (no response, just HTTP 429 with retry-after)
  - Anti-spam: Redis flag `ratelimit:notified:{user_id}` with 5min TTL

### Step 3.3 — Safety Filter
- [ ] `app/safety/filter.py`
  - `SafetyFilter.check(content) → SafetyResult(safe, category, confidence)`
  - Detection categories: jailbreak, nsfw, harassment, self_harm, illegal
  - Implementation: keyword matching + regex patterns + basic heuristics
    - Jailbreak: "ignore previous", "you are now", "DAN", prompt injection patterns
    - NSFW: configurable word lists + phrase patterns
    - Other: pattern-based detection
  - Returns confidence score (0-1), blocks above threshold (0.7)

### Step 3.4 — Personality-Aware Safety Responses
- [ ] `app/safety/responses.py`
  - Similar template bank to rate limiting, keyed by personality + safety category
  - Examples:
    - friendly + jailbreak: "I appreciate creativity, but I'm happiest being myself!"
    - professional + nsfw: "I'd prefer to keep our conversation professional..."
  - Integrates with rate limiter: repeated safety violations accelerate rate limiting

---

## Phase 4: Analytics & Structured Logging
**Goal:** Non-blocking observability that never impacts user-facing latency.

### Step 4.1 — Analytics Pipeline
- [ ] `app/analytics/pipeline.py`
  - `AnalyticsPipeline`:
    - Bounded `asyncio.Queue(maxsize=10000)`
    - `track(event)` → non-blocking put_nowait, drops if full (increments drop counter)
    - Background flush task: drains queue in batches of 100, writes to MongoDB `analytics` collection
    - Flush triggers: batch size reached OR flush interval (5s) elapsed
    - Graceful shutdown: drain remaining queue before exit
  - Event types: message_processed, rate_limited, safety_blocked, pool_routed, error

### Step 4.2 — Structured Logging
- [ ] `app/analytics/logger.py`
  - Built on `structlog` with JSON output
  - Auto-binds: correlation_id, user_id, tier, operation, timestamp
  - Context manager for operation timing
  - Log levels: DEBUG for internal, INFO for request lifecycle, WARN for degradation, ERROR for failures

### Step 4.3 — Correlation ID Middleware
- [ ] `app/api/middleware.py`
  - Generate UUID4 correlation ID per request (or accept from X-Correlation-ID header)
  - Store in contextvars for access throughout request lifecycle
  - Include in all log entries and analytics events
  - Return in response headers

### Step 4.4 — Slow Operation Detection
- [ ] `app/analytics/slow_op.py`
  - `@track_slow(threshold_ms=100)` decorator
  - Measures execution time, auto-logs WARNING if exceeds threshold
  - Includes operation name, duration, context in log entry
  - Threshold configurable per-operation

### Step 4.5 — Graceful Shutdown
- [ ] In `app/main.py` lifespan:
  - On shutdown signal: stop accepting new requests
  - Drain worker pools (wait for in-flight requests, timeout 30s)
  - Flush analytics queue completely
  - Flush structured log buffers
  - Close MongoDB and Redis connections
  - Log shutdown complete

---

## Phase 5: API Layer & Integration
**Goal:** Wire everything together into working FastAPI endpoints.

### Step 5.1 — Chat Endpoint
- [ ] `app/api/routes.py`
  - `POST /chat` — Main chat endpoint
    ```json
    Request:  { "user_id": "...", "content": "...", "platform": "whatsapp|app" }
    Response: { "message": "...", "session_id": "...", "metadata": {...} }
    ```
  - Flow: middleware → rate limit → safety → route to pool → process → respond
  - `GET /health` — System health (pool status, db connectivity, queue depth)
  - `GET /health/pools` — Detailed pool health metrics
  - `GET /admin/stats` — Aggregate analytics (protected)

### Step 5.2 — Error Handling
- [ ] Custom exception handlers that return personality-aware messages
- [ ] Never expose stack traces or system internals to users
- [ ] Map all errors to user-friendly responses

---

## Phase 6: Docker & DevOps
**Goal:** Fully runnable with `docker-compose up`.

### Step 6.1 — Dockerfile
- [ ] Multi-stage build: builder (install deps) → runtime (slim)
- [ ] Python 3.13-slim base
- [ ] Non-root user
- [ ] Health check endpoint

### Step 6.2 — Docker Compose
- [ ] Services:
  - `app` — FastAPI application (2 replicas to simulate multi-node)
  - `mongodb` — MongoDB 7 with volume persistence
  - `redis` — Redis 7 with persistence
  - `seeder` — One-shot service that seeds data, then exits
  - `nginx` — (optional) reverse proxy in front of app replicas
- [ ] Networks: internal (app↔db↔redis)
- [ ] Volumes: mongodb-data, redis-data
- [ ] Environment files: `.env.example`

### Step 6.3 — Seed Integration
- [ ] Seeder waits for MongoDB to be ready
- [ ] Creates indexes first, then seeds data
- [ ] Idempotent (checks if data exists before seeding)
- [ ] Progress logging

---

## Phase 7: Testing
**Goal:** Comprehensive test suite covering all requirements.

### Step 7.1 — Test Infrastructure
- [ ] `tests/conftest.py` — Fixtures for test DB, Redis, app client
- [ ] Use separate test database
- [ ] Fixtures for sample users (one per tier), sessions, messages

### Step 7.2 — Unit Tests
- [ ] `tests/test_safety.py` — Safety filter detection accuracy
  - Jailbreak detection (true positives + false negatives)
  - NSFW detection
  - Clean content passes through
  - Personality-aware responses generated correctly
- [ ] `tests/test_ratelimit.py` — Rate limiting logic
  - Limits enforced per tier
  - First limit returns personality message
  - Subsequent limits are silent
  - Limits reset after window
- [ ] `tests/test_balancer.py` — Routing logic
  - Enterprise → priority pool
  - Premium → general pool, overflow when needed
  - Free → general pool, shed under load
  - Health score calculation
- [ ] `tests/test_analytics.py` — Analytics pipeline
  - Events tracked without blocking
  - Queue overflow drops gracefully
  - Batch flush works
  - Graceful shutdown drains queue
- [ ] `tests/test_queries.py` — Database queries
  - Active session lookup returns correct session
  - Recent messages returns ordered, limited results
  - Aggregation pipelines return correct counts

### Step 7.3 — Load Tests
- [ ] `tests/test_load.py`
  - **Normal load**: 50 concurrent users (mixed tiers), all requests succeed, p99 < 500ms
  - **High load**: 200 concurrent users, enterprise stable, premium slight degradation, free noticeable degradation
  - **Extreme load**: 500 concurrent users, enterprise still stable, free heavily shed
  - Measure and report: p50, p95, p99 latency per tier, success rate per tier, shed rate

### Step 7.4 — Integration Tests
- [ ] End-to-end chat flow: send message → get response → verify stored in DB
- [ ] Rate limit flow: send messages until limited → verify graceful response → verify no spam
- [ ] Safety flow: send unsafe content → verify blocked with personality response
- [ ] Pool overload: simulate full pools → verify tier-appropriate behavior

### Step 7.5 — Test Report
- [ ] `REPORT.md` — Generated after test run with:
  - Latency tables (p50, p95, p99 per tier per scenario)
  - Success/shed rates per tier under each load level
  - Observations and trade-off notes

---

## Phase 8: Documentation & Diagrams
**Goal:** Clear documentation for reviewers.

### Step 8.1 — Architecture Diagram
- [ ] Create ASCII or Mermaid diagram showing:
  - Client → API Gateway → Rate Limiter → Safety Filter → Load Balancer → Worker Pools → MongoDB/Redis
  - Analytics pipeline (side channel)
  - Redis connections (rate limit state, pool health, pub/sub)

### Step 8.2 — README.md
- [ ] Setup instructions (docker-compose up)
- [ ] Architecture overview with diagram
- [ ] Design decisions with rationale
- [ ] Trade-offs acknowledged
- [ ] How to run tests
- [ ] How to view test reports

### Step 8.3 — Performance Notes
- [ ] Document index strategy with explain() output
- [ ] Document why each index exists and what queries it supports
- [ ] Include benchmark results from seed data

---

## Execution Order

| Order | Phase | Estimated Complexity | Dependencies |
|-------|-------|---------------------|--------------|
| 1 | Phase 1 (Data Layer) | High | None |
| 2 | Phase 4 (Analytics/Logging) | Medium | Phase 1 (config) |
| 3 | Phase 3 (Rate Limit + Safety) | Medium | Phase 1 (models, Redis) |
| 4 | Phase 2 (Load Balancer) | High | Phase 1, 3 |
| 5 | Phase 5 (API Integration) | Medium | Phase 1-4 |
| 6 | Phase 6 (Docker) | Medium | Phase 5 |
| 7 | Phase 7 (Testing) | High | Phase 6 |
| 8 | Phase 8 (Docs) | Low | Phase 7 |

**Rationale for order:** Data layer first because everything depends on it. Analytics/logging next because they're cross-cutting and needed by all other components. Rate limiting and safety before load balancer because the balancer integrates with them. API layer wires it all together. Docker wraps it for deployment. Tests validate everything. Docs come last when the system is stable.
