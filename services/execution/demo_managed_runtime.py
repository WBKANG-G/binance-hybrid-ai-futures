"""Single-use synthetic assembly of existing controls. No activation/provisioning CLI.

The caller owns the already acquired lease and raw transport. This scope owns the
order journal and cloned client; closing revokes its budget before releasing the
journal lock. Neither close nor construction seals, repairs, or refunds a flow.
"""

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select, text

from integrations.binance_usdm.demo import DemoClient, Transport, transport
from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from integrations.binance_usdm.rest import RESTResponse
from packages.persistence.demo import DemoStore, accounts
from packages.persistence.demo_order_quota import (
    DurableOrderQuota,
    LocalQuotaStopped,
    quota_require,
)
from packages.persistence.demo_shared_weight import SharedWeightJournal, boot_id
from services.execution.demo_managed_order_quota import (
    STORAGE_KEY,
    ManagedQuotaBridge,
    ManagedQuotaTPTriggerCoordinator,
    run_managed_quota_candidate,
    validate_journal_bindings,
)
from services.execution.demo_models import DemoLedger
from services.execution.demo_shared_weight_budget import SharedManagedOrderWindowBudget
from services.execution.demo_tp_uncertain_cancel_candidate import UncertainCancellationClient
from services.execution.models import Lease
from services.risk.paper import period_keys


@dataclass(frozen=True)
class RuntimeStorage:
    order_directory: Path
    shared_directory: Path
    shared_scope: str
    shared_identity: UUID


@dataclass(frozen=True)
class RuntimeClocks:
    monotonic: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    boot: Callable[[], str] = boot_id


class OwnedSharedBudget(SharedManagedOrderWindowBudget):
    def __init__(self, *args: Any, **kwargs: Any):
        self.lifetime = threading.RLock()
        self.revoked = False
        super().__init__(*args, **kwargs)

    def revoke(self) -> None:
        with self.lifetime:
            self.revoked = True

    def __call__(
        self, method: str, path: str, query: SecretStr, key: SecretStr | None
    ) -> RESTResponse:
        with self.lifetime:
            quota_require(not self.revoked, "RUNTIME_CLOSED")
            return super().__call__(method, path, query, key)

    def require_proof(self, cost: int) -> None:
        with self.lifetime:
            quota_require(not self.revoked, "RUNTIME_CLOSED")
            super().require_proof(cost)


class ManagedRuntimeSession:
    def __init__(
        self,
        journal: DurableOrderQuota,
        coordinator: ManagedQuotaTPTriggerCoordinator,
        budget: OwnedSharedBudget,
    ):
        self.journal, self.coordinator, self.budget = journal, coordinator, budget
        self._state_lock = threading.Lock()
        self._started = self._running = self._closing = self._closed = False

    async def run(self, operation: UUID) -> dict[str, Any]:
        with self._state_lock:
            quota_require(
                not self._started and not self._closing and not self._closed,
                "RUNTIME_SINGLE_USE_REQUIRED",
            )
            self._started = self._running = True
        client = self.coordinator.client
        disabled = client.settings.model_copy(update={"demo_trading_enabled": False})
        try:
            await self.coordinator.quota_bridge.wait_ready()
            bridge = self.coordinator.quota_bridge
            quota_require(self.journal.owns_process_lock(), "RUNTIME_ORDER_LOCK_REQUIRED")
            current = _current_ledger(bridge.store, bridge.lease)
            validate_journal_bindings(current, self.journal)
            quota_require(
                period_keys(bridge.store.now(), current.policy.week_start_kst)
                == (current.daily_key, current.weekly_key),
                "RUNTIME_PERIOD_KEYS_MISMATCH",
            )
            quota_require(
                self.budget.shared.view()["pending_attempts"] == 0,
                "RUNTIME_PENDING_SHARED_REQUESTS",
            )
            # Only the explicit synthetic constructor can create this object.
            client.settings = disabled.model_copy(update={"demo_trading_enabled": True})
            return await run_managed_quota_candidate(self.coordinator, operation)
        finally:
            client.settings = disabled
            with self._state_lock:
                self._running = False

    def close(self) -> None:
        with self._state_lock:
            quota_require(not self._running, "RUNTIME_STILL_RUNNING")
            if self._closed:
                return
            self._closing = True
            self.budget.revoke()
            # Failure leaves run revoked but permits retry of local cleanup only.
            self.journal.close()
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


def _current_ledger(store: DemoStore, lease: Lease) -> DemoLedger:
    with store.engine.connect() as connection, connection.begin():
        connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        connection.execute(text("SET LOCAL statement_timeout = '2s'"))
        row = (
            connection.execute(select(accounts).where(accounts.c.account_id == lease.account_id))
            .mappings()
            .one()
        )
        quota_require(
            row["owner"] == str(lease.owner)
            and row["fence"] == lease.fence
            and row["lease_until"] is not None
            and row["lease_until"] > store.now(),
            "RUNTIME_CURRENT_LEASE_REQUIRED",
        )
        return DemoLedger.model_validate(row["state"])


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
    clocks = clocks or RuntimeClocks()
    journal = None
    try:
        ledger = _current_ledger(store, lease)
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
        pressure_reader = SharedWeightJournal(
            storage.shared_directory,
            storage.shared_scope,
            storage.shared_identity,
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
        shared = SharedWeightJournal(
            storage.shared_directory,
            storage.shared_scope,
            storage.shared_identity,
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
        client = UncertainCancellationClient(
            source.settings, source.credentials, send=budget, clock_ms=source.clock_ms
        )
        client.offset, client.synced_at = source.offset, source.synced_at
        coordinator = ManagedQuotaTPTriggerCoordinator(store, lease, client, budget)
        return ManagedRuntimeSession(journal, coordinator, budget)
    except Exception as error:
        if journal is not None:
            try:
                journal.close()
            except Exception:
                raise LocalQuotaStopped("RUNTIME_ASSEMBLY_CLEANUP_FAILED") from None
        if isinstance(error, LocalQuotaStopped):
            raise
        raise LocalQuotaStopped("RUNTIME_ASSEMBLY_FAILED") from None
