"""Cloud-tested candidate only; no CLI/default or production activation."""

from decimal import Decimal
from typing import Literal

from integrations.binance_usdm.demo_tp_budget import TPRequestBudget
from packages.domain.contracts import OrderState, PositionState, RiskState, Signal
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


class CoalescedBudgetedTPTriggerCoordinator(BudgetedTPTriggerCoordinator):
    """Opt-in cloud candidate: only fully protected ACCOUNT_UPDATE hints may batch."""

    def prepare_event_batch(self) -> float:
        ledger = self.store.read(self.lease.account_id)
        delay = 0.25
        active = [o for o in ledger.orders.values() if o.open_quantity]
        if (
            self.session_failed
            or self.request_budget.stopped
            or not self.client.settings.demo_trading_enabled
            or self.client.settings.margin_mode != "ISOLATED"
            or self.client.settings.symbols != ("BTCUSDT",)
            or ledger.risk_state != RiskState.NORMAL
            or not ledger.reconciled
            or not ledger.stream_ready
            or any(o.remaining for o in ledger.orders.values())
            or len(active) != 1
            or ledger.last_reconciled is None
            or ledger.stream_at is None
            or any(
                not 0
                <= (self.store.now() - stamp).total_seconds()
                <= ledger.policy.max_snapshot_age_seconds / 2 - delay
                for stamp in (ledger.last_reconciled, ledger.stream_at)
            )
        ):
            return 0.0
        order = active[0]
        evidence = ledger.validation_evidence.get(str(order.signal.signal_id), {})
        if (
            order.state != OrderState.FILLED
            or order.signal.purpose != "DEMO_VALIDATION"
            or order.signal.symbol != "BTCUSDT"
            or order.signal.direction != "LONG"
            or not 0 < order.quantity <= Decimal("0.0007")
            or order.quantity * order.signal.suggested_limit_price > 100
            or order.open_quantity != order.filled
            or order.filled != order.quantity
            or order.position_state != PositionState.PROTECTED
            or not order.protected
            or not order.algo_id
            or not order.tp_algo_id
            or order.exit_id is not None
            or order.exit_state != "NONE"
            or not evidence.get("stop_verified")
            or not evidence.get("target_verified")
            or ledger.positions.get("BTCUSDT") != order.open_quantity
            or any(q for symbol, q in ledger.positions.items() if symbol != "BTCUSDT")
        ):
            return 0.0
        # Invalidate admission BEFORE any delay. The next event() still performs
        # the original clock validation, full snapshot pair, protection recheck.
        self.disconnect()
        return delay


class PrecheckedBudgetedTPTriggerCoordinator(CoalescedBudgetedTPTriggerCoordinator):
    """Candidate only: early refusal before resuming entries or planning prices."""

    def __init__(
        self,
        store: DemoStore,
        lease: Lease,
        client: TPTriggerClient,
        budget: TPRequestBudget,
        *,
        preparation_cost_assumption: int,
    ):
        if type(preparation_cost_assumption) is not int or preparation_cost_assumption <= 0:
            raise ValueError("Explicit positive preparation cost assumption required")
        super().__init__(store, lease, client, budget)
        self.preparation_cost_assumption = preparation_cost_assumption

    def before_entry_prepare(self) -> None:
        self.request_budget.precheck_preparation(self.preparation_cost_assumption)


class IncomeBudgetedTPTriggerCoordinator(PrecheckedBudgetedTPTriggerCoordinator):
    """Opt-in candidate: account for pagination before preparation and reservation."""

    def before_entry_prepare(self) -> None:
        preparation = self.request_budget.precheck_income_allowances(
            self.preparation_cost_assumption
        )
        self.request_budget.precheck_preparation(preparation)

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: Literal["PASSIVE", "OPPOSITE_QUOTE"] = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        # Recheck the profile without charging the preparation forecast again.
        self.request_budget.precheck_income_allowances(self.preparation_cost_assumption)
        return super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)
