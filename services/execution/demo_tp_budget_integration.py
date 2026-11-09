"""Integrated synthetic TP budget and physical risk reduction.

No CLI enables real execution. Physical position observations only schedule
resources; the original full historical proof alone establishes VERIFIED_FLAT.
"""

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal
from uuid import UUID

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import signed_number
from integrations.binance_usdm.demo_tp_window_budget import (
    ProofDeferred,
    WindowTPBudget,
    weight_scope,
)
from packages.domain.contracts import PositionState, RiskState, Signal
from packages.persistence.demo import DemoStore
from services.execution.demo_models import DemoLedger, DemoSnapshot
from services.execution.demo_tp_uncertain_cancel_candidate import (
    UncertainCancellationClient,
    UncertainCancellationTPTriggerCoordinator,
)
from services.execution.models import Lease, PaperOrder
from services.risk.paper import GateSnapshot


class BudgetedTPTriggerCoordinator(UncertainCancellationTPTriggerCoordinator):
    admission_budget: WindowTPBudget

    def __init__(
        self,
        store: DemoStore,
        lease: Lease,
        client: UncertainCancellationClient,
        budget: WindowTPBudget,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(budget, WindowTPBudget):
            raise ValueError("Integrated window budget required")
        super().__init__(store, lease, client, budget, monotonic=monotonic)
        self.budgeted_flat_confirmation = "UNKNOWN"
        self.proof_resource_wait_seconds = 0.0
        self.confirmation_attempts_completed = 0

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: str = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        order = super().reserve(signal, gate, quantity_cap=quantity_cap, entry_style=entry_style)
        budget = self.admission_budget
        budget.owned_entry_id = order.client_id
        budget.owned_quantity = order.quantity
        budget.owned_side = "BUY" if order.signal.direction == "LONG" else "SELL"
        return order

    def _merge(self, ledger: DemoLedger, remote: DemoSnapshot) -> None:
        super()._merge(ledger, remote)
        self.admission_budget.flat_observed = bool(
            self._own_operation is not None
            and not any(remote.positions.values())
            and not any(o.remaining or o.open_quantity for o in ledger.orders.values())
        )

    def _physical_position(self) -> tuple[Decimal, Decimal]:
        self.admission_budget.flat_observed = False
        self.client.sync_clock()
        rows = self.client.request("GET", "/fapi/v3/positionRisk")
        if not isinstance(rows, list) or not rows:
            raise DemoFailure("Invalid rescue position response")
        seen = set()
        btc = None
        for row in rows:
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or symbol in seen or row.get("positionSide") != "BOTH":
                raise DemoFailure("Ambiguous rescue position")
            seen.add(symbol)
            amount = signed_number(row.get("positionAmt"))
            if symbol == "BTCUSDT":
                btc = (amount, signed_number(row.get("entryPrice")))
            elif amount:
                raise DemoFailure("Unowned position requires operator review")
        if btc is None:
            raise DemoFailure("Missing rescue position")
        if self._own_operation is not None:
            order = self.store.read(self.lease.account_id).orders[str(self._own_operation)]
            if order.remaining and btc[0] == 0:
                row = self.client.request(
                    "GET",
                    "/fapi/v1/order",
                    {
                        "symbol": order.signal.symbol,
                        "origClientOrderId": order.client_id,
                    },
                )
                if (
                    not isinstance(row, dict)
                    or row.get("clientOrderId") != order.client_id
                    or row.get("symbol") != order.signal.symbol
                    or row.get("status") not in {"FILLED", "CANCELED", "EXPIRED", "REJECTED"}
                    or not 0 <= signed_number(row.get("executedQty")) <= order.quantity
                ):
                    raise DemoFailure("Entry cancellation not terminal; do not wait for proof")
        self.admission_budget.flat_observed = btc[0] == 0
        return btc

    def command(self, operation_id: UUID, command: str) -> str:
        if self._own_operation is None:
            return super().command(operation_id, command)
        if command not in {"PAUSE_ENTRIES", "SAFE_STOP", "EMERGENCY_FLATTEN"}:
            raise ValueError("Invalid DEMO command")
        with self._handoff_lock, weight_scope("RESCUE"):

            def pause(ledger: DemoLedger) -> None:
                key = str(operation_id)
                if key in ledger.operations and ledger.operations[key] != command:
                    raise ValueError("Conflicting operation ID")
                ledger.operations[key] = command
                ledger.paused = True

            self.store.change(self.lease, "DEMO_BUDGETED_PAUSE_INTENT", pause)
            order = self.store.read(self.lease.account_id).orders[str(self._own_operation)]
            budget = self.admission_budget
            if order.remaining and order.client_id not in budget.cancel_ids:

                def cancel(ledger: DemoLedger) -> None:
                    current = ledger.orders[str(self._own_operation)]
                    ledger.requests[current.client_id] = "CANCEL_REQUESTED"
                    try:
                        self.client.request(
                            "DELETE",
                            "/fapi/v1/order",
                            {
                                "symbol": current.signal.symbol,
                                "origClientOrderId": current.client_id,
                            },
                        )
                    except DemoFailure:
                        ledger.requests[current.client_id] = "UNKNOWN"

                self.store.change(self.lease, "DEMO_BUDGETED_ENTRY_CANCEL", cancel)
            amount, entry_price = self._physical_position()
            if amount and (
                not budget.entry_sent
                or order.position_state == PositionState.CLOSED
                or abs(amount) > order.quantity
                or amount * (1 if order.signal.direction == "LONG" else -1) <= 0
            ):
                raise DemoFailure("Rescue position outside owned entry")
            if amount:

                def attribute(ledger: DemoLedger) -> None:
                    current = ledger.orders[str(self._own_operation)]
                    current.open_quantity = abs(amount)
                    current.average_price = entry_price
                    current.actual_risk = abs(amount) * entry_price
                    if not current.protected or command == "EMERGENCY_FLATTEN":
                        ledger.risk_state = RiskState.EMERGENCY
                    if not current.protected:
                        current.position_state = PositionState.OPEN_UNPROTECTED
                    ledger.reconciled = False

                self.store.change(self.lease, "DEMO_BUDGETED_RESCUE_POSITION", attribute)
                if command == "EMERGENCY_FLATTEN":
                    self._flatten()  # Original fenced exit intent/effect; no POST retry.
                    self._physical_position()
            return "UNKNOWN" if command == "EMERGENCY_FLATTEN" else "ENTRIES_BLOCKED"

    def _flatten(self) -> None:
        # Reconciliation failure can also call the original rescue before the
        # session finalizer. It needs the same bounded reserve and one-shot IDs.
        with weight_scope("RESCUE"):
            super()._flatten()

    @property
    def proof_batch(self) -> int:
        profile = self.admission_budget.profile
        # Original five historical snapshots plus cleanup snapshots for every
        # known order, possible two protection DELETEs per order and clock reads.
        return (
            (6 + profile.historical_orders) * profile.snapshot
            + 80 * (profile.historical_orders + 1)
            + 38
        )

    def _confirm_flat_checked(self) -> str:
        self.admission_budget.require_proof(self.proof_batch)
        return super()._confirm_flat_checked()

    async def confirm_exit_budgeted(self) -> str:
        budget = self.admission_budget
        completed = 0
        deadline = time.monotonic() + 120
        last_watch = 0.0
        full_deferred = False
        while completed < 4:
            await asyncio.to_thread(self.store.renew, self.lease)
            now = time.monotonic()
            if not budget._fresh(budget.clock()):
                with weight_scope("WATCH"):
                    await asyncio.to_thread(self.client.sync_clock)
            if now - last_watch >= 10 or not budget.flat_observed:
                with weight_scope("WATCH"):
                    amount, _ = await asyncio.to_thread(self._physical_position)
                last_watch = time.monotonic()
                if amount:
                    # No waiting for financial proof while exposure remains.
                    return "UNKNOWN"
            try:
                with weight_scope("PROOF"):
                    if full_deferred or (
                        self._uncertain_ticket is None and not self._pending_after_full
                    ):
                        budget.require_proof(self.proof_batch)
                    result = await asyncio.to_thread(self.confirm_flat)
            except ProofDeferred:
                full_deferred = True
                if time.monotonic() >= deadline:
                    return "UNKNOWN"
                started = time.monotonic()
                await asyncio.sleep(1)
                self.proof_resource_wait_seconds += time.monotonic() - started
                continue
            completed += 1
            self.confirmation_attempts_completed = completed
            full_deferred = False
            if result == "VERIFIED_FLAT":
                self.budgeted_flat_confirmation = result
                return result
            if completed < 4:
                await asyncio.sleep(1)
        return "UNKNOWN"
