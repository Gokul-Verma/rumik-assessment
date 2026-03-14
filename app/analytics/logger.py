"""
Structured logging with correlation IDs and context binding.
Uses structlog for JSON-formatted, non-blocking log output.
"""

import contextvars
import logging
import uuid

import structlog

from app.config import settings

# Context variable for correlation ID — set per request
correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=""
)


def generate_correlation_id() -> str:
    return str(uuid.uuid4())


def setup_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**kwargs) -> structlog.BoundLogger:
    """Get a logger with optional pre-bound context."""
    log = structlog.get_logger()
    cid = correlation_id_var.get()
    if cid:
        log = log.bind(correlation_id=cid)
    if kwargs:
        log = log.bind(**kwargs)
    return log
