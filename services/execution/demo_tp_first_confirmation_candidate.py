"""Uninstalled candidate: acknowledged pending cleanup can defer the first VERIFY.

The initial complete historical pair and original fenced cleanup are unchanged.
A pending probe can only return UNKNOWN. Every successful final confirmation
still executes the original entire confirmation, including its final snapshots.
"""

import time
from collections.abc import Callable

from integrations.binance_usdm.demo_tp_admission import TPAdmissionBudget
from packages.persistence.demo import DemoStore
from services.execution.demo_flat_transport import ConfirmationClient
from services.execution.demo_tp_fenced_handoff_candidate import FencedHandoffTPTriggerCoordinator
from services.execution.demo_tp_staged_confirmation_candidate import StagedConfirmationTPClient
from services.execution.models import Lease


class _FirstCleanupPending(Exception):
    """Private local control flow; never denotes an SDK failure or exchange success."""


class FirstConfirmationTPTriggerCoordinator(FencedHandoffTPTriggerCoordinator):
    def __init__(
        self,
        store: DemoStore,
        lease: Lease,
        client: StagedConfirmationTPClient,
        budget: TPAdmissionBudget,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        super().__init__(store, lease, client, budget, monotonic=monotonic)
        self._confirmation_started = False
        self._first_confirmation_source: StagedConfirmationTPClient | None = None
        self.first_pending_deferrals = 0
        self.first_pending_checks = 0

    def _cleanup(self, key: str) -> None:
        super()._cleanup(key)  # Original fresh position proof and fenced DELETE.
        source = self._first_confirmation_source
        if source is None:
            return
        bounded = self.client
        if not isinstance(bounded, ConfirmationClient):
            return
        self.first_pending_checks += 1
        # This uses the existing bounded, fenced read-only pending probe, with
        # the explicit original source because the full proof temporarily binds
        # self.client to its ConfirmationClient. No client/transport swap occurs.
        if self._pending_protection(source, observer=bounded):
            raise _FirstCleanupPending

    def confirm_flat(self) -> str:
        with self._handoff_lock:
            first = not self._confirmation_started
            self._confirmation_started = True
            if first:
                if not isinstance(self.client, StagedConfirmationTPClient):
                    raise ValueError("Explicit first-confirmation source required")
                self._first_confirmation_source = self.client
            try:
                return super().confirm_flat()
            except _FirstCleanupPending:
                # Original QUERY pair and cleanup have already committed under
                # fencing. Do not execute the three full VERIFY snapshots while
                # the known successful cancellation is still reported NEW.
                self.first_pending_deferrals += 1
                self._pending_after_full = True
                self.flat_confirmation_stage = "CANDIDATE_FIRST_CLEANUP_PENDING"
                self.flat_confirmation_diagnostic["candidate_first_cleanup_pending"] = 1
                return "UNKNOWN"
            finally:
                self._first_confirmation_source = None

    def first_confirmation_diagnostics(self) -> dict[str, str | int | bool]:
        return {
            "candidate_first_confirmation": "PENDING_CAN_ONLY_DEFER_UNKNOWN",
            "checks_after_original_cleanup": self.first_pending_checks,
            "first_pending_deferrals": self.first_pending_deferrals,
            "subsequent_pending_deferrals": self.pending_confirmation_deferrals,
            "original_full_confirmation_required_for_success": True,
            "actual_execution_approved": False,
        }
