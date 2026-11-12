"""Atomic audit schema/binding setup for the account's existing empty journal.

No account/financial writes, clock repair, request sending, or implicit adoption.
"""

import copy
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Connection, Engine

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import ReadAuditStore, read_bindings, snapshot
from packages.persistence.demo_read_audit import require as require
from packages.persistence.demo_read_recovery import verify_recovery_backups
from packages.persistence.demo_shared_checkpoint import (
    AnchoredSharedWeightJournal,
    CheckpointContext,
    SharedWeightAnchorStore,
)
from packages.persistence.demo_shared_weight import POLICY, STARTUP_HOLD, SharedWeightJournal
from services.execution.demo_models import DemoLedger
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY, RuntimeClocks
from services.risk.paper import period_keys


def _validate_schema(connection: Connection) -> None:
    rows = connection.execute(
        text("""
        SELECT r.relname,t.tgname,t.tgtype,t.tgenabled,p.prosrc,p.prosecdef
        FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=r.relnamespace
        JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE n.nspname=current_schema() AND NOT t.tgisinternal
        AND r.relname IN ('demo_read_audit_bindings','demo_read_audit_events')
    """)
    ).all()
    require(
        len(rows) == 2
        and {r[0] for r in rows} == {"demo_read_audit_bindings", "demo_read_audit_events"}
        and all(
            r[1] == "demo_read_audit_immutable"
            and r[2] == 27
            and r[3] in {"O", "A"}
            and " ".join(r[4].split()) == "BEGIN RAISE EXCEPTION 'demo_read_audit_append_only'; END"
            and r[5] is False
            for r in rows
        ),
        "READ_SETUP_IMMUTABILITY_SCHEMA_INVALID",
    )


def validate_account_row(
    row: Any, scope: str, journal: UUID, installation: UUID, clock: RuntimeClocks
) -> None:
    ledger = DemoLedger.model_validate(row["state"])
    require(
        row["owner"] is None
        and (row["lease_until"] is None or row["lease_until"] <= clock.wall_clock()),
        "READ_SETUP_ACTIVE_OWNER",
    )
    require(
        ledger.paused and ledger.risk_state.value == "NORMAL",
        "READ_SETUP_PAUSED_NORMAL_REQUIRED",
    )
    require(
        not any(ledger.positions.values())
        and all(
            not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
            for o in ledger.orders.values()
        ),
        "READ_SETUP_LOCAL_FLAT_REQUIRED",
    )
    require(
        period_keys(clock.wall_clock(), ledger.policy.week_start_kst)
        == (ledger.daily_key, ledger.weekly_key),
        "READ_SETUP_PERIOD_MISMATCH",
    )
    expected = dict(
        version=1,
        scope=scope,
        journal=str(journal),
        checkpoint_installation=str(installation),
        policy=POLICY,
    )
    require(
        json.loads(ledger.operations.get(RUNTIME_STORAGE_KEY, "{}")) == expected,
        "READ_SETUP_ACCOUNT_BINDING_MISMATCH",
    )


def prepare_read_audit(
    engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    *,
    apply: bool = False,
    readers_stopped: bool = False,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    require(type(apply) is bool and type(readers_stopped) is bool, "READ_SETUP_FLAGS_INVALID")
    require(not apply or readers_stopped, "READ_SETUP_READERS_STOPPED_REQUIRED")
    require(account.startswith("DEMO-") and len(account) <= 80, "READ_SETUP_DEMO_ACCOUNT_REQUIRED")
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "READ_SETUP_LIVE_CONTEXT_FORBIDDEN",
    )
    clock = clocks or RuntimeClocks()
    anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
    require(anchor.installation_id() == installation, "READ_SETUP_INSTALLATION_MISMATCH")
    opts = dict(monotonic=clock.monotonic, wall_clock=clock.wall_clock, boot=clock.boot)
    created_schema = created_binding = False
    # Lock ordering: account row (shared), global schema lock, PG scope, SQLite.
    # Account mutations/leader acquisitions cannot pass the shared row lock.
    with engine.connect() as control, control.begin():
        control.execute(text("SET LOCAL statement_timeout='2s'"))
        control.execute(text("SET LOCAL lock_timeout='2s'"))
        row = (
            control.execute(
                select(accounts).where(accounts.c.account_id == account).with_for_update(read=True)
            )
            .mappings()
            .one()
        )
        validate_account_row(row, scope, journal, installation, clock)
        # Global lock serializes schema creation across cooperating scopes/accounts.
        control.execute(text("SELECT pg_advisory_xact_lock(717332001228)"))
        shared = AnchoredSharedWeightJournal(
            directory, scope, journal, anchors=anchor, read_only=True, **opts
        )
        with anchor.scope(scope, journal, read_only=not apply) as cp:
            assert isinstance(cp, CheckpointContext)
            reader = copy.copy(shared)
            marker = reader.checkpoint.set(cp)
            try:
                with SharedWeightJournal._transaction(reader) as (local, meta, now):
                    from services.execution.demo_unused_read_clock import verify_backups

                    verify_backups(directory, cp, installation)
                    tables = set(inspect(cp.connection).get_table_names())
                    present = tables & {"demo_read_audit_bindings", "demo_read_audit_events"}
                    require(len(present) in (0, 2), "READ_SETUP_PARTIAL_SCHEMA")
                    if present:
                        _validate_schema(cp.connection)
                    exists = (
                        bool(present)
                        and cp.connection.execute(
                            select(read_bindings.c.scope).where(read_bindings.c.scope == scope)
                        ).first()
                        is not None
                    )
                    if exists:
                        rows, state = audit._verified_history(cp, local)
                        verify_recovery_backups(directory, rows)
                    else:
                        state = snapshot(local)
                        require(
                            meta["total"] == 0 and not state[2], "READ_AUDIT_USED_STORE_NOT_ADOPTED"
                        )
                        verify_recovery_backups(directory, [])
                    require(now - meta["start"] >= STARTUP_HOLD, "READ_SETUP_STARTUP_HOLD")
                    require(all(r[4] is not None for r in state[2]), "READ_SETUP_PENDING_REQUESTS")
                    needed_schema, needed_binding = not present, not exists
                    if apply:
                        if needed_schema:
                            audit._provision_on(cp.connection)
                            _validate_schema(cp.connection)
                            created_schema = True
                        if needed_binding:
                            audit._bind_empty_on(cp, local, meta)
                            created_binding = True
                        audit._verified_history(cp, local)
            finally:
                reader.checkpoint.reset(marker)
    return dict(
        read_audit_setup="CONFIGURED_KEEP_PAUSED"
        if apply or not needed_binding
        else "PLAN_READY_KEEP_PAUSED",
        audit_schema_required=needed_schema,
        audit_binding_required=needed_binding,
        audit_schema_created=created_schema,
        audit_binding_created=created_binding,
        account_binding_verified=True,
        local_account_state_checked=True,
        exchange_state_checked=False,
        readers_stopped_attested=readers_stopped,
        readers_stopped_independently_verified=False,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        shared_journal_changed=False,
        resource_checkpoint_changed=False,
        exchange_http_attempts=0,
        order_writes=0,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        next_action="KEEP_PAUSED_REVIEW_ONLY",
    )
