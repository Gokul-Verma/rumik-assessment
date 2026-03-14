"""
Request middleware: correlation ID injection, request timing.
"""

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.analytics.logger import correlation_id_var, get_logger


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Accept existing correlation ID or generate new one
        cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        correlation_id_var.set(cid)
        request.state.correlation_id = cid

        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Processing-Time-Ms"] = str(round(elapsed_ms, 2))

        log = get_logger()
        log.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round(elapsed_ms, 2),
        )

        return response
