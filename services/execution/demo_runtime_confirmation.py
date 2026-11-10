"""Start deadlines for the opt-in runtime; original signed proof stays mandatory."""

import asyncio
import math
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred, weight_scope
from packages.persistence.demo_order_quota import quota_require
from services.execution.demo_managed_order_quota import (
    ManagedQuotaStore,
    ManagedQuotaTPTriggerCoordinator,
)
from services.execution.demo_runtime_guards import RuntimeBoundClient
from services.execution.demo_stream import settled_call

T = TypeVar("T")


class ConfirmationWindowClosed(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ConfirmationWindow:
    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.last: float | None = None
        self.end = self.sample() + 120.0

    def sample(self) -> float:
        try:
            now = self.clock()
        except Exception:
            raise ConfirmationWindowClosed("CLOCK_UNTRUSTED") from None
        if (
            type(now) not in {int, float}
            or not math.isfinite(now)
            or self.last is not None
            and now < self.last
        ):
            raise ConfirmationWindowClosed("CLOCK_UNTRUSTED")
        self.last = now
        return now

    def remaining(self) -> float:
        remaining = self.end - self.sample()
        if remaining <= 0:
            raise ConfirmationWindowClosed("START_DEADLINE_REACHED")
        return remaining


class ConfirmationClaim:
    """Atomic ownership across threads/event loops; completion never resets it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = self._active = False
        self._rejected = 0
        self._closed = False
        self._closed_rejected = 0

    def claim(self) -> bool:
        with self._lock:
            if self._closed:
                self._closed_rejected += 1
                return False
            if self._started:
                self._rejected += 1
                return False
            self._started = self._active = True
            return True

    def finish(self) -> None:
        with self._lock:
            self._active = False

    def close(self) -> bool:
        with self._lock:
            if self._active:
                return False
            self._closed = True
            return True

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return dict(
                confirmation_started=self._started,
                confirmation_call_active=self._active,
                repeated_calls_refused=self._rejected,
                confirmation_closed=self._closed,
                closed_calls_refused=self._closed_rejected,
                start_scope="ONE_CALL_PER_OWNED_COORDINATOR",
            )


class RuntimeConfirmationCoordinator(ManagedQuotaTPTriggerCoordinator):
    def __init__(self, *args: Any, check_confirmation_budget: Callable[[], None], **kwargs: Any):
        super().__init__(*args, **kwargs)
        quota_require(
            isinstance(self.store, ManagedQuotaStore)
            and isinstance(self.client, RuntimeBoundClient),
            "RUNTIME_CONFIRMATION_CONTEXT_REQUIRED",
        )
        assert isinstance(self.store, ManagedQuotaStore)
        assert isinstance(self.client, RuntimeBoundClient)
        self._confirmation_store, self._confirmation_client = self.store, self.client
        self._confirmation_budget, self._confirmation_bridge = (
            self.admission_budget,
            self.quota_bridge,
        )
        self._confirmation_lease, self._confirmation_journal = self.lease, self.quota_bridge.journal
        self._confirmation_base, self._confirmation_engine = (
            self.quota_bridge.store,
            self.store.engine,
        )
        self._confirmation_check_budget = check_confirmation_budget
        self.confirmation_binding_rejections = 0
        self._confirmation_clock: Callable[[], float] = time.monotonic
        self._confirmation_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self._confirmation_claim = ConfirmationClaim()
        self.confirmation_stop_reason = "NOT_STARTED"
        self.confirmation_deferrals = 0

    def _require_confirmation_bindings(self) -> None:
        try:
            quota_require(
                self.store is self._confirmation_store
                and self.client is self._confirmation_client
                and self.admission_budget is self._confirmation_budget
                and self.quota_bridge is self._confirmation_bridge
                and self.lease == self._confirmation_lease
                and self._confirmation_store.bridge is self._confirmation_bridge
                and self._confirmation_store.engine is self._confirmation_engine
                and self._confirmation_base.engine is self._confirmation_engine
                and self._confirmation_bridge.store is self._confirmation_base
                and self._confirmation_bridge.lease == self._confirmation_lease
                and self._confirmation_bridge.journal is self._confirmation_journal,
                "RUNTIME_CONFIRMATION_WIRING_CHANGED",
            )
            quota_require(
                self._confirmation_journal.owns_process_lock(),
                "RUNTIME_CONFIRMATION_ORDER_LOCK_REQUIRED",
            )
            self._confirmation_client._require_binding()
            self._confirmation_check_budget()
        except Exception:
            self.confirmation_binding_rejections += 1
            raise

    def _confirmation_step(self, function: Callable[..., T], *args: Any) -> T:
        self._require_confirmation_bindings()
        return function(*args)

    def close_confirmation_admission(self) -> bool:
        return self._confirmation_claim.close()

    async def _confirmation_pause(self, window: ConfirmationWindow, *, resource: bool) -> None:
        delay = min(1.0, window.remaining())
        assert window.last is not None
        before = window.last
        await self._confirmation_sleep(delay)
        after = window.sample()
        if resource:
            self.proof_resource_wait_seconds += after - before

    async def confirm_exit_budgeted(self) -> str:
        if not self._confirmation_claim.claim():
            return "UNKNOWN"
        try:
            self._require_confirmation_bindings()
            return await self._confirm_exit_owned()
        except Exception:
            self.confirmation_stop_reason = "ORIGINAL_OPERATION_FAILED"
            raise
        finally:
            # Thread stages drain through settled_call before cancellation can
            # leave the owner. Completion still never returns start permission.
            self._confirmation_claim.finish()

    async def _confirm_exit_owned(self) -> str:
        budget = self._confirmation_budget
        completed, last_watch, full_deferred = 0, 0.0, False
        self.confirmation_stop_reason = "RUNNING"
        self.confirmation_deferrals = 0
        try:
            window = ConfirmationWindow(self._confirmation_clock)
            while completed < 4:
                self._require_confirmation_bindings()
                window.remaining()
                await settled_call(
                    self._confirmation_step,
                    self._confirmation_store.renew,
                    self._confirmation_lease,
                )
                self._require_confirmation_bindings()
                window.remaining()
                if not budget._fresh(budget.clock()):
                    with weight_scope("WATCH"):
                        await settled_call(
                            self._confirmation_step, self._confirmation_client.sync_clock
                        )
                self._require_confirmation_bindings()
                window.remaining()
                assert window.last is not None
                if window.last - last_watch >= 10 or not budget.flat_observed:
                    with weight_scope("WATCH"):
                        amount, _ = await settled_call(
                            self._confirmation_step, self._physical_position
                        )
                    self._require_confirmation_bindings()
                    last_watch = window.sample()
                    if amount:
                        self.confirmation_stop_reason = "EXPOSURE_REMAINS"
                        return "UNKNOWN"
                try:
                    self._require_confirmation_bindings()
                    window.remaining()
                    with weight_scope("PROOF"):
                        if full_deferred or (
                            self._uncertain_ticket is None and not self._pending_after_full
                        ):
                            budget.require_proof(self.proof_batch)
                        self._require_confirmation_bindings()
                        window.remaining()
                        result: str = await settled_call(self._confirmation_step, self.confirm_flat)
                        self._require_confirmation_bindings()
                except ProofDeferred:
                    full_deferred = True
                    self.confirmation_deferrals += 1
                    # A stalled injected clock cannot create an unbounded loop.
                    if self.confirmation_deferrals >= 120:
                        self.confirmation_stop_reason = "DEFERRAL_LIMIT_REACHED"
                        return "UNKNOWN"
                    await self._confirmation_pause(window, resource=True)
                    continue
                completed += 1
                self.confirmation_attempts_completed = completed
                full_deferred = False
                if result == "VERIFIED_FLAT":
                    # An already-started original proof is allowed to settle;
                    # never discard a genuine committed proof to fake a timeout.
                    self.budgeted_flat_confirmation = result
                    self.confirmation_stop_reason = "ORIGINAL_PROOF_VERIFIED"
                    return result
                if completed < 4:
                    await self._confirmation_pause(window, resource=False)
        except ConfirmationWindowClosed as error:
            self.confirmation_stop_reason = error.reason
            return "UNKNOWN"
        except asyncio.CancelledError:
            self.confirmation_stop_reason = "CALLER_CANCELLED"
            raise
        except Exception:
            self.confirmation_stop_reason = "ORIGINAL_OPERATION_FAILED"
            raise
        self.confirmation_stop_reason = "ATTEMPT_LIMIT_REACHED"
        return "UNKNOWN"

    def _reported_confirmation_outcome(self, claim: dict[str, Any]) -> str:
        if claim["confirmation_call_active"]:
            return "IN_PROGRESS"
        if not claim["confirmation_started"]:
            return "NOT_STARTED"
        if self.budgeted_flat_confirmation == "VERIFIED_FLAT":
            if self.quota_seal_failed:
                return "PROOF_VERIFIED_QUOTA_SEAL_FAILED"
            if self.session_failed:
                return "PROOF_VERIFIED_SESSION_FAILED"
            if self._confirmation_bridge.degraded:
                return "PROOF_VERIFIED_RUNTIME_DEGRADED"
            return "PROOF_VERIFIED_REVIEW_ONLY"
        if self.confirmation_stop_reason == "CALLER_CANCELLED":
            return "CALLER_CANCELLED_PROOF_UNCONFIRMED"
        if self.confirmation_stop_reason == "START_DEADLINE_REACHED":
            return "START_DEADLINE_PROOF_UNCONFIRMED"
        if self.confirmation_stop_reason in {"ATTEMPT_LIMIT_REACHED", "DEFERRAL_LIMIT_REACHED"}:
            return "LIMIT_REACHED_PROOF_UNCONFIRMED"
        if self.confirmation_binding_rejections:
            return "CONTEXT_REJECTED_PROOF_UNCONFIRMED"
        if self.confirmation_stop_reason == "ORIGINAL_OPERATION_FAILED":
            return "OPERATION_FAILED_PROOF_UNCONFIRMED"
        return "PROOF_UNCONFIRMED"

    def confirmation_diagnostics(self) -> dict[str, Any]:
        claim = self._confirmation_claim.diagnostics()
        return dict(
            **claim,
            reported_outcome=self._reported_confirmation_outcome(claim),
            returned_flat_confirmation=(
                "VERIFIED_FLAT" if self.budgeted_flat_confirmation == "VERIFIED_FLAT" else "UNKNOWN"
            ),
            quota_seal_failed=self.quota_seal_failed,
            session_failed=self.session_failed,
            bridge_degraded=self._confirmation_bridge.degraded,
            completed_confirmation_attempts=self.confirmation_attempts_completed,
            quota_seal_verified_from_records=False,
            financial_database_rechecked=False,
            next_action="KEEP_PAUSED_REVIEW_ONLY",
            binding_rejections=self.confirmation_binding_rejections,
            start_deadline_seconds=120,
            completed_attempt_limit=4,
            deferral_limit=120,
            deferrals=self.confirmation_deferrals,
            stop_reason=self.confirmation_stop_reason,
            in_flight_work_force_canceled=False,
            whole_function_wall_time_limit_proven=False,
            execution_approved=False,
        )
