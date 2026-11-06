"""Cloud-tested candidate only; no CLI/default or production activation."""

from decimal import Decimal
from typing import Literal

from integrations.binance_usdm.demo_tp_budget import TPRequestBudget
from packages.domain.contracts import RiskState, Signal
from packages.persistence.demo import DemoStore
from services.execution.demo_models import DemoLedger
from services.execution.demo_tp_trigger_check import PooledReadTPTriggerCoordinator, TPTriggerClient
from services.execution.models import Lease, PaperOrder
from services.risk.paper import GateSnapshot


class BudgetedTPTriggerCoordinator(PooledReadTPTriggerCoordinator):
    def __init__(
        self, store: DemoStore, lease: Lease, client: TPTriggerClient, budget: TPRequestBudget
    ):
        if client.send is not budget:
            raise ValueError("Candidate coordinator requires its shared budget transport")
        super().__init__(store, lease, client)
        self.request_budget = budget

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        # Fail before creating an entry reservation/intent if its lifecycle cannot fit.
        # The parent still owns scope, quote, expiry, size and risk/fencing checks.
        self.request_budget.admit_entry()
        return super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)

    def disconnect(self) -> None:
        super().disconnect()
        # The private supervisor intentionally suppresses close-time errors. A
        # local budget stop there must still invalidate observation acceptance.
        if self.request_budget.stopped:
            self.session_failed = True

    def begin_finalization(self) -> None:
        self.request_budget.begin_cleanup()
        if not self.request_budget.stopped:
            return
        self.session_failed = True

        def pause(ledger: DemoLedger) -> None:
            ledger.paused = True
            ledger.stream_ready = ledger.reconciled = False
            if (
                any(ledger.positions.values())
                or any(o.open_quantity for o in ledger.orders.values())
            ) and ledger.risk_state not in {
                RiskState.EMERGENCY,
                RiskState.DAILY_LOCK,
                RiskState.WEEKLY_LOCK,
                RiskState.DRAWDOWN_LOCK,
            }:
                ledger.risk_state = RiskState.EMERGENCY

        self.store.change(self.lease, "DEMO_TP_BUDGET_FINALIZATION_PAUSE", pause)
