"""Uninstalled GET-only V2 setup candidate; no clock repair or account adoption.

Schema and binding use one PG transaction. The independent file witness cannot
be atomically committed with PG: any orphan/gap blocks subsequent setup, without
cleanup, backfill, or automatic retry. Callers must review ambiguous outcomes.
"""

import copy
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore, require
from packages.persistence.demo_response_evidence import ResponseEvidenceStore, bindings, events
from packages.persistence.demo_shared_checkpoint import CheckpointContext, SharedWeightAnchorStore
from packages.persistence.demo_shared_weight import STARTUP_HOLD, SharedWeightJournal
from services.execution.demo_read_audit_bootstrap import _validate_schema, validate_account_row
from services.execution.demo_runtime_storage import RuntimeClocks


def prepare_response_evidence(
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
    require(type(apply) is bool and type(readers_stopped) is bool, "GET_SETUP_FLAGS_INVALID")
    require(not apply or readers_stopped, "GET_SETUP_READERS_STOPPED_REQUIRED")
    require(
        isinstance(account, str) and account.startswith("DEMO-") and len(account) <= 80,
        "GET_SETUP_DEMO_ACCOUNT_REQUIRED",
    )
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "GET_SETUP_LIVE_CONTEXT_FORBIDDEN",
    )
    clock = clocks or RuntimeClocks()
    anchor, audit, evidence = (
        SharedWeightAnchorStore(engine),
        ReadAuditStore(engine),
        ResponseEvidenceStore(engine),
    )
    require(anchor.installation_id() == installation, "GET_SETUP_INSTALLATION_MISMATCH")
    created_schema = created_binding = False
    # Keep the account shared lock throughout the independent scope transaction.
    # Cooperating setup uses the same global lock as V1: account, schema, scope, SQLite.
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
        control.execute(text("SELECT pg_advisory_xact_lock(717332001228)"))
        _validate_schema(control)
        # A used V1 journal must already have complete verified history. This
        # constructor is explicitly read-only, including on clock failure.
        shared = AuditedDemoReadJournal(
            directory,
            scope,
            journal,
            anchors=anchor,
            audit=audit,
            read_only=True,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
            boot=clock.boot,
        )
        with anchor.scope(scope, journal, read_only=not apply) as cp:
            assert isinstance(cp, CheckpointContext)
            reader = copy.copy(shared)
            marker = reader.checkpoint.set(cp)
            try:
                with SharedWeightJournal._transaction(reader) as (local, meta, now):
                    old, state = audit._verified_history(cp, local)
                    require(now - meta["start"] >= STARTUP_HOLD, "GET_SETUP_STARTUP_HOLD")
                    require(all(r[4] is not None for r in state[2]), "GET_SETUP_PENDING_REQUESTS")
                    present = set(inspect(cp.connection).get_table_names()) & {
                        bindings.name,
                        events.name,
                    }
                    require(len(present) in (0, 2), "GET_SETUP_PARTIAL_SCHEMA")
                    if present:
                        evidence._schema(cp.connection)
                    exists = (
                        bool(present)
                        and cp.connection.execute(
                            select(bindings.c.scope).where(bindings.c.scope == scope)
                        ).first()
                        is not None
                    )
                    if exists:
                        _, _, pending, _ = evidence.verify(cp, directory, account, old)
                        require(not pending, "GET_SETUP_PENDING_EVIDENCE")
                    else:
                        path = evidence.witness(directory, scope)
                        require(
                            not path.exists() and not path.is_symlink(),
                            "RESPONSE_EVIDENCE_ORPHAN_WITNESS",
                        )
                    needed_schema, needed_binding = not present, not exists
                    if apply:
                        if needed_schema:
                            evidence._provision_on(cp.connection)
                            created_schema = True
                        if needed_binding:
                            evidence._bind_on(cp, directory, account, local, meta, now)
                            created_binding = True
                        evidence.verify(cp, directory, account, old)
                    # Recheck before PG commit; never rebase or roll the account day.
                    from packages.persistence.demo_response_evidence import verify_clock

                    verify_clock(meta, clock.monotonic(), clock.wall_clock(), clock.boot())
                    validate_account_row(row, scope, journal, installation, clock)
            finally:
                reader.checkpoint.reset(marker)
    return dict(
        get_evidence_setup="CONFIGURED_KEEP_PAUSED"
        if apply or not needed_binding
        else "PLAN_READY_KEEP_PAUSED",
        evidence_schema_required=needed_schema,
        evidence_binding_required=needed_binding,
        evidence_schema_created=created_schema,
        evidence_binding_created=created_binding,
        prior_audit_events_preserved=len(old),
        prior_audit_backfilled=False,
        readers_stopped_attested=readers_stopped,
        readers_stopped_independently_verified=False,
        clock_change_policy="FAIL_CLOSED_NO_AUTOMATIC_REBASE_OR_RETRY",
        financial_ledger_changed=False,
        period_baselines_changed=False,
        shared_journal_changed=False,
        resource_checkpoint_changed=False,
        automatic_retry=False,
        exchange_http_attempts=0,
        order_writes=0,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        next_action="KEEP_STOPPED_REVIEW_FULL_V2_SNAPSHOT_AND_WRITE_BOUNDARIES",
    )
