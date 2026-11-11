"""Uninstalled candidate: bounded, fenced handoff after the original PAUSE proof.

No HTTP response cache: one fresh full-history snapshot must match all account,
order and transfer state. Any mismatch retains the original emergency command.
Admission stays disabled, and only original full confirmation can establish flat.
"""

import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid5

from integrations.binance_usdm.demo import DemoFailure, DemoRateLimited
from integrations.binance_usdm.demo_snapshot import snapshot as full_snapshot
from integrations.binance_usdm.demo_tp_admission import TPAdmissionBudget
from packages.domain.contracts import OrderState, PositionState, RiskState, Signal
from packages.persistence.demo import DemoStore
from services.execution.demo_models import DemoLedger
from services.execution.demo_tp_staged_confirmation_candidate import (
    StagedConfirmationTPClient,
    StagedConfirmationTPTriggerCoordinator,
)
from services.execution.models import Lease, PaperOrder
from services.risk.paper import GateSnapshot


@dataclass(frozen=True)
class PauseHandoff:
    ledger_hash: str
    lease_identity: tuple[str, str, int]
    created: float


def ledger_hash(ledger: DemoLedger) -> str:
    return hashlib.sha256(
        json.dumps(ledger.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FencedHandoffTPTriggerCoordinator(StagedConfirmationTPTriggerCoordinator):
    def __init__(
        self,
        store: DemoStore,
        lease: Lease,
        client: StagedConfirmationTPClient,
        budget: TPAdmissionBudget,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        super().__init__(store, lease, client, budget)
        self.handoff_clock = monotonic
        self._handoff_lock = threading.Lock()
        self._own_operation: UUID | None = None
        self._handoff: PauseHandoff | None = None
        self._finalizing = False
        self._seen_flatten_commands: set[UUID] = set()
        self.handoff_checks = 0
        self.handoff_used = 0
        self.handoff_fallbacks = 0
        self.handoff_reason = "NOT_REQUESTED"

    def reserve(
        self,
        signal: Signal,
        gate: GateSnapshot,
        *,
        quantity_cap: Decimal | None = None,
        entry_style: str = "OPPOSITE_QUOTE",
    ) -> PaperOrder:
        # Keep the original exact-minimum, risk, quote, reservation and one-entry gates.
        if entry_style != "OPPOSITE_QUOTE":
            raise ValueError("Explicit opposite quote candidate required")
        order = super().reserve(
            signal, gate, quantity_cap=quantity_cap, entry_style="OPPOSITE_QUOTE"
        )
        self._own_operation = signal.signal_id
        return order

    def begin_finalization(self) -> None:
        self._handoff = None
        self._finalizing = True
        super().begin_finalization()

    def _identity(self) -> tuple[str, str, int]:
        return self.lease.account_id, str(self.lease.owner), self.lease.fence

    def _eligible(self, ledger: DemoLedger) -> bool:
        if self._own_operation is None:
            return False
        own = ledger.orders.get(str(self._own_operation))
        return bool(
            own
            and self._finalizing
            and ledger.paused
            and ledger.reconciled
            and ledger.risk_state == RiskState.NORMAL
            and ledger.margin_mode == "ISOLATED"
            and ledger.symbols == ("BTCUSDT",)
            and own.signal.purpose == "DEMO_VALIDATION"
            and own.signal.symbol == "BTCUSDT"
            and own.signal.direction == "LONG"
            and own.state == OrderState.FILLED
            and own.filled == own.quantity == own.open_quantity
            and Decimal(0) < own.quantity <= Decimal("0.0007")
            and not own.remaining
            and own.protected
            and own.position_state == PositionState.PROTECTED
            and own.exit_id is None
            and own.exit_state == "NONE"
            and own.algo_id
            and own.tp_algo_id
            and ledger.requests.get(own.algo_id) == "VERIFIED"
            and ledger.requests.get(own.tp_algo_id) == "VERIFIED"
            and ledger.positions.get("BTCUSDT") == own.open_quantity
            and all(
                not amount for symbol, amount in ledger.positions.items() if symbol != "BTCUSDT"
            )
            and all(
                not (order.remaining or order.open_quantity or order.actual_risk or order.protected)
                and order.position_state == PositionState.CLOSED
                for key, order in ledger.orders.items()
                if key != str(self._own_operation)
            )
        )

    def _handoff_fresh(self, token: PauseHandoff, ledger: DemoLedger) -> bool:
        now = self.handoff_clock()
        age = (
            (self.store.now() - ledger.last_reconciled).total_seconds()
            if ledger.last_reconciled
            else -1
        )
        return bool(
            math.isfinite(now)
            and math.isfinite(token.created)
            and 0 <= now - token.created <= 2
            and 0 <= age <= min(2, ledger.policy.max_snapshot_age_seconds)
            and token.lease_identity == self._identity()
            and token.ledger_hash == ledger_hash(ledger)
            and self._eligible(ledger)
        )

    def _repeat(self, operation: UUID) -> str:
        def reject(ledger: DemoLedger) -> None:
            previous = ledger.operations.get(str(operation))
            if previous is not None and previous != "EMERGENCY_FLATTEN":
                raise ValueError("Conflicting operation ID")
            ledger.paused = True

        self.store.change(self.lease, "DEMO_CANDIDATE_DUPLICATE_FINISH_BLOCKED", reject)
        return "UNKNOWN"

    def command(self, operation_id: UUID, command: str) -> str:
        with self._handoff_lock:
            return self._command_once(operation_id, command)

    def _command_once(self, operation_id: UUID, command: str) -> str:
        expected_pause = uuid5(self._own_operation, "finish-pause") if self._own_operation else None
        expected_finish = uuid5(self._own_operation, "finish") if self._own_operation else None
        if (
            not self._finalizing
            or command != "EMERGENCY_FLATTEN"
            or operation_id != expected_finish
        ):
            self._handoff = None
            result = super().command(operation_id, command)
            if self._finalizing and command == "PAUSE_ENTRIES" and operation_id == expected_pause:
                ledger = self.store.read(self.lease.account_id)
                if self._eligible(ledger):
                    self._handoff = PauseHandoff(
                        ledger_hash(ledger), self._identity(), self.handoff_clock()
                    )
            return result
        if operation_id in self._seen_flatten_commands:
            return self._repeat(operation_id)
        self._seen_flatten_commands.add(operation_id)  # Possible-send is never retried.
        token, self._handoff = self._handoff, None
        self.handoff_checks += 1

        def check(ledger: DemoLedger) -> str:
            prior = ledger.operations.get(str(operation_id))
            if prior is not None:
                if prior != "EMERGENCY_FLATTEN":
                    raise ValueError("Conflicting operation ID")
                return "REPEAT"
            if token is None or not self._handoff_fresh(token, ledger):
                return "LOCAL_HANDOFF_UNAVAILABLE"
            self.client.sync_clock()
            remote = full_snapshot(self.client, ledger, self.store.now())
            if (
                not self._handoff_fresh(token, ledger)
                or remote.fingerprint != ledger.fingerprint
                or remote.transfers != ledger.income_seen
            ):
                return "FRESH_SNAPSHOT_CHANGED"
            ledger.operations[str(operation_id)] = "EMERGENCY_FLATTEN"
            ledger.risk_state = RiskState.EMERGENCY
            ledger.paused = True
            return "READY"

        try:
            decision = self.store.change(self.lease, "DEMO_CANDIDATE_FRESH_FINISH_HANDOFF", check)
        except DemoRateLimited:
            self.handoff_reason = "SDK_RATE_LIMITED_NO_FALLBACK"
            raise
        except DemoFailure:
            # No exit has been attempted. Preserve the original reconciliation's
            # metadata-failure/minimal-position emergency handling once.
            decision = "QUERY_FAILED_ORIGINAL_EMERGENCY_POLICY"
        self.handoff_reason = decision
        if decision == "REPEAT":
            return "UNKNOWN"
        if decision != "READY":
            self.handoff_fallbacks += 1
            return super().command(operation_id, command)
        self.handoff_used += 1
        # Unchanged original intent staging, fenced effects and fresh position
        # query; no fallback or retry after an exit can possibly have been sent.
        self._flatten()
        return "UNKNOWN"  # Only the original complete confirm_flat may verify.

    def handoff_diagnostics(self) -> dict[str, str | int | bool]:
        return {
            "candidate_finish_handoff": "FRESH_FULL_SNAPSHOT_ONLY",
            "checks": self.handoff_checks,
            "used": self.handoff_used,
            "fallbacks": self.handoff_fallbacks,
            "reason": self.handoff_reason,
            "maximum_age_seconds": 2,
            "cached_http_responses": 0,
            "actual_execution_approved": False,
        }
