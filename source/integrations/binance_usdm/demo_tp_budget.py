"""Candidate DEMO-only admission budget. Costs are assumptions, never IP reservations.

Not enabled by any CLI. Do not reset credit on local minute changes or lower
headers: the guard deliberately budgets the entire bounded observation. Cleanup
keeps the existing SDK cooldown/authorization; this guard never retries requests.
"""

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import parse_qs

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoFailure, Transport
from integrations.binance_usdm.demo_rate_limits import rate_headers
from integrations.binance_usdm.rest import RESTResponse


class TPBudgetStopped(DemoFailure):
    """Local admission failure, distinct from an exchange 418/429."""


def published_weight_limit(info: Any) -> int:
    if not isinstance(info, dict) or not isinstance(info.get("rateLimits"), list):
        raise TPBudgetStopped("TP budget metadata unavailable")
    rows = [
        r
        for r in info["rateLimits"]
        if isinstance(r, dict)
        and r.get("rateLimitType") == "REQUEST_WEIGHT"
        and r.get("interval") == "MINUTE"
        and r.get("intervalNum") == 1
        and type(r.get("intervalNum")) is int
    ]
    if len(rows) != 1 or type(rows[0].get("limit")) is not int or rows[0]["limit"] <= 0:
        raise TPBudgetStopped("TP budget metadata ambiguous")
    return int(rows[0]["limit"])


@dataclass(frozen=True)
class TPBudgetPlan:
    published_limit: int
    entry_and_protection: int
    cleanup_reserve: int
    headroom: int
    header_max_age_seconds: float = 5.0

    def __post_init__(self) -> None:
        for number in (
            self.published_limit,
            self.entry_and_protection,
            self.cleanup_reserve,
            self.headroom,
        ):
            if type(number) is not int or number <= 0:
                raise ValueError("TP budget requires positive integer assumptions")
        if self.entry_and_protection + self.cleanup_reserve + self.headroom >= self.published_limit:
            raise ValueError("TP budget assumptions leave no admission space")
        if not 0 < self.header_max_age_seconds <= 5:
            raise ValueError("TP budget header freshness must be at most five seconds")

    @property
    def ordinary_ceiling(self) -> int:
        return self.published_limit - self.cleanup_reserve - self.headroom


class TPRequestBudget:
    def __init__(
        self,
        sender: Transport,
        plan: TPBudgetPlan,
        costs: Mapping[tuple[str, str], int],
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not costs or any(type(n) is not int or n <= 0 for n in costs.values()):
            raise ValueError("TP budget requires explicit positive route assumptions")
        self.original = sender
        self.plan = plan
        self._costs = dict(costs)
        self._now = monotonic
        self._lock = threading.Lock()
        self._used = self._attempts = self._denials = 0
        self._phase = "ORDINARY"
        self._header_at: float | None = None
        self._last_now: float | None = None
        self._untrusted = False
        self._admitted = False
        self._stopped = False
        self._stop_reason = "NONE"
        self._preparation_checks = 0
        self._last_preparation_assumption: int | None = None
        self._weights = {"ORDINARY": 0, "CLEANUP": 0}
        self._cleanup_over_model = False

    def _clock(self) -> float:
        now = self._now()
        if self._last_now is not None and now < self._last_now:
            self._untrusted = True
        self._last_now = now
        return now

    def _stop(self, reason: str = "BUDGET_UNAVAILABLE") -> NoReturn:
        if self._stop_reason == "NONE":
            self._stop_reason = reason
        self._stopped = True
        self._denials += 1
        raise TPBudgetStopped("TP request budget exhausted or untrusted; keep paused")

    def _fresh(self, now: float) -> bool:
        return bool(
            not self._untrusted
            and self._header_at is not None
            and 0 <= now - self._header_at <= self.plan.header_max_age_seconds
        )

    def precheck_preparation(self, assumed_preparation_cost: int) -> None:
        """Deny a doomed preparation early; never grants admission or reserves IP credit.

        The explicit assumption covers work until admit_entry(), not any of its
        entry/protection allowance. Actual requests are still charged normally.
        Both the original reserve-time admission and per-request checks remain.
        """
        if type(assumed_preparation_cost) is not int or assumed_preparation_cost <= 0:
            raise ValueError("TP preparation requires an explicit positive integer assumption")
        with self._lock:
            now = self._clock()
            self._preparation_checks += 1
            self._last_preparation_assumption = assumed_preparation_cost
            if self._phase != "ORDINARY" or self._stopped or self._admitted:
                self._stop("PREPARATION_SCOPE_OR_ALREADY_STOPPED")
            if not self._fresh(now):
                self._stop("PREPARATION_WEIGHT_METADATA_UNAVAILABLE")
            if (
                self._used + assumed_preparation_cost + self.plan.entry_and_protection
                > self.plan.ordinary_ceiling
            ):
                self._stop("PREPARATION_LIFECYCLE_NO_HEADROOM")

    def admit_entry(self) -> None:
        with self._lock:
            now = self._clock()
            if self._phase != "ORDINARY" or self._stopped or self._admitted:
                self._stop("ENTRY_SCOPE_OR_ALREADY_STOPPED")
            if not self._fresh(now):
                self._stop("ENTRY_WEIGHT_METADATA_UNAVAILABLE")
            if self._used + self.plan.entry_and_protection > self.plan.ordinary_ceiling:
                self._stop("ENTRY_LIFECYCLE_NO_HEADROOM")
            self._admitted = True

    def begin_cleanup(self) -> None:
        """Sticky transition only after the stream and its workers have drained."""
        with self._lock:
            self._phase = "CLEANUP"

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def __call__(
        self, method: str, path: str, query: SecretStr, api_key: SecretStr | None
    ) -> RESTResponse:
        # Inspect only the admission discriminator, never retain/log query or keys.
        is_entry = False
        if method == "POST" and path == "/fapi/v1/order":
            params = parse_qs(query.get_secret_value(), keep_blank_values=True)
            is_entry = params.get("reduceOnly") != ["true"]
        with self._lock:
            now = self._clock()
            cost = self._costs.get((method, path))
            if cost is None:
                self._stop("ROUTE_COST_UNKNOWN")  # No implicit cheap fallback, including cleanup.
            if is_entry and (self._phase != "ORDINARY" or not self._admitted):
                self._stop("ENTRY_FORBIDDEN")
            if self._phase == "ORDINARY":
                # A clock GET can refresh stale metadata, but cannot restore stopped credit.
                clock_probe = method == "GET" and path == "/fapi/v1/time"
                if self._stopped or self._untrusted:
                    self._stop("WEIGHT_METADATA_UNTRUSTED")
                if not clock_probe and not self._fresh(now):
                    self._stop("WEIGHT_HEADER_STALE")
                if self._used + cost > self.plan.ordinary_ceiling:
                    self._stop("ORDINARY_HEADROOM_EXHAUSTED")
            self._used += cost
            self._weights[self._phase] += cost
            self._attempts += 1
            if self._phase == "CLEANUP" and self._used > self.plan.published_limit:
                self._cleanup_over_model = True
        try:
            response = self.original(method, path, query, api_key)
        except Exception:
            # A possibly-sent call is charged, never automatically retried or refunded.
            with self._lock:
                self._untrusted = True
            raise
        metadata = rate_headers(response.headers)
        with self._lock:
            now = self._clock()
            header = metadata.get("x-mbx-used-weight-1m")
            if header is None:
                self._untrusted = True
            else:
                self._used = max(self._used, int(header))
                self._header_at = now
            if response.status in {418, 429}:
                self._untrusted = True
            if self._phase == "CLEANUP" and self._used > self.plan.published_limit:
                self._cleanup_over_model = True
        return response  # SDK retains its own response validation and cooldown handling.

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "tp_request_budget": "CANDIDATE_NON_RENEWING_OBSERVATION_BUDGET",
                "published_limit": self.plan.published_limit,
                "ordinary_ceiling": self.plan.ordinary_ceiling,
                "entry_protection_assumption": self.plan.entry_and_protection,
                "preparation_prechecks": self._preparation_checks,
                "last_preparation_assumption": self._last_preparation_assumption,
                "cleanup_reserve_assumption": self.plan.cleanup_reserve,
                "headroom_assumption": self.plan.headroom,
                "used_conservative_model": self._used,
                "phase": self._phase,
                "forwarded_attempts": self._attempts,
                "denials": self._denials,
                "ordinary_assumed_weight": self._weights["ORDINARY"],
                "cleanup_assumed_weight": self._weights["CLEANUP"],
                "cleanup_exceeded_model_limit": self._cleanup_over_model,
                "admitted": self._admitted,
                "stopped": self._stopped,
                "stop_reason": self._stop_reason,
                "metadata_untrusted": self._untrusted,
                "resets_or_refunds": 0,
                "added_retries": 0,
                "shared_ip_capacity_reserved": False,
                "endpoint_weights_proven": False,
            }
