"""Uninstalled candidate. Explicit model assumptions are never an IP reservation.

Default entry admission is disabled. Test-only modeled execution must be explicit;
no CLI enables it. No credit is replenished at a minute boundary or on lower headers.
"""

import math
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


class TPAdmissionStopped(DemoFailure):
    """Local budget gate; never represents an exchange rejection or failed POST."""


@dataclass(frozen=True)
class TPModelProfile:
    historical_orders: int
    max_income_pages: int = 10
    flat_attempts: int = 4
    headroom: int = 300

    def __post_init__(self) -> None:
        if type(self.historical_orders) is not int or not 0 <= self.historical_orders <= 128:
            raise ValueError("History model outside candidate scope")
        if (
            self.max_income_pages != 10
            or type(self.max_income_pages) is not int
            or self.flat_attempts != 4
            or type(self.flat_attempts) is not int
            or type(self.headroom) is not int
            or self.headroom <= 0
        ):
            raise ValueError("Strict ten-page/four-attempt assumptions required")

    @property
    def snapshot(self) -> int:
        # Current global reads100; up to ten income pages30; six original-ID /
        # possible algo-execution queries per historical/current order. Assumption only.
        return 100 + 30 * self.max_income_pages + 6 * (self.historical_orders + 1)

    @property
    def preparation(self) -> int:
        return 4 * self.snapshot + 200

    @property
    def entry(self) -> int:
        return 8 * self.snapshot + 400

    @property
    def cleanup(self) -> int:
        return self.flat_attempts * (16 * self.snapshot + 400)

    def report(self) -> dict[str, int | bool]:
        return {
            "historical_orders": self.historical_orders,
            "max_income_pages": self.max_income_pages,
            "flat_attempts": self.flat_attempts,
            "snapshot_assumption": self.snapshot,
            "preparation_assumption": self.preparation,
            "entry_protection_assumption": self.entry,
            "cleanup_assumption": self.cleanup,
            "headroom_assumption": self.headroom,
            "is_proven_upper_bound": False,
        }


def published_limit(info: Any) -> int:
    if not isinstance(info, dict) or not isinstance(info.get("rateLimits"), list):
        raise TPAdmissionStopped("WEIGHT_LIMIT_UNAVAILABLE")
    rows = [
        r
        for r in info["rateLimits"]
        if isinstance(r, dict)
        and r.get("rateLimitType") == "REQUEST_WEIGHT"
        and r.get("interval") == "MINUTE"
        and type(r.get("intervalNum")) is int
        and r["intervalNum"] == 1
    ]
    if len(rows) != 1 or type(rows[0].get("limit")) is not int or rows[0]["limit"] <= 0:
        raise TPAdmissionStopped("WEIGHT_LIMIT_UNAVAILABLE")
    return int(rows[0]["limit"])


class TPAdmissionBudget:
    def __init__(
        self,
        sender: Transport,
        profile: TPModelProfile,
        costs: Mapping[tuple[str, str], int],
        *,
        allow_synthetic_model_execution: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if (
            type(allow_synthetic_model_execution) is not bool
            or not costs
            or any(type(n) is not int or n <= 0 for n in costs.values())
        ):
            raise ValueError("Explicit positive route assumptions required")
        self.sender, self.profile = sender, profile
        self.costs = dict(costs)
        self.synthetic = allow_synthetic_model_execution
        self.clock = monotonic
        self.lock = threading.RLock()
        self.phase = "BOOTSTRAP"
        self.limit: int | None = None
        self.used = 0
        self.header_at: float | None = None
        self.last_time: float | None = None
        self.untrusted = False
        self.stopped = False
        self.reason = "NONE"
        self.prechecked = False
        self.admitted = False
        self.entry_sent = False
        self.entry_owner: int | None = None
        self.weights = {
            "BOOTSTRAP": 0,
            "PREPARATION": 0,
            "ENTRY_PROTECTION": 0,
            "OBSERVATION": 0,
            "CLEANUP": 0,
        }
        self.forwarded = 0
        self.denied = 0
        self.entry_overrun = False
        self.cleanup_overrun = False

    def _time(self) -> float:
        now = self.clock()
        if not math.isfinite(now) or self.last_time is not None and now < self.last_time:
            self._mark("MONOTONIC_CLOCK_UNTRUSTED")
            self.untrusted = True
        self.last_time = now
        return now

    def _mark(self, reason: str) -> None:
        self.stopped = True
        if self.reason == "NONE":
            self.reason = reason

    def _deny(self, reason: str) -> NoReturn:
        self._mark(reason)
        self.denied += 1
        raise TPAdmissionStopped("TP local admission stopped; preserve original cleanup")

    def _fresh(self, now: float) -> bool:
        return not self.untrusted and self.header_at is not None and 0 <= now - self.header_at <= 5

    def configure(self, info: Any) -> None:
        with self.lock:
            if self.phase != "BOOTSTRAP" or self.limit is not None:
                self._deny("LIMIT_REBIND_FORBIDDEN")
            self.limit = published_limit(info)

    def preflight(self) -> None:
        with self.lock:
            now = self._time()
            if self.phase != "BOOTSTRAP" or self.limit is None or self.stopped:
                self._deny("PREFLIGHT_SCOPE_INVALID")
            if not self._fresh(now):
                self._deny("WEIGHT_METADATA_UNAVAILABLE")
            required = (
                self.profile.preparation
                + self.profile.entry
                + self.profile.cleanup
                + self.profile.headroom
            )
            if self.used + required > self.limit:
                self._deny("MODEL_LIFECYCLE_DOES_NOT_FIT")
            if not self.synthetic:
                self._deny("UNPROVEN_MODEL_REAL_EXECUTION_DISABLED")
            self.phase = "PREPARATION"

    def before_preparation(self) -> None:
        with self.lock:
            now = self._time()
            if self.phase != "PREPARATION" or self.stopped or self.limit is None:
                self._deny("PREPARATION_SCOPE_INVALID")
            if not self._fresh(now):
                self._deny("WEIGHT_METADATA_UNAVAILABLE")
            if (
                self.used
                + self.profile.preparation
                + self.profile.entry
                + self.profile.cleanup
                + self.profile.headroom
                > self.limit
            ):
                self._deny("PREPARATION_NO_HEADROOM")
            self.prechecked = True

    def admit_entry(self) -> None:
        with self.lock:
            now = self._time()
            if (
                self.phase != "PREPARATION"
                or not self.prechecked
                or self.admitted
                or self.stopped
                or self.limit is None
            ):
                self._deny("ENTRY_SCOPE_INVALID")
            if not self._fresh(now):
                self._deny("WEIGHT_METADATA_UNAVAILABLE")
            if (
                self.used + self.profile.entry + self.profile.cleanup + self.profile.headroom
                > self.limit
            ):
                self._deny("ENTRY_PROTECTION_NO_HEADROOM")
            self.admitted = True
            self.entry_owner = threading.get_ident()
            self.phase = "ENTRY_PROTECTION"

    def finish_entry(self, *, raise_on_stop: bool = True) -> None:
        with self.lock:
            if self.phase == "ENTRY_PROTECTION":
                self.phase = "OBSERVATION"
                self.entry_owner = None
            if raise_on_stop and self.stopped:
                self._deny("ENTRY_MODEL_EXCEEDED_OR_UNTRUSTED")

    def begin_cleanup(self) -> None:
        with self.lock:
            self.phase = "CLEANUP"
            self.entry_owner = None

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        params = (
            parse_qs(query.get_secret_value(), keep_blank_values=True)
            if method == "POST" and path == "/fapi/v1/order"
            else {}
        )
        entry = (
            method == "POST" and path == "/fapi/v1/order" and params.get("reduceOnly") != ["true"]
        )
        key_close = method == "DELETE" and path == "/fapi/v1/listenKey"
        with self.lock:
            now = self._time()
            cost = self.costs.get((method, path))
            if cost is None:
                self._deny("UNMODELED_ROUTE")
            protected_phase = (
                self.phase == "ENTRY_PROTECTION" and self.entry_owner == threading.get_ident()
            )
            safety_cleanup = self.phase == "CLEANUP" or key_close
            if entry:
                if (
                    not self.synthetic
                    or not protected_phase
                    or not self.admitted
                    or self.entry_sent
                    or self.stopped
                    or params.get("type") != ["LIMIT"]
                ):
                    self._deny("NEW_ENTRY_FORBIDDEN")
                self.entry_sent = (
                    True  # Charge possible-send exactly once, including transport failure.
                )
            if self.phase == "BOOTSTRAP":
                if (
                    method != "GET"
                    or path not in {"/fapi/v1/time", "/fapi/v1/exchangeInfo"}
                    or self.stopped
                ):
                    self._deny("BOOTSTRAP_READ_SCOPE_REQUIRED")
            elif not safety_cleanup and not protected_phase:
                if self.stopped or self.untrusted:
                    self._deny("ORDINARY_STOPPED")
                if not self._fresh(now) and (method, path) != ("GET", "/fapi/v1/time"):
                    self._deny("WEIGHT_HEADER_STALE")
                reserved = self.profile.cleanup + self.profile.headroom
                if self.phase == "PREPARATION" or self.phase == "ENTRY_PROTECTION":
                    reserved += self.profile.entry
                if self.limit is None or self.used + cost > self.limit - reserved:
                    self._deny("ORDINARY_CLEANUP_RESERVE_REACHED")
            charged = "CLEANUP" if key_close else self.phase
            self.used += cost
            self.weights[charged] += cost
            self.forwarded += 1
            if protected_phase and self.weights["ENTRY_PROTECTION"] > self.profile.entry:
                self.entry_overrun = True
                self._mark("ENTRY_PROTECTION_MODEL_OVERRUN")
            if self.weights["CLEANUP"] > self.profile.cleanup:
                self.cleanup_overrun = True
                self._mark("CLEANUP_MODEL_OVERRUN")
        try:
            response = self.sender(method, path, query, key)
        except Exception:
            with self.lock:
                self.untrusted = True
                self._mark("POSSIBLE_SEND_TRANSPORT_FAILURE")
            raise
        metadata = rate_headers(response.headers)
        with self.lock:
            now = self._time()
            value = metadata.get("x-mbx-used-weight-1m")
            if value is None:
                self.untrusted = True
                self._mark("WEIGHT_HEADER_MISSING")
            else:
                self.used = max(self.used, int(value))
                self.header_at = now
            if response.status in {418, 429}:
                self.untrusted = True
                self._mark("VENUE_RATE_LIMITED")
        return response  # No response/body conversion, retries, or SDK cooldown override.

    def diagnostics(self) -> dict[str, Any]:
        with self.lock:
            return {
                "tp_admission_candidate": "NON_RENEWING_MODEL_ONLY",
                "phase": self.phase,
                "published_limit": self.limit,
                "used_non_refunding_model": self.used,
                "profile": self.profile.report(),
                "weights": dict(self.weights),
                "forwarded_attempts": self.forwarded,
                "denials": self.denied,
                "model_entry_admitted": self.admitted,
                "entry_post_forwarded": self.entry_sent,
                "stopped": self.stopped,
                "reason": self.reason,
                "entry_protection_overrun": self.entry_overrun,
                "cleanup_overrun": self.cleanup_overrun,
                "metadata_untrusted": self.untrusted,
                "added_retries": 0,
                "minute_reset_credit": 0,
                "shared_ip_capacity_reserved": False,
                "cost_upper_bounds_proven": False,
                "actual_tp_execution_approved": False,
                "synthetic_execution_explicit": self.synthetic,
            }
