"""Single-use synthetic assembly of existing controls. No activation/provisioning CLI.

The caller owns the already acquired lease and raw transport. This scope owns the
order journal and cloned client; closing revokes its budget before releasing the
journal lock. Neither close nor construction seals, repairs, or refunds a flow.
"""

import json
import os
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from types import TracebackType
from typing import Any
from uuid import UUID

from pydantic import SecretStr

from integrations.binance_usdm.demo import DemoClient, Transport, transport
from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from integrations.binance_usdm.demo_tp_window_budget import ProofDeferred
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import DemoStore
from packages.persistence.demo_order_quota import (
    DurableOrderQuota,
    LocalQuotaStopped,
    quota_require,
)
from packages.persistence.demo_shared_checkpoint import (
    AnchoredSharedWeightJournal,
)
from services.execution.demo_managed_order_quota import (
    STORAGE_KEY,
    ManagedQuotaBridge,
    run_managed_quota_candidate,
    validate_journal_bindings,
)
from services.execution.demo_query_contract import contract_report, require_wire_query
from services.execution.demo_runtime_confirmation import RuntimeConfirmationCoordinator
from services.execution.demo_runtime_guards import (
    RuntimeBoundClient,
    require_start_context,
    require_start_ledger,
)
from services.execution.demo_runtime_storage import (
    RUNTIME_STORAGE_KEY,
    runtime_storage_binding,
)
from services.execution.demo_runtime_storage import (
    RuntimeClocks as RuntimeClocks,
)
from services.execution.demo_runtime_storage import (
    RuntimeStorage as RuntimeStorage,
)
from services.execution.demo_runtime_storage import (
    current_runtime_ledger as _current_ledger,
)
from services.execution.demo_shared_weight_budget import SharedManagedOrderWindowBudget
from services.execution.models import Lease
from services.risk.paper import period_keys


@dataclass
class QuoteObservation:
    saved: tuple[int, int, float | None, int, bool] | None = None
    status: int | None = None


class OwnedSharedBudget(SharedManagedOrderWindowBudget):
    def __init__(self, *args: Any, **kwargs: Any):
        self.lifetime = threading.RLock()
        self.revoked = False
        self._quote_observation: ContextVar[QuoteObservation | None] = ContextVar(
            "quote_observation", default=None
        )
        self.ignored_quote_observations = 0
        self.proof_metadata_deferrals = 0
        self.clock_refresh_failures = 0
        self.hard_stop_early_rejections = 0
        super().__init__(*args, **kwargs)
        self._configured_profile = self.profile
        self._configured_costs = dict(self.costs)
        self._configured_sender = self.sender
        self._configured_raw_sender = self.raw_sender
        self._configured_bridge = self.bridge
        self._configured_shared = self.shared
        self._configured_provider = (
            self.shared.scope,
            self.shared.identity,
            getattr(self.shared, "anchors", None),
        )

    def _check_provider(self) -> None:
        quota_require(
            self.profile == self._configured_profile and self.costs == self._configured_costs,
            "RUNTIME_MODEL_BINDING_CHANGED",
        )
        quota_require(
            self.sender is self._configured_sender
            and self.raw_sender is self._configured_raw_sender
            and self.bridge is self._configured_bridge
            and self.synthetic is True,
            "RUNTIME_TRANSPORT_BINDING_CHANGED",
        )
        quota_require(
            self.shared is self._configured_shared
            and (self.shared.scope, self.shared.identity, getattr(self.shared, "anchors", None))
            == self._configured_provider,
            "RUNTIME_SHARED_PROVIDER_CHANGED",
        )

    def check_confirmation_context(self) -> None:
        with self.lifetime:
            quota_require(not self.revoked, "RUNTIME_CLOSED")
            self._check_provider()
            if self.untrusted:
                self._deny("TRANSPORT_STOPPED")

    def revoke(self) -> None:
        with self.lifetime:
            self.revoked = True

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        with self.lifetime:
            quota_require(not self.revoked, "RUNTIME_CLOSED")
            self._check_provider()
            require_wire_query(method, path, query, key)
            if self.untrusted:
                # The original wire gate refuses every lane in this state.
                # Refuse before committing another shared charge/order claim.
                self.hard_stop_early_rejections += 1
                self._deny("TRANSPORT_STOPPED")
            observation = QuoteObservation() if not self._observes_weight(method, path) else None
            context = self._quote_observation.set(observation)
            try:
                return super().__call__(method, path, query, key)
            finally:
                if observation is not None and observation.saved is not None:
                    # Keep the modeled cost and all faults. Restore only metadata,
                    # captured after transport so nested trustworthy reads survive.
                    (
                        self.header_weight,
                        self.header_charge,
                        self.header_at,
                        self.header_sequence,
                    ) = observation.saved[:4]
                    self.ignored_quote_observations += 1
                self._quote_observation.reset(context)

    def _observes_weight(self, method: str, path: str) -> bool:
        return (method, path) != ("GET", "/fapi/v1/ticker/bookTicker")

    def _send_shared(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        response = super()._send_shared(method, path, query, key)
        observation = self._quote_observation.get()
        if observation is not None:
            observation.saved = (
                self.header_weight,
                self.header_charge,
                self.header_at,
                self.header_sequence,
                self.untrusted,
            )
            observation.status = response.status
        return response

    def _mark(self, reason: str) -> None:
        observation = self._quote_observation.get()
        if (
            reason == "WEIGHT_HEADER_MISSING"
            and observation is not None
            and observation.saved is not None
            and observation.status not in {418, 429}
        ):
            self.untrusted = observation.saved[4]
            return
        super()._mark(reason)

    def invalidate_clock_observation(self) -> None:
        """Latch only this session; transport uncertainty is already journaled."""
        with self.lifetime:
            self.clock_refresh_failures += 1
            self.untrusted = True
            self._mark("RUNTIME_CLOCK_REFRESH_FAILED")

    def require_proof(self, cost: int) -> None:
        with self.lifetime:
            quota_require(not self.revoked, "RUNTIME_CLOSED")
            self._check_provider()
            now = self._time()
            if self.untrusted:
                self._deny("PROOF_METADATA_UNTRUSTED")
            if not self._fresh(now):
                # The coordinator already refreshes through its scoped clock
                # read. Defer without a send, reservation, or fabricated freshness.
                self.deferrals += 1
                self.proof_metadata_deferrals += 1
                raise ProofDeferred
            super().require_proof(cost)

    def diagnostics(self) -> dict[str, Any]:
        result = super().diagnostics()
        result["query_contract"] = contract_report()
        result["hard_stop_policy"] = dict(
            rejections_before_reservation=self.hard_stop_early_rejections,
            existing_records_preserved=True,
            capacity_stop_rescue_policy_unchanged=True,
            execution_approved=False,
        )
        result["proof_metadata_policy"] = dict(
            authoritative_observation_max_age_seconds=5,
            stale_batch_deferrals=self.proof_metadata_deferrals,
            clock_refresh_failures=self.clock_refresh_failures,
            rejected_clock_action="STOP_OWNED_SESSION_NO_RETRY",
            stale_action="DEFER_TO_EXISTING_SCOPED_CLOCK_READ",
            implicit_transport_attempts=0,
            execution_approved=False,
        )
        result["quote_header_policy"] = dict(
            weight_observation_authoritative=False,
            observations_ignored=self.ignored_quote_observations,
            modeled_cost_retained=True,
            freshness_refreshed=False,
            execution_approved=False,
        )
        return result


class ManagedRuntimeSession:
    def __init__(
        self,
        journal: DurableOrderQuota,
        coordinator: RuntimeConfirmationCoordinator,
        budget: OwnedSharedBudget,
        storage: RuntimeStorage,
        binding: str,
    ):
        self.journal, self.coordinator, self.budget = journal, coordinator, budget
        self._storage, self._binding = storage, binding
        self._owned_client = coordinator.client
        self._owned_coordinator, self._owned_budget = coordinator, budget
        self._owned_bridge, self._owned_store = coordinator.quota_bridge, coordinator.store
        self._owned_base_store, self._owned_lease = (
            coordinator.quota_bridge.store,
            coordinator.lease,
        )
        self._owned_journal = journal
        self._owned_credentials = coordinator.client.credentials
        self._disabled_settings = coordinator.client.settings
        self._state_lock = threading.Lock()
        self._started = self._running = self._closing = self._closed = False

    def _check_start(self) -> None:
        require_start_context()
        c, b, bridge = self._owned_coordinator, self._owned_budget, self._owned_bridge
        client = self._owned_client
        quota_require(
            self.coordinator is c
            and self.budget is b
            and self.journal is self._owned_journal
            and c.client is client
            and c.admission_budget is b
            and c.quota_bridge is bridge
            and c.store is self._owned_store
            and c.lease == self._owned_lease
            and bridge.store is self._owned_base_store
            and bridge.lease == self._owned_lease
            and bridge.journal is self.journal
            and b.bridge is bridge
            and client.send is b
            and client.credentials is self._owned_credentials,
            "RUNTIME_START_WIRING_CHANGED",
        )
        quota_require(client.settings == self._disabled_settings, "RUNTIME_START_CLIENT_CHANGED")
        quota_require(
            not b.revoked
            and b.phase == "BOOTSTRAP"
            and not b.stopped
            and not b.admitted
            and not b.entry_sent
            and b.forwarded == 0,
            "RUNTIME_START_BUDGET_CHANGED",
        )
        b._check_provider()
        quota_require(self.journal.owns_process_lock(), "RUNTIME_ORDER_LOCK_REQUIRED")
        current = _current_ledger(bridge.store, bridge.lease)
        require_start_ledger(
            current, self._disabled_settings, b.profile.historical_orders, bridge.store.now()
        )
        quota_require(
            current.operations.get(RUNTIME_STORAGE_KEY) == self._binding
            and runtime_storage_binding(self._storage) == self._binding,
            "RUNTIME_STORAGE_BINDING_MISMATCH",
        )
        validate_journal_bindings(current, self.journal)
        quota_require(
            all(f["seal"] is not None for f in self.journal.flow_records().values()),
            "RUNTIME_PENDING_ORDER_FLOW",
        )
        quota_require(
            isinstance(b.shared, AnchoredSharedWeightJournal), "RUNTIME_START_WIRING_CHANGED"
        )
        assert isinstance(b.shared, AnchoredSharedWeightJournal)
        quota_require(
            b.shared.read_only_snapshot()["pending_attempts"] == 0,
            "RUNTIME_PENDING_SHARED_REQUESTS",
        )

    async def run(self, operation: UUID) -> dict[str, Any]:
        with self._state_lock:
            quota_require(
                not self._started and not self._closing and not self._closed,
                "RUNTIME_SINGLE_USE_REQUIRED",
            )
            self._started = self._running = True
        client = self._owned_client
        try:
            self._check_start()
            await self._owned_bridge.wait_ready()
            self._check_start()
            client.settings = self._disabled_settings.model_copy(
                update={"demo_trading_enabled": True}
            )
            return await run_managed_quota_candidate(self._owned_coordinator, operation)
        finally:
            client.settings = self._disabled_settings
            with self._state_lock:
                self._running = False

    def close(self) -> None:
        with self._state_lock:
            quota_require(not self._running, "RUNTIME_STILL_RUNNING")
            if self._closed:
                return
            quota_require(
                self._owned_coordinator.close_confirmation_admission(),
                "RUNTIME_CONFIRMATION_RUNNING",
            )
            self._closing = True
            self._owned_budget.revoke()
            # Failure leaves run revoked but permits retry of local cleanup only.
            self._owned_journal.close()
            self._closed = True

    def __enter__(self) -> "ManagedRuntimeSession":
        with self._state_lock:
            quota_require(not self._closing and not self._closed, "RUNTIME_CLOSED")
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def assemble_managed_runtime(
    store: DemoStore,
    lease: Lease,
    source: DemoClient,
    sender: Transport,
    storage: RuntimeStorage,
    *,
    allow_synthetic_model_execution: bool = False,
    clocks: RuntimeClocks | None = None,
) -> ManagedRuntimeSession:
    # Default refusal precedes SQL, filesystem access, sessions and HTTP.
    quota_require(allow_synthetic_model_execution is True, "RUNTIME_REAL_EXECUTION_DISABLED")
    quota_require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE"
        and sender is not transport,
        "RUNTIME_SYNTHETIC_CONTEXT_REQUIRED",
    )
    quota_require(
        source.settings.mode == "DEMO"
        and not source.settings.demo_trading_enabled
        and source.settings.margin_mode == "ISOLATED"
        and source.settings.symbols == ("BTCUSDT",)
        and lease.account_id.startswith("DEMO-"),
        "RUNTIME_ISOLATED_DISABLED_CLIENT_REQUIRED",
    )
    anchors = storage.shared_anchor
    quota_require(anchors is not None, "RUNTIME_SHARED_ANCHOR_REQUIRED")
    assert anchors is not None
    quota_require(
        anchors.engine.pool is not store.engine.pool,
        "RUNTIME_DEDICATED_ANCHOR_POOL_REQUIRED",
    )
    clocks = clocks or RuntimeClocks()
    journal = None
    try:
        ledger = _current_ledger(store, lease)
        binding = runtime_storage_binding(storage)
        quota_require(
            ledger.operations.get(RUNTIME_STORAGE_KEY) == binding,
            "RUNTIME_STORAGE_BINDING_MISMATCH",
        )
        quota_require(
            ledger.policy == source.settings.policy
            and ledger.margin_mode == source.settings.margin_mode
            and ledger.symbols == source.settings.symbols,
            "RUNTIME_LEDGER_SCOPE_MISMATCH",
        )
        quota_require(
            ledger.paused
            and ledger.risk_state.value == "NORMAL"
            and not any(ledger.positions.values())
            and all(
                not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
                for o in ledger.orders.values()
            ),
            "RUNTIME_PAUSED_FLAT_REQUIRED",
        )
        quota_require(
            period_keys(store.now(), ledger.policy.week_start_kst)
            == (ledger.daily_key, ledger.weekly_key),
            "RUNTIME_PERIOD_KEYS_MISMATCH",
        )
        profile = TPModelProfile(len(ledger.orders))
        raw = json.loads(ledger.operations.get(STORAGE_KEY, "{}"))
        quota_require(
            isinstance(raw, dict) and isinstance(raw.get("journal"), str),
            "RUNTIME_ORDER_STORAGE_NOT_BOUND",
        )
        identity = UUID(raw["journal"])
        options: dict[str, Any] = dict(monotonic=clocks.monotonic, wall_clock=clocks.wall_clock)
        # Validate both stores before creating a writable session. No bootstrap.
        reader = DurableOrderQuota(
            storage.order_directory, lease.account_id, identity, read_only=True, **options
        )
        try:
            validate_journal_bindings(ledger, reader)
            quota_require(
                all(f["seal"] is not None for f in reader.flow_records().values()),
                "RUNTIME_PENDING_ORDER_FLOW",
            )
        finally:
            reader.close()
        pressure_reader = AnchoredSharedWeightJournal(
            storage.shared_directory,
            storage.shared_scope,
            storage.shared_identity,
            anchors=anchors,
            read_only=True,
            boot=clocks.boot,
            **options,
        )
        pressure = pressure_reader.view()
        quota_require(pressure["startup_ready"], "RUNTIME_SHARED_STARTUP_HOLD")
        quota_require(pressure["pending_attempts"] == 0, "RUNTIME_PENDING_SHARED_REQUESTS")
        journal = DurableOrderQuota(storage.order_directory, lease.account_id, identity, **options)
        validate_journal_bindings(ledger, journal)
        quota_require(
            all(f["seal"] is not None for f in journal.flow_records().values()),
            "RUNTIME_PENDING_ORDER_FLOW",
        )
        shared = AnchoredSharedWeightJournal(
            storage.shared_directory,
            storage.shared_scope,
            storage.shared_identity,
            anchors=anchors,
            boot=clocks.boot,
            **options,
        )
        quota_require(shared.view()["pending_attempts"] == 0, "RUNTIME_PENDING_SHARED_REQUESTS")
        bridge = ManagedQuotaBridge(store, lease, journal)  # Original fence rechecks the lease.
        budget = OwnedSharedBudget(
            sender,
            profile,
            MODELED_TP_ROUTE_COSTS,
            bridge=bridge,
            shared=shared,
            allow_synthetic_model_execution=True,
        )
        client = RuntimeBoundClient(
            source.settings,
            source.credentials,
            send=budget,
            clock_ms=source.clock_ms,
            on_clock_failure=budget.invalidate_clock_observation,
            clock_guard=budget.lifetime,
        )
        client.offset, client.synced_at = source.offset, source.synced_at
        coordinator = RuntimeConfirmationCoordinator(
            store,
            lease,
            client,
            budget,
            check_confirmation_budget=budget.check_confirmation_context,
        )
        return ManagedRuntimeSession(journal, coordinator, budget, storage, binding)
    except Exception as error:
        if journal is not None:
            try:
                journal.close()
            except Exception:
                raise LocalQuotaStopped("RUNTIME_ASSEMBLY_CLEANUP_FAILED") from None
        if isinstance(error, LocalQuotaStopped):
            raise
        raise LocalQuotaStopped("RUNTIME_ASSEMBLY_FAILED") from None
