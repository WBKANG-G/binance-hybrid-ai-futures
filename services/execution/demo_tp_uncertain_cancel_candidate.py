"""Uninstalled candidate: a failed cancellation can only defer UNKNOWN.

A short-lived proof ticket binds the complete post-QUERY ledger and fence.
Pending probes never DELETE, merge financial state, reconcile or establish flat.
The unchanged original full confirmation is mandatory once uncertainty resolves.
"""

import math
import time
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from decimal import InvalidOperation
from typing import Any
from uuid import uuid5

from integrations.binance_usdm.demo import DemoFailure
from integrations.binance_usdm.demo_snapshot import ALGO_KEYS, pick, signed_number
from integrations.binance_usdm.demo_tp_admission import TPAdmissionBudget
from packages.persistence.demo import DemoStore
from services.execution.demo_flat_transport import ConfirmationClient, confirmation_context
from services.execution.demo_models import DemoLedger
from services.execution.demo_tp_fenced_handoff_candidate import ledger_hash
from services.execution.demo_tp_first_confirmation_candidate import (
    FirstConfirmationTPTriggerCoordinator,
)
from services.execution.demo_tp_staged_confirmation_candidate import StagedConfirmationTPClient
from services.execution.models import Lease, PaperOrder

PENDING_MARKER = "CANCEL_UNCERTAIN_REVIEW"
VERIFIED_MARKER = "FULL_CONFIRMATION_COMPLETED"


class UncertainCancellationClient(StagedConfirmationTPClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.cancel_failures: dict[str, int] = {}

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        identifier = (
            params.get("clientAlgoId")
            if method == "DELETE" and path == "/fapi/v1/algoOrder" and params
            else None
        )
        try:
            return super().request(method, path, params)
        except DemoFailure:
            if identifier:
                self.cancel_failures[identifier] = self.cancel_failures.get(identifier, 0) + 1
            raise  # Preserve SDK type/cooldown and distinguish failure from ACK.


@dataclass(frozen=True)
class UncertainTicket:
    state_hash: str
    identity: tuple[str, str, int]
    created: float
    failed_ids: tuple[str, ...]


class _UncertainCancelDeferred(Exception):
    pass


class UncertainCancellationTPTriggerCoordinator(FirstConfirmationTPTriggerCoordinator):
    def __init__(
        self,
        store: DemoStore,
        lease: Lease,
        client: UncertainCancellationClient,
        budget: TPAdmissionBudget,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(client, UncertainCancellationClient):
            raise ValueError("Explicit uncertain-cancellation client required")
        super().__init__(store, lease, client, budget, monotonic=monotonic)
        self._uncertain_ticket: UncertainTicket | None = None
        self._inside_full_confirmation = False
        self.uncertain_tickets = 0
        self.uncertain_deferrals = 0
        self.uncertain_probes = 0

    def _marker(self) -> str:
        if self._own_operation is None:
            raise ValueError("Owned operation required")
        return str(uuid5(self._own_operation, "candidate-uncertain-protection-cancel-v1"))

    def _confirm_flat_checked(self) -> str:
        self._inside_full_confirmation = True
        try:
            return super()._confirm_flat_checked()
        finally:
            self._inside_full_confirmation = False

    def _cleanup(self, key: str) -> None:
        bounded = self.client
        if (
            not self._inside_full_confirmation
            or not isinstance(bounded, ConfirmationClient)
            or not isinstance(bounded.source, UncertainCancellationClient)
        ):
            super()._cleanup(key)
            return
        source = bounded.source
        before = self.store.read(self.lease.account_id)
        eligible = self._own_operation is not None and self._locally_flat(before)
        previous = source.cancel_failures.copy()
        super()._cleanup(key)  # Full pair and original fenced cleanup come first.
        if not eligible:
            return
        order = before.orders[key]
        failed = tuple(
            identifier
            for identifier in (order.algo_id, order.tp_algo_id)
            if identifier
            and source.cancel_failures.get(identifier, 0) > previous.get(identifier, 0)
        )
        if not failed:
            return

        def register(ledger: DemoLedger) -> UncertainTicket | None:
            normalized = ledger.model_copy(deep=True)
            normalized.reconciled = True
            if ledger_hash(normalized) != ledger_hash(before):
                return None  # Cleanup may only have invalidated reconciliation.
            marker = self._marker()
            if ledger.operations.get(marker) not in (None, PENDING_MARKER):
                return None
            ledger.operations[marker] = PENDING_MARKER
            return UncertainTicket(
                ledger_hash(ledger), self._identity(), self.handoff_clock(), failed
            )

        ticket = self.store.change(
            self.lease, "DEMO_CANDIDATE_UNCERTAIN_CANCEL_REGISTERED", register
        )
        if ticket is None:
            return
        self._uncertain_ticket = ticket
        self.uncertain_tickets += 1
        if self._probe_ticket(ticket, source, observer=bounded):
            raise _UncertainCancelDeferred
        self._uncertain_ticket = None

    def _ticket_valid(self, ticket: UncertainTicket, ledger: DemoLedger) -> bool:
        now = self.handoff_clock()
        age = (
            (self.store.now() - ledger.last_reconciled).total_seconds()
            if ledger.last_reconciled
            else -1
        )
        locally_flat = ledger.model_copy(deep=True)
        locally_flat.reconciled = True
        return bool(
            math.isfinite(now)
            and math.isfinite(ticket.created)
            and 0 <= now - ticket.created <= 8
            and 0 <= age <= min(8, ledger.policy.max_snapshot_age_seconds)
            and ticket.identity == self._identity()
            and ticket.state_hash == ledger_hash(ledger)
            and self._locally_flat(locally_flat)
            and ledger.operations.get(self._marker()) == PENDING_MARKER
        )

    def _probe_ticket(
        self,
        ticket: UncertainTicket,
        source: UncertainCancellationClient,
        *,
        observer: ConfirmationClient | None = None,
    ) -> bool:
        if observer is not None and observer.source is not source:
            raise ValueError("Uncertain probe observer/source mismatch")
        context = confirmation_context(source) if observer is None else nullcontext(observer)
        with context as bounded:

            def probe(ledger: DemoLedger) -> bool:
                if not ledger.paused:
                    ledger.paused = True  # Fenced local stop; never resume on uncertainty.
                    return False
                if not self._ticket_valid(ticket, ledger) or len(ledger.orders) > 129:
                    return False
                self.uncertain_probes += 1
                bounded.sync_clock()
                mode = bounded.request("GET", "/fapi/v1/accountConfig")
                if (
                    not isinstance(mode, dict)
                    or mode.get("dualSidePosition") is not False
                    or mode.get("multiAssetsMargin") is not False
                ):
                    return False
                positions = bounded.request("GET", "/fapi/v3/positionRisk")
                if not isinstance(positions, list) or not positions:
                    return False
                symbols: set[str] = set()
                for row in positions:
                    if (
                        not isinstance(row, dict)
                        or not isinstance(row.get("symbol"), str)
                        or row["symbol"] in symbols
                        or row.get("positionSide") != "BOTH"
                        or signed_number(row.get("positionAmt")) != 0
                    ):
                        return False
                    symbols.add(row["symbol"])
                if "BTCUSDT" not in symbols:
                    return False
                regular = bounded.request("GET", "/fapi/v1/openOrders")
                opened = bounded.request("GET", "/fapi/v1/openAlgoOrders")
                if not isinstance(regular, list) or regular or not isinstance(opened, list):
                    return False
                known: dict[str, tuple[PaperOrder, str]] = {}
                for order in ledger.orders.values():
                    for identifier, kind in (
                        (order.algo_id, "STOP_MARKET"),
                        (order.tp_algo_id, "TAKE_PROFIT_MARKET"),
                    ):
                        if identifier:
                            if identifier in known:
                                return False
                            known[identifier] = order, kind
                if not set(ticket.failed_ids) <= known.keys():
                    return False
                open_ids: set[str] = set()
                open_rows: dict[str, dict[str, str]] = {}
                for raw in opened:
                    row = pick(raw, ALGO_KEYS)
                    identifier = row["clientAlgoId"]
                    if (
                        identifier in open_ids
                        or identifier not in known
                        or row["algoStatus"] != "NEW"
                    ):
                        return False
                    open_ids.add(identifier)
                    open_rows[identifier] = row
                new_ids: set[str] = set()
                for identifier, (order, kind) in known.items():
                    row = pick(
                        bounded.request("GET", "/fapi/v1/algoOrder", {"clientAlgoId": identifier}),
                        ALGO_KEYS,
                    )
                    expected = {
                        "symbol": order.signal.symbol,
                        "clientAlgoId": identifier,
                        "side": "SELL" if order.signal.direction == "LONG" else "BUY",
                        "orderType": kind,
                        "algoType": "CONDITIONAL",
                        "positionSide": "BOTH",
                        "workingType": "MARK_PRICE",
                        "closePosition": "true",
                        "priceProtect": "false",
                    }
                    if any(row[k] != value for k, value in expected.items()):
                        return False
                    trigger = (
                        order.signal.initial_stop_price
                        if kind == "STOP_MARKET"
                        else order.signal.target_price
                    )
                    if signed_number(row["triggerPrice"]) != trigger:
                        return False
                    if row["algoStatus"] == "NEW":
                        if identifier not in open_rows or row != open_rows[identifier]:
                            return False
                        if (
                            identifier not in ticket.failed_ids
                            and identifier not in source.acknowledged_cleanup_ids
                        ):
                            return False
                        new_ids.add(identifier)
                    elif row["algoStatus"] not in {"CANCELED", "EXPIRED", "FINISHED", "REJECTED"}:
                        return False
                return bool(
                    new_ids == open_ids
                    and new_ids.intersection(ticket.failed_ids)
                    and self._ticket_valid(ticket, ledger)
                )

            try:
                return self.store.change(
                    self.lease, "DEMO_CANDIDATE_UNCERTAIN_CANCEL_OBSERVED", probe
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                raise DemoFailure("Invalid uncertain-cancellation probe response") from None
            finally:
                self.flat_confirmation_diagnostic = bounded.diagnostics()

    def _candidate_confirmation_attempt(self) -> str:
        ticket = self._uncertain_ticket
        source = self.client
        if not isinstance(source, UncertainCancellationClient):
            raise ValueError("Uncertain cancellation source required")
        if ticket is not None:
            if self._probe_ticket(ticket, source):
                self.uncertain_deferrals += 1
                self.flat_confirmation_stage = "CANDIDATE_UNCERTAIN_CANCEL_PENDING"
                return "UNKNOWN"
            self._uncertain_ticket = None
        try:
            result = super()._candidate_confirmation_attempt()
        except _UncertainCancelDeferred:
            self.uncertain_deferrals += 1
            self.flat_confirmation_stage = "CANDIDATE_UNCERTAIN_CANCEL_PENDING"
            self.flat_confirmation_diagnostic["candidate_uncertain_cancel"] = 1
            return "UNKNOWN"
        if result == "VERIFIED_FLAT" and self.uncertain_tickets:

            def resolved(ledger: DemoLedger) -> None:
                if not self._locally_flat(ledger):
                    raise DemoFailure("Full confirmation changed before receipt")
                if ledger.operations.get(self._marker()) == PENDING_MARKER:
                    ledger.operations[self._marker()] = VERIFIED_MARKER

            self.store.change(
                self.lease, "DEMO_CANDIDATE_UNCERTAIN_CANCEL_FULLY_VERIFIED", resolved
            )
        return result

    def uncertain_cancel_diagnostics(self) -> dict[str, str | int | bool]:
        return {
            "uncertain_cancel_candidate": "READ_ONLY_WHILE_FRESH_UNCERTAIN",
            "tickets": self.uncertain_tickets,
            "probes": self.uncertain_probes,
            "unknown_deferrals": self.uncertain_deferrals,
            "maximum_proof_age_seconds": 8,
            "probe_can_post_or_delete": False,
            "probe_can_verify_flat": False,
            "actual_execution_approved": False,
        }
