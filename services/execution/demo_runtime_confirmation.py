"""Start deadlines for the opt-in runtime; original signed proof stays mandatory."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from typing import Any

from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred, weight_scope
from services.execution.demo_managed_order_quota import ManagedQuotaTPTriggerCoordinator


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


class RuntimeConfirmationCoordinator(ManagedQuotaTPTriggerCoordinator):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._confirmation_clock: Callable[[], float] = time.monotonic
        self._confirmation_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
        self.confirmation_stop_reason = "NOT_STARTED"
        self.confirmation_deferrals = 0

    async def _confirmation_pause(self, window: ConfirmationWindow, *, resource: bool) -> None:
        delay = min(1.0, window.remaining())
        assert window.last is not None
        before = window.last
        await self._confirmation_sleep(delay)
        after = window.sample()
        if resource:
            self.proof_resource_wait_seconds += after - before

    async def confirm_exit_budgeted(self) -> str:
        budget = self.admission_budget
        completed, last_watch, full_deferred = 0, 0.0, False
        self.confirmation_stop_reason = "RUNNING"
        self.confirmation_deferrals = 0
        try:
            window = ConfirmationWindow(self._confirmation_clock)
            while completed < 4:
                window.remaining()
                await asyncio.to_thread(self.store.renew, self.lease)
                window.remaining()
                if not budget._fresh(budget.clock()):
                    with weight_scope("WATCH"):
                        await asyncio.to_thread(self.client.sync_clock)
                window.remaining()
                assert window.last is not None
                if window.last - last_watch >= 10 or not budget.flat_observed:
                    with weight_scope("WATCH"):
                        amount, _ = await asyncio.to_thread(self._physical_position)
                    last_watch = window.sample()
                    if amount:
                        self.confirmation_stop_reason = "EXPOSURE_REMAINS"
                        return "UNKNOWN"
                try:
                    window.remaining()
                    with weight_scope("PROOF"):
                        if full_deferred or (
                            self._uncertain_ticket is None and not self._pending_after_full
                        ):
                            budget.require_proof(self.proof_batch)
                        window.remaining()
                        result = await asyncio.to_thread(self.confirm_flat)
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

    def confirmation_diagnostics(self) -> dict[str, Any]:
        return dict(
            start_deadline_seconds=120,
            completed_attempt_limit=4,
            deferral_limit=120,
            deferrals=self.confirmation_deferrals,
            stop_reason=self.confirmation_stop_reason,
            in_flight_work_force_canceled=False,
            whole_function_wall_time_limit_proven=False,
            execution_approved=False,
        )
