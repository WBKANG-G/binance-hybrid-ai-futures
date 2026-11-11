"""Candidate only. No CLI activates this coordinator or modeled execution."""

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

from integrations.binance_usdm.demo_tp_admission import TPAdmissionBudget
from packages.domain.contracts import Signal
from packages.persistence.demo import DemoStore
from services.execution.demo_api_restart_check import empty
from services.execution.demo_tp_trigger_check import (
    PooledReadTPTriggerCoordinator,
    TPTriggerClient,
    run,
)
from services.execution.models import Lease, PaperOrder
from services.risk.paper import GateSnapshot


class AdmissionTPTriggerCoordinator(PooledReadTPTriggerCoordinator):
    def __init__(
        self, store: DemoStore, lease: Lease, client: TPTriggerClient, budget: TPAdmissionBudget
    ):
        if client.send is not budget:
            raise ValueError("Shared candidate transport required")
        super().__init__(store, lease, client)
        self.admission_budget = budget

    def before_entry_prepare(self) -> None:
        self.admission_budget.before_preparation()

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        self.admission_budget.admit_entry()
        try:
            return super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)
        except BaseException:
            self.admission_budget.finish_entry(raise_on_stop=False)
            raise

    def submit(self, key: str) -> None:
        try:
            super().submit(key)
        except BaseException:
            self.admission_budget.finish_entry(raise_on_stop=False)
            raise
        self.admission_budget.finish_entry()

    def begin_finalization(self) -> None:
        self.admission_budget.begin_cleanup()
        if self.admission_budget.stopped:
            self.session_failed = True

    def disconnect(self) -> None:
        super().disconnect()
        if self.admission_budget.stopped:
            self.session_failed = True


async def run_admission_candidate(
    coordinator: AdmissionTPTriggerCoordinator, operation: UUID
) -> dict[str, Any]:
    if not isinstance(coordinator, AdmissionTPTriggerCoordinator):
        raise ValueError("Explicit candidate coordinator required")
    client = coordinator.client
    state = coordinator.store.read(coordinator.lease.account_id)
    if (
        not client.settings.demo_trading_enabled
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or state.risk_state.value != "NORMAL"
        or not state.paused
        or coordinator.admission_budget.profile.historical_orders != len(state.orders)
    ):
        raise ValueError("Explicit isolated candidate/profile context required")
    if (
        str(operation) in state.orders
        or str(uuid5(operation, "tp-trigger-observation")) in state.operations
    ):
        raise ValueError("Existing candidate operation must not be submitted again")
    client.sync_clock()
    info = client.public("/fapi/v1/exchangeInfo")
    coordinator.admission_budget.configure(info)
    coordinator.admission_budget.preflight()  # Before the TP marker, session or reservation.
    settings = client.settings
    client.settings = settings.model_copy(update={"demo_trading_enabled": False})
    try:
        empty(coordinator)
        coordinator.refresh_metadata()
        coordinator.reconcile()
    finally:
        client.settings = settings
    return await run(coordinator, operation, entry_style="OPPOSITE_QUOTE")
