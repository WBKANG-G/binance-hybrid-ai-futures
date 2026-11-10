"""Pause only flat owned observations while waiting for conservative shared credit."""

import time
from collections.abc import Callable
from typing import Any

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred, weight_scope
from packages.persistence.demo_shared_weight import RESERVES
from services.execution.demo_runtime_confirmation import (
    ConfirmationWindow,
    ConfirmationWindowClosed,
    RuntimeConfirmationCoordinator,
)


class RuntimeObservationCoordinator(RuntimeConfirmationCoordinator):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.observation_deferrals = 0
        self._observation_clock: Callable[[], float] = time.monotonic
        self._observation_sleep: Callable[[float], None] = time.sleep

    def _require_observation_session(self) -> None:
        self._require_confirmation_bindings()
        if self.session_failed:
            raise DemoFailure("Observation session failed; ordinary reconciliation blocked")

    def _require_owned_flat_observation(self) -> None:
        self._require_observation_session()
        ledger = self.store.read(self.lease.account_id)
        own = ledger.orders.get(str(self._own_operation))
        if (
            self._own_operation is None
            or own is None
            or own.remaining
            or own.open_quantity
            or own.position_state.value != "CLOSED"
            or any(ledger.positions.values())
            or any(o.remaining or o.open_quantity for o in ledger.orders.values())
        ):
            raise DemoFailure("Owned flat observation required for resource waiting")

    def _wait_for_observation_credit(self) -> None:
        budget = self.admission_budget
        window = ConfirmationWindow(self._observation_clock)
        last_watch: float | None = None
        paused = False
        deferred = 0
        # Full original reconciliation, rather than the cheaper resource watch.
        # Keep the original model's preparation allowance; no history is omitted.
        cost = budget.profile.preparation + max(0, RESERVES["ORDINARY"] - budget.WATCH)
        try:
            while True:
                self._require_owned_flat_observation()
                remaining = window.remaining()
                if not budget._fresh(budget.clock()):
                    with weight_scope("WATCH"):
                        self.client.sync_clock()
                try:
                    budget.require_proof(cost)
                    window.remaining()
                    self._require_owned_flat_observation()
                    window.remaining()
                    return
                except ProofDeferred:
                    deferred += 1
                    self.observation_deferrals += 1
                    if deferred >= 120:
                        raise DemoFailure("Observation resource deferral limit reached") from None
                # No DB transaction surrounds the wait. Keep the same lease and
                # query all positions through the unchanged bounded watch lane.
                self.store.renew(self.lease)
                if not paused:
                    self.store.change(
                        self.lease,
                        "DEMO_OBSERVATION_RESOURCE_PAUSE",
                        lambda ledger: setattr(ledger, "paused", True),
                    )
                    paused = True
                assert window.last is not None
                if last_watch is None or window.last - last_watch >= 10:
                    with weight_scope("WATCH"):
                        amount, _ = self._physical_position()
                    if amount:
                        raise DemoFailure("Exposure returned during observation resource wait")
                    last_watch = window.sample()
                remaining = window.remaining()
                self._observation_sleep(min(1.0, remaining))
        except ConfirmationWindowClosed:
            raise DemoFailure("Observation resource start deadline reached") from None

    def connected(self) -> None:
        self._require_observation_session()
        if self._own_operation is not None and self.admission_budget.flat_observed:
            self._wait_for_observation_credit()
        self._require_observation_session()
        super().connected()

    def confirmation_diagnostics(self) -> dict[str, Any]:
        result = super().confirmation_diagnostics()
        result["observation_resource_wait"] = dict(
            deferrals=self.observation_deferrals,
            start_deadline_seconds=120,
            deferral_limit=120,
            full_reconciliation_omitted=False,
            flat_watch_is_financial_proof=False,
            execution_approved=False,
        )
        return result
