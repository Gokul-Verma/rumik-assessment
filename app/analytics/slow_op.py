"""
Slow operation detection decorator.
Auto-logs a WARNING when an async operation exceeds the configured threshold.
"""

import functools
import time

from app.analytics.logger import get_logger
from app.config import settings


def track_slow(threshold_ms: int | None = None):
    """
    Decorator that measures execution time and logs slow operations.

    Usage:
        @track_slow(threshold_ms=200)
        async def my_operation():
            ...
    """
    threshold = threshold_ms or settings.slow_op_threshold_ms

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            start = time.monotonic()
            try:
                return await func(*args, **kwargs)
            finally:
                elapsed_ms = (time.monotonic() - start) * 1000
                if elapsed_ms > threshold:
                    log = get_logger(operation=func.__qualname__)
                    log.warning(
                        "slow_operation",
                        duration_ms=round(elapsed_ms, 2),
                        threshold_ms=threshold,
                    )
        return wrapper
    return decorator
