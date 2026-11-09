"""Uninstalled opt-in candidate: cheap pending polls never establish flat.

The original complete confirmation is mandatory before VERIFIED_FLAT. No CLI
selects this coordinator; admission remains disabled outside synthetic tests.
"""

from collections.abc import Mapping
from contextlib import nullcontext
from typing import Any

from integrations.binance_usdm.demo_snapshot import signed_number
from integrations.binance_usdm.demo_tp_admission import TPAdmissionBudget
from packages.persistence.demo import DemoStore
from services.execution.demo_flat_transport import ConfirmationClient, confirmation_context
from services.execution.demo_models import DemoLedger
from services.execution.demo_tp_admission_candidate import AdmissionTPTriggerCoordinator
from services.execution.demo_tp_trigger_check import TPTriggerClient
from services.execution.models import Lease


class StagedConfirmationTPClient(TPTriggerClient):
    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.acknowledged_cleanup_ids: set[str] = set()

    def request(self, method: str, path: str, params: Mapping[str, str] | None = None) -> Any:
        identifier = (
            params.get("clientAlgoId")
            if method == "DELETE" and path == "/fapi/v1/algoOrder" and params
            else None
        )
        if identifier:
            self.acknowledged_cleanup_ids.discard(identifier)
        result = super().request(method, path, params)
        if identifier:
            # Record only a parsed successful SDK return. Never infer terminal
            # exchange state from this ACK, and never retain an earlier ACK after
            # a later failed DELETE of the same ID.
            self.acknowledged_cleanup_ids.add(identifier)
        return result


class StagedConfirmationTPTriggerCoordinator(AdmissionTPTriggerCoordinator):
    def __init__(
        self, store: DemoStore, lease: Lease, client: TPTriggerClient, budget: TPAdmissionBudget
    ):
        if not isinstance(client, StagedConfirmationTPClient):
            raise ValueError("Explicit staged candidate ACK observer required")
        super().__init__(store, lease, client, budget)
        self._pending_after_full = False
        self.pending_confirmation_deferrals = 0

    @staticmethod
    def _locally_flat(ledger: DemoLedger) -> bool:
        return bool(
            ledger.paused
            and ledger.reconciled
            and not any(ledger.positions.values())
            and all(
                order.position_state.value == "CLOSED"
                and not order.remaining
                and not order.open_quantity
                for order in ledger.orders.values()
            )
        )

    def _pending_protection(
        self,
        source: StagedConfirmationTPClient | None = None,
        *,
        observer: ConfirmationClient | None = None,
    ) -> bool:
        source_client = self.client if source is None else source
        if not isinstance(source_client, StagedConfirmationTPClient):
            raise ValueError("Staged candidate source client required")
        if observer is not None and observer.source is not source_client:
            raise ValueError("Pending probe observer/source mismatch")
        context = confirmation_context(source_client) if observer is None else nullcontext(observer)
        with context as bounded:
            # A bounded, fenced read may defer an expensive retry. It never sends
            # DELETE, writes financial state, merges snapshots or establishes flat.
            def probe(ledger: DemoLedger) -> bool:
                if not self._locally_flat(ledger) or len(ledger.orders) > 129:
                    return False
                bounded.sync_clock()
                positions = bounded.request("GET", "/fapi/v3/positionRisk")
                if (
                    not isinstance(positions, list)
                    or not positions
                    or any(
                        not isinstance(row, dict)
                        or row.get("positionSide") != "BOTH"
                        or signed_number(row.get("positionAmt")) != 0
                        for row in positions
                    )
                ):
                    return False  # Delegate any exposure/malformed data to full proof.
                pending = False
                for order in ledger.orders.values():
                    for identifier, kind in (
                        (order.algo_id, "STOP_MARKET"),
                        (order.tp_algo_id, "TAKE_PROFIT_MARKET"),
                    ):
                        if not identifier:
                            continue
                        row = bounded.request(
                            "GET", "/fapi/v1/algoOrder", {"clientAlgoId": identifier}
                        )
                        if (
                            not isinstance(row, dict)
                            or row.get("clientAlgoId") != identifier
                            or row.get("symbol") != order.signal.symbol
                            or row.get("orderType") != kind
                            or row.get("algoStatus")
                            not in {"NEW", "CANCELED", "EXPIRED", "FINISHED", "REJECTED"}
                        ):
                            return False
                        if (
                            row["algoStatus"] == "NEW"
                            and identifier not in source_client.acknowledged_cleanup_ids
                        ):
                            return False  # A failed/unattempted DELETE must retain full cleanup retries.
                        pending = pending or row["algoStatus"] == "NEW"
                return pending

            try:
                return self.store.change(
                    self.lease, "DEMO_CANDIDATE_PENDING_PROTECTION_PROBE", probe
                )
            finally:
                self.flat_confirmation_diagnostic = bounded.diagnostics()

    def confirm_flat(self) -> str:
        if self._pending_after_full:
            self.flat_confirmation_stage = "CANDIDATE_PENDING_PROTECTION_PROBE"
            if self._pending_protection():
                self.pending_confirmation_deferrals += 1
                return "UNKNOWN"
        result = super().confirm_flat()
        # A full original attempt must first reach VERIFY, perform cleanup and
        # remain locally flat. Other UNKNOWN causes always retain full retries.
        self._pending_after_full = bool(
            result == "UNKNOWN"
            and self.flat_confirmation_stage == "VERIFY"
            and self.flat_confirmation_diagnostic.get("confirmation_cleanup_attempts", 0)
            and self._locally_flat(self.store.read(self.lease.account_id))
        )
        return result
