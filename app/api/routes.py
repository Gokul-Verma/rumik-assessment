"""
API routes for Ira backend.
"""

from pydantic import BaseModel, Field
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.analytics import pipeline as analytics
from app.workers.processor import ChatRequest

router = APIRouter()


class ChatInput(BaseModel):
    user_id: str
    content: str
    platform: str = "app"


class ChatOutput(BaseModel):
    message: str
    session_id: str
    processing_time_ms: float
    rate_limited: bool = False
    safety_blocked: bool = False
    metadata: dict = Field(default_factory=dict)


@router.post("/chat", response_model=ChatOutput)
async def chat(body: ChatInput, request: Request):
    """Main chat endpoint. Processes a user message through the full pipeline."""
    processor = request.app.state.processor
    correlation_id = getattr(request.state, "correlation_id", "")

    chat_request = ChatRequest(
        user_id=body.user_id,
        content=body.content,
        platform=body.platform,
        correlation_id=correlation_id,
    )

    result = await processor.process(chat_request)

    if result.shed:
        return JSONResponse(
            status_code=503,
            content={
                "message": result.message,
                "session_id": "",
                "processing_time_ms": result.processing_time_ms,
                "rate_limited": False,
                "safety_blocked": False,
                "metadata": {"retry_after": 5},
            },
            headers={"Retry-After": "5"},
        )

    if result.rate_limited and not result.message:
        # Silent rate limit — no message body
        return JSONResponse(
            status_code=429,
            content={
                "message": "",
                "session_id": result.session_id,
                "processing_time_ms": result.processing_time_ms,
                "rate_limited": True,
                "safety_blocked": False,
                "metadata": result.metadata,
            },
            headers={"Retry-After": str(result.metadata.get("retry_after", 60))},
        )

    return ChatOutput(
        message=result.message,
        session_id=result.session_id,
        processing_time_ms=result.processing_time_ms,
        rate_limited=result.rate_limited,
        safety_blocked=result.safety_blocked,
        metadata=result.metadata,
    )


@router.get("/health")
async def health(request: Request):
    """System health check."""
    pool_manager = request.app.state.pool_manager
    return {
        "status": "ok",
        "pools": pool_manager.status(),
        "analytics_queue_depth": analytics.get_queue_depth(),
        "analytics_drops": analytics.get_drop_count(),
    }


@router.get("/health/pools")
async def pool_health(request: Request):
    """Detailed pool health metrics."""
    pool_manager = request.app.state.pool_manager
    health_tracker = request.app.state.health_tracker
    pools = pool_manager.status()
    for name in pools:
        pools[name]["trend"] = health_tracker.trend(name)
    return pools


@router.get("/admin/stats")
async def admin_stats(request: Request):
    """Aggregate analytics stats."""
    db = request.app.state.db
    pipeline = [
        {"$group": {
            "_id": "$event_type",
            "count": {"$sum": 1},
            "avg_duration_ms": {"$avg": "$duration_ms"},
        }},
        {"$sort": {"count": -1}},
    ]
    stats = await db.analytics.aggregate(pipeline).to_list(length=50)
    return {"stats": stats}
