"""
Tier-aware request router.

Routing policy:
- Enterprise → priority pool (always). If full, steal from general (never shed).
- Premium → general pool. If >80% full, use overflow. Never shed.
- Free → general pool. If >60% full, route to overflow. If overflow >80%, shed probabilistically.

Load shedding for free tier is probabilistic (linear ramp, not cliff-edge)
to ensure graceful degradation rather than abrupt cutoffs.
"""

import random
from dataclasses import dataclass

from app.analytics import pipeline as analytics
from app.analytics.logger import get_logger
from app.balancer.pools import PoolManager, WorkerPool
from app.models.user import Tier


@dataclass
class RoutingResult:
    pool: WorkerPool | None
    shed: bool  # True if request should be dropped
    reason: str = ""


class TierRouter:
    def __init__(self, pool_manager: PoolManager):
        self.pm = pool_manager

    def route(self, tier: str, correlation_id: str = "") -> RoutingResult:
        """
        Route a request to the appropriate worker pool based on tier and load.
        """
        log = get_logger(tier=tier, correlation_id=correlation_id)

        if tier == Tier.ENTERPRISE:
            return self._route_enterprise(log, correlation_id)
        elif tier == Tier.PREMIUM:
            return self._route_premium(log, correlation_id)
        else:
            return self._route_free(log, correlation_id)

    def _route_enterprise(self, log, correlation_id: str) -> RoutingResult:
        """Enterprise: priority pool, fallback to general. Never shed."""
        pool = self.pm.priority
        if pool.available > 0:
            analytics.track("pool_routed", tier="enterprise", extra={"pool": "priority"}, correlation_id=correlation_id)
            return RoutingResult(pool=pool, shed=False)

        # Fallback: steal from general pool
        log.warning("priority_pool_full", fallback="general")
        pool = self.pm.general
        analytics.track("pool_routed", tier="enterprise", extra={"pool": "general", "fallback": True}, correlation_id=correlation_id)
        return RoutingResult(pool=pool, shed=False)

    def _route_premium(self, log, correlation_id: str) -> RoutingResult:
        """Premium: general pool, overflow if needed. Never shed."""
        general = self.pm.general
        overflow = self.pm.overflow

        if general.utilization <= 0.8:
            analytics.track("pool_routed", tier="premium", extra={"pool": "general"}, correlation_id=correlation_id)
            return RoutingResult(pool=general, shed=False)

        # General pool >80% — use overflow
        log.info("general_pool_high", utilization=general.utilization, fallback="overflow")
        analytics.track("pool_routed", tier="premium", extra={"pool": "overflow"}, correlation_id=correlation_id)
        return RoutingResult(pool=overflow, shed=False)

    def _route_free(self, log, correlation_id: str) -> RoutingResult:
        """Free: general pool, overflow, then probabilistic shedding."""
        general = self.pm.general
        overflow = self.pm.overflow

        if general.utilization <= 0.6:
            analytics.track("pool_routed", tier="free", extra={"pool": "general"}, correlation_id=correlation_id)
            return RoutingResult(pool=general, shed=False)

        # General >60% — try overflow
        if overflow.utilization <= 0.8:
            log.info("free_routed_overflow", general_util=general.utilization)
            analytics.track("pool_routed", tier="free", extra={"pool": "overflow"}, correlation_id=correlation_id)
            return RoutingResult(pool=overflow, shed=False)

        # Overflow >80% — probabilistic shedding
        # Linear ramp: 0% shed at 80% load, 100% shed at 100% load
        shed_probability = max(0.0, (overflow.utilization - 0.8) / 0.2)
        if random.random() < shed_probability:
            log.warning("free_shed", overflow_util=overflow.utilization, shed_prob=shed_probability)
            analytics.track("load_shed", tier="free", extra={"probability": shed_probability}, correlation_id=correlation_id)
            return RoutingResult(pool=None, shed=True, reason="system_busy")

        # Lucky — still gets through
        analytics.track("pool_routed", tier="free", extra={"pool": "overflow", "under_pressure": True}, correlation_id=correlation_id)
        return RoutingResult(pool=overflow, shed=False)
