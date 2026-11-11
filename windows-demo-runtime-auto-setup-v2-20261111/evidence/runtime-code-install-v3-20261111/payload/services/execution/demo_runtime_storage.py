"""Fenced, immutable account binding for the configured shared-pressure provider."""

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, text

from packages.persistence.demo import DemoStore, accounts
from packages.persistence.demo_order_quota import DurableOrderQuota, canonical, quota_require
from packages.persistence.demo_shared_checkpoint import (
    AnchoredSharedWeightJournal,
    SharedWeightAnchorStore,
)
from packages.persistence.demo_shared_weight import POLICY, boot_id
from services.execution.demo_managed_order_quota import STORAGE_KEY, validate_journal_bindings
from services.execution.demo_models import DemoLedger
from services.execution.models import Lease
from services.risk.paper import period_keys

RUNTIME_STORAGE_KEY = "MANAGED_RUNTIME:SHARED_STORAGE_V1"


@dataclass(frozen=True)
class RuntimeStorage:
    order_directory: Path
    shared_directory: Path
    shared_scope: str
    shared_identity: UUID
    shared_anchor: SharedWeightAnchorStore | None = None


@dataclass(frozen=True)
class RuntimeClocks:
    monotonic: Callable[[], float] = time.monotonic
    wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    boot: Callable[[], str] = boot_id


def current_runtime_ledger(store: DemoStore, lease: Lease) -> DemoLedger:
    with store.engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout = '2s'"))
        row = (
            c.execute(select(accounts).where(accounts.c.account_id == lease.account_id))
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


def runtime_storage_binding(storage: RuntimeStorage) -> str:
    anchor = storage.shared_anchor
    quota_require(anchor is not None, "RUNTIME_SHARED_ANCHOR_REQUIRED")
    assert anchor is not None
    quota_require(
        storage.shared_scope.startswith("DEMO-") and len(storage.shared_scope) <= 80,
        "RUNTIME_STORAGE_BINDING_INVALID",
    )
    return canonical(
        dict(
            version=1,
            scope=storage.shared_scope,
            journal=str(storage.shared_identity),
            checkpoint_installation=str(anchor.installation_id()),
            policy=POLICY,
        )
    )


def bind_runtime_storage(
    store: DemoStore, lease: Lease, storage: RuntimeStorage, *, clocks: RuntimeClocks | None = None
) -> str:
    """Explicit configuration metadata write, never called by assemble or run.

    Requires existing clean stores and a current fenced lease. Existing bindings
    are idempotent only when identical; they cannot be overwritten or repaired.
    """
    quota_require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "RUNTIME_BIND_LIVE_CONTEXT_FORBIDDEN",
    )
    quota_require(
        lease.account_id.startswith("DEMO-") and len(lease.account_id) <= 80,
        "RUNTIME_BIND_DEMO_ACCOUNT_REQUIRED",
    )
    anchor = storage.shared_anchor
    quota_require(anchor is not None, "RUNTIME_SHARED_ANCHOR_REQUIRED")
    assert anchor is not None
    quota_require(
        anchor.engine.pool is not store.engine.pool, "RUNTIME_DEDICATED_ANCHOR_POOL_REQUIRED"
    )
    clocks = clocks or RuntimeClocks()
    before = current_runtime_ledger(store, lease)
    expected = runtime_storage_binding(storage)
    old = before.operations.get(RUNTIME_STORAGE_KEY)
    if old is not None:
        quota_require(old == expected, "RUNTIME_STORAGE_BINDING_MISMATCH")
        return expected
    identity = UUID(json.loads(before.operations.get(STORAGE_KEY, "{}"))["journal"])
    reader = DurableOrderQuota(
        storage.order_directory,
        lease.account_id,
        identity,
        read_only=True,
        monotonic=clocks.monotonic,
        wall_clock=clocks.wall_clock,
    )
    try:

        def bind(ledger: DemoLedger) -> None:
            prior = ledger.operations.get(RUNTIME_STORAGE_KEY)
            quota_require(prior is None or prior == expected, "RUNTIME_STORAGE_BINDING_MISMATCH")
            if prior is not None:
                return
            quota_require(
                ledger.paused
                and ledger.risk_state.value == "NORMAL"
                and not any(ledger.positions.values())
                and all(
                    not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
                    for o in ledger.orders.values()
                ),
                "RUNTIME_BIND_REQUIRES_PAUSED_FLAT",
            )
            quota_require(
                period_keys(store.now(), ledger.policy.week_start_kst)
                == (ledger.daily_key, ledger.weekly_key),
                "RUNTIME_PERIOD_KEYS_MISMATCH",
            )
            validate_journal_bindings(ledger, reader)
            quota_require(
                all(f["seal"] is not None for f in reader.flow_records().values()),
                "RUNTIME_PENDING_ORDER_FLOW",
            )
            pressure = AnchoredSharedWeightJournal(
                storage.shared_directory,
                storage.shared_scope,
                storage.shared_identity,
                anchors=anchor,
                read_only=True,
                monotonic=clocks.monotonic,
                wall_clock=clocks.wall_clock,
                boot=clocks.boot,
            ).view()
            quota_require(pressure["startup_ready"], "RUNTIME_SHARED_STARTUP_HOLD")
            quota_require(pressure["pending_attempts"] == 0, "RUNTIME_PENDING_SHARED_REQUESTS")
            ledger.operations[RUNTIME_STORAGE_KEY] = expected

        store.change(lease, "DEMO_RUNTIME_SHARED_STORAGE_BOUND", bind)
        return expected
    finally:
        reader.close()
