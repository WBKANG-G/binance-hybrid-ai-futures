"""Empty idle-account DB restart checkpoint; no Docker control or exchange writes."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text

from integrations.binance_usdm.demo import DemoClient, DemoFailure
from integrations.binance_usdm.demo_snapshot import operational_snapshot
from integrations.binance_usdm.recovery_transport import recovery_connection
from packages.persistence.demo import DemoStore, accounts
from services.execution.demo_models import DemoLedger
from services.execution.demo_recovery_budget import RecoveryReadClient
from services.execution.models import Lease

SCOPE = "EMPTY_IDLE_DB_SERVER_RESTART"
CODES = frozenset(
    {
        "EMPTY_NORMAL_PAUSED_REQUIRED",
        "READ_ONLY_CONTEXT_REQUIRED",
        "EXCHANGE_NOT_EMPTY",
        "SNAPSHOT_CHANGED",
        "HISTORY_CHANGED",
        "CHECKPOINT_MISSING",
        "CHECKPOINT_INVALID",
        "OPERATION_ALREADY_EXISTS",
        "POSTGRES_RESTART_NOT_OBSERVED",
        "FENCE_CHANGED",
        "LEASE_RELEASE_NOT_VERIFIED",
        "LIVE_CONTEXT_FORBIDDEN",
        "CHECK_FAILED",
    }
)


class DBRestartBlocked(DemoFailure):
    def __init__(self, code: str):
        self.code = code if code in CODES else "CHECK_FAILED"
        super().__init__(self.code)


def tags(operation: UUID) -> tuple[str, str]:
    return str(uuid5(operation, "empty-db-before")), str(uuid5(operation, "empty-db-after"))


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def history_digest(ledger: DemoLedger, operation: UUID) -> str:
    data = ledger.model_dump(mode="json", exclude={"stream_ready", "reconciled"})
    before, after = tags(operation)
    data["operations"] = {k: v for k, v in ledger.operations.items() if k not in {before, after}}
    return digest(data)


def empty(ledger: DemoLedger) -> None:
    if (
        ledger.risk_state.value != "NORMAL"
        or not ledger.paused
        or ledger.margin_mode != "ISOLATED"
        or ledger.symbols != ("BTCUSDT",)
        or ledger.verified_leverage.get("BTCUSDT") != 2
        or any(ledger.positions.values())
        or any(
            o.remaining
            or o.open_quantity
            or o.actual_risk
            or o.protected
            or o.position_state.value != "CLOSED"
            for o in ledger.orders.values()
        )
    ):
        raise DBRestartBlocked("EMPTY_NORMAL_PAUSED_REQUIRED")


def server_started(store: DemoStore) -> str:
    with store.engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout = '2s'"))
        value = conn.execute(text("SELECT pg_postmaster_start_time()")).scalar_one()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise DBRestartBlocked("CHECKPOINT_INVALID")
    return value.astimezone(UTC).isoformat()


def released(store: DemoStore, lease: Lease) -> bool:
    with store.engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        row = conn.execute(
            select(accounts.c.owner, accounts.c.fence).where(
                accounts.c.account_id == lease.account_id
            )
        ).one()
    return row.owner is None and row.fence == lease.fence


def exchange_digest(
    store: DemoStore, client: DemoClient, ledger: DemoLedger
) -> tuple[str, dict[str, str | int]]:
    if (
        client.settings.demo_trading_enabled
        or client.settings.margin_mode != "ISOLATED"
        or client.settings.symbols != ("BTCUSDT",)
        or client.settings.policy != ledger.policy
    ):
        raise DBRestartBlocked("READ_ONLY_CONTEXT_REQUIRED")
    with recovery_connection(client) as pooled:
        bounded = RecoveryReadClient(client)
        hashes = []
        for _ in range(2):
            snap = operational_snapshot(bounded, ledger, store.now())
            if (
                any(snap.positions.values())
                or snap.margin_used
                or any(r["status"] in {"NEW", "PARTIALLY_FILLED"} for r in snap.orders.values())
                or any(r["algoStatus"] == "NEW" for r in snap.algos.values())
            ):
                raise DBRestartBlocked("EXCHANGE_NOT_EMPTY")
            hashes.append(digest(snap.model_dump(mode="json", exclude={"as_of"})))
        bounded.budget.check()
        if hashes[0] != hashes[1]:
            raise DBRestartBlocked("SNAPSHOT_CHANGED")
        return hashes[-1], {
            **(pooled.diagnostics() if pooled else {"audit_transport_mode": "UNPOOLED_TRANSPORT"}),
            "signed_get_count": bounded.budget.signed_queries,
            "clock_probe_count": bounded.budget.clock_probes,
        }


def check(
    store: DemoStore, lease: Lease, client: DemoClient, operation: UUID, *, prepare: bool
) -> dict[str, Any]:
    state = store.read(lease.account_id)
    empty(state)
    before_tag, after_tag = tags(operation)
    if (
        after_tag in state.operations
        or str(operation) in state.operations
        or str(operation) in state.orders
    ):
        raise DBRestartBlocked("OPERATION_ALREADY_EXISTS")
    local = history_digest(state, operation)
    started = server_started(store)
    record: dict[str, Any]
    if prepare:
        if before_tag in state.operations:
            raise DBRestartBlocked("OPERATION_ALREADY_EXISTS")
        record = {
            "schema": 1,
            "scope": SCOPE,
            "operation_id": str(operation),
            "history_digest": local,
            "server_started": started,
            "fence": lease.fence,
        }
    else:
        if before_tag not in state.operations:
            raise DBRestartBlocked("CHECKPOINT_MISSING")
        try:
            record = json.loads(state.operations[before_tag])
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "schema",
                    "scope",
                    "operation_id",
                    "history_digest",
                    "exchange_digest",
                    "server_started",
                    "fence",
                }
                or record["schema"] != 1
                or record["scope"] != SCOPE
                or record["operation_id"] != str(operation)
                or type(record["fence"]) is not int
            ):
                raise ValueError
            old_started = datetime.fromisoformat(record["server_started"])
            if old_started.tzinfo is None:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise DBRestartBlocked("CHECKPOINT_INVALID") from None
        if local != record["history_digest"]:
            raise DBRestartBlocked("HISTORY_CHANGED")
        if lease.fence != record["fence"] + 1:
            raise DBRestartBlocked("FENCE_CHANGED")
        if datetime.fromisoformat(started) <= old_started:
            raise DBRestartBlocked("POSTGRES_RESTART_NOT_OBSERVED")
    remote, metrics = exchange_digest(store, client, state)
    if not prepare and remote != record["exchange_digest"]:
        raise DBRestartBlocked("SNAPSHOT_CHANGED")
    if prepare:
        record["exchange_digest"] = remote

    def save(ledger: DemoLedger) -> None:
        empty(ledger)
        if history_digest(ledger, operation) != local:
            raise DBRestartBlocked("HISTORY_CHANGED")
        if (
            ledger.operations.get(before_tag) != state.operations.get(before_tag)
            or after_tag in ledger.operations
        ):
            raise DBRestartBlocked("CHECKPOINT_INVALID")
        if server_started(store) != started:
            raise DBRestartBlocked("POSTGRES_RESTART_NOT_OBSERVED")
        if prepare:
            ledger.operations[before_tag] = json.dumps(
                record, sort_keys=True, separators=(",", ":")
            )
        else:
            ledger.operations[after_tag] = "EMPTY_DB_RESTART_HISTORY_PRESERVED_VERIFIED"

    store.change(
        lease,
        "DEMO_EMPTY_DB_RESTART_PREPARED" if prepare else "DEMO_EMPTY_DB_RESTART_VERIFIED",
        save,
    )
    final = store.read(lease.account_id)
    if history_digest(final, operation) != local:
        raise DBRestartBlocked("HISTORY_CHANGED")
    return {
        "db_restart_check": "PREPARED_EMPTY_NO_ORDERS"
        if prepare
        else "EMPTY_DB_RESTART_HISTORY_PRESERVED_VERIFIED",
        "scope": SCOPE,
        "operation_id": str(operation),
        "history_and_baselines_preserved": True,
        "postgres_restart_observed": not prepare,
        "server_started_before_utc": record["server_started"],
        "server_started_current_utc": started,
        "fence_before": record["fence"],
        "fence_current": lease.fence,
        "exchange_empty_verified": True,
        "risk_state": final.risk_state.value,
        "paused": final.paused,
        "entry_signal_supplied": False,
        "new_entry_post_count": 0,
        "orders_enabled": False,
        "live_trading_enabled": False,
        "read_diagnostic": metrics,
    }
