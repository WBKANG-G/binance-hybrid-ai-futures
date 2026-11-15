"""Copy-only evidence preparation for stale periods; never adopt a baseline.

Operational bootstrap still requires current periods. This separate opt-in path
only attaches V2 evidence to an audited, paused, flat disposable copy. Clock,
identity, custody and live-venue proof requirements are never invented here.
"""

import copy
import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore, require
from packages.persistence.demo_response_evidence import ResponseEvidenceStore, bindings, events
from packages.persistence.demo_shared_checkpoint import CheckpointContext, SharedWeightAnchorStore
from packages.persistence.demo_shared_weight import SharedWeightJournal
from services.execution.demo_copy_transition_review import review_copy_transition
from services.execution.demo_evidence_snapshot import evidence_snapshot
from services.execution.demo_runtime_storage import RuntimeClocks

REQUIRED_EVIDENCE = (
    "PINNED_COPY_AND_CURRENT_LOCAL_CLOCK",
    "FRESH_REMOTE_ACCOUNT_IDENTITY_BOUND_TO_CREDENTIAL_CONTEXT",
    "SIGNED_USDT_ACCOUNT_BALANCE_AND_ZERO_OR_EXPLAINED_EXPOSURE",
    "REPLAYABLE_RESPONSE_BODIES_BOUND_TO_V2_AUDIT_AND_CHECKPOINTS",
    "COMPLETE_TRADE_INCOME_AND_TRANSFER_COVERAGE_TO_REVIEW_CUTOFF",
    "DAY_BOUNDARY_VALUATION_OR_EXPLICITLY_ACCEPTED_RECONSTRUCTION_SOURCE",
    "EXPLICIT_OPERATOR_ATTESTATION_AND_SOURCE_ACCEPTANCE",
    "CURRENT_PERIOD_RECHECK_AND_LOSS_HIGH_WATER_GATES",
)
ALLOWED_LOCAL_BLOCKERS = {
    "HISTORICAL_REQUESTS_REQUIRE_INDEPENDENT_REMOTE_PROOF",
    "NEW_PERIOD_BASELINE_EVIDENCE_REQUIRED_NO_AUTO_ADOPTION",
}


def _guard(copy_only: bool, account: str, pin: str) -> None:
    require(copy_only is True, "COPY_BASELINE_EXPLICIT_COPY_CONTEXT_REQUIRED")
    require(
        isinstance(account, str) and account.startswith("DEMO-") and len(account) <= 80,
        "COPY_BASELINE_DEMO_ACCOUNT_REQUIRED",
    )
    require(
        isinstance(pin, str) and len(pin) == 64 and all(c in "0123456789abcdef" for c in pin),
        "COPY_BASELINE_SNAPSHOT_PIN_REQUIRED",
    )
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "COPY_BASELINE_LIVE_CONTEXT_FORBIDDEN",
    )


def review_copy_baseline_requirements(
    engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    expected_snapshot_sha256: str,
    *,
    copy_only: bool = False,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    _guard(copy_only, account, expected_snapshot_sha256)
    diagnostic = review_copy_transition(
        engine,
        account,
        directory,
        scope,
        journal,
        installation,
        copy_only_review=True,
        clocks=clocks,
    )
    require(
        diagnostic["snapshot_sha256"] == expected_snapshot_sha256, "COPY_BASELINE_SOURCE_CHANGED"
    )
    try:
        old_day = date.fromisoformat(diagnostic["stored_day_kst"])
        old_week = date.fromisoformat(diagnostic["stored_week_kst"])
    except ValueError:
        require(False, "COPY_BASELINE_STORED_PERIOD_INVALID")
        raise AssertionError("unreachable") from None
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        state = c.execute(
            select(accounts.c.state).where(accounts.c.account_id == account)
        ).scalar_one()
        week_start = state["policy"]["week_start_kst"]
    require(
        type(week_start) is int
        and 0 <= week_start <= 6
        and old_week == old_day - timedelta(days=(old_day.weekday() - week_start) % 7),
        "COPY_BASELINE_STORED_PERIOD_INVALID",
    )
    new_day = date.fromisoformat(diagnostic["current_day_kst"])
    new_week = date.fromisoformat(diagnostic["current_week_kst"])
    require(
        old_day.isoformat() == diagnostic["stored_day_kst"]
        and old_week.isoformat() == diagnostic["stored_week_kst"],
        "COPY_BASELINE_STORED_PERIOD_INVALID",
    )
    if (new_day, new_week) == (old_day, old_week):
        kind = "NO_PERIOD_CHANGE"
    elif new_day > old_day and new_week == old_week:
        kind = "DAY_ONLY"
    elif new_day > old_day and new_week > old_week:
        kind = "DAY_AND_WEEK_SEPARATE_REVIEW"
    else:
        kind = "NONFORWARD_OR_INCONSISTENT_PERIOD_BLOCKED"
    blockers = list(diagnostic["blockers"])
    if kind == "NONFORWARD_OR_INCONSISTENT_PERIOD_BLOCKED":
        blockers.append(kind)
    return dict(
        copy_baseline_review="EVIDENCE_REQUIREMENTS_ONLY_KEEP_PAUSED",
        snapshot_sha256=expected_snapshot_sha256,
        transition_kind=kind,
        stored_day_kst=diagnostic["stored_day_kst"],
        current_day_kst=diagnostic["current_day_kst"],
        stored_week_kst=diagnostic["stored_week_kst"],
        current_week_kst=diagnostic["current_week_kst"],
        weekly_change_proposed=kind == "DAY_AND_WEEK_SEPARATE_REVIEW",
        evidence_binding_required=not diagnostic["v2_bound"],
        clock_matches=diagnostic["clock_matches"],
        historical_unresolved_requests=diagnostic["historical_unresolved_requests"],
        local_blockers=blockers,
        required_evidence=list(REQUIRED_EVIDENCE),
        operator_attestation_received=False,
        baseline_source_accepted=False,
        baseline_amount_proposed=False,
        old_amount_reused=False,
        baseline_adoption_approved=False,
        evidence_reads_enabled=False,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        request_states_changed=False,
        copy_isolation_independently_verified=False,
        exchange_http_attempts=0,
        order_writes=0,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )


def prepare_copy_baseline_evidence(
    engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    expected_snapshot_sha256: str,
    *,
    copy_only: bool = False,
    apply: bool = False,
    readers_stopped: bool = False,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    _guard(copy_only, account, expected_snapshot_sha256)
    require(type(apply) is bool and type(readers_stopped) is bool, "COPY_BASELINE_FLAGS_INVALID")
    require(not apply or readers_stopped, "COPY_BASELINE_READERS_STOPPED_REQUIRED")
    clock = clocks or RuntimeClocks()
    anchors = SharedWeightAnchorStore(engine)
    audit = ReadAuditStore(engine)
    evidence = ResponseEvidenceStore(engine)
    created_schema = created_binding = False
    with engine.connect() as control, control.begin():
        control.execute(text("SET LOCAL lock_timeout='2s'"))
        control.execute(text("SET LOCAL statement_timeout='2s'"))
        control.execute(
            select(accounts.c.account_id)
            .where(accounts.c.account_id == account)
            .with_for_update(read=True)
        ).one()
        control.execute(text("SELECT pg_advisory_xact_lock(717332001228)"))
        plan = review_copy_baseline_requirements(
            engine,
            account,
            directory,
            scope,
            journal,
            installation,
            expected_snapshot_sha256,
            copy_only=True,
            clocks=clock,
        )
        require(
            plan["transition_kind"] != "NONFORWARD_OR_INCONSISTENT_PERIOD_BLOCKED",
            "COPY_BASELINE_NONFORWARD_PERIOD",
        )
        require(
            plan["clock_matches"] and set(plan["local_blockers"]) <= ALLOWED_LOCAL_BLOCKERS,
            "COPY_BASELINE_UNSAFE_LOCAL_STATE",
        )
        before = evidence_snapshot(engine, directory)
        require(before["sha256"] == expected_snapshot_sha256, "COPY_BASELINE_SOURCE_CHANGED")
        old_bindings = []
        if bindings.name in before["tables"]:
            old_bindings = [
                dict(r)
                for r in control.execute(
                    select(bindings).order_by(bindings.c.scope).limit(4097)
                ).mappings()
            ]
            require(len(old_bindings) <= 4096, "COPY_BASELINE_BINDINGS_LIMIT")
        shared = AuditedDemoReadJournal(
            directory,
            scope,
            journal,
            anchors=anchors,
            audit=audit,
            read_only=True,
            monotonic=clock.monotonic,
            wall_clock=clock.wall_clock,
            boot=clock.boot,
        )
        with anchors.scope(scope, journal, read_only=not apply) as cp:
            assert isinstance(cp, CheckpointContext)
            reader = copy.copy(shared)
            marker = reader.checkpoint.set(cp)
            try:
                with SharedWeightJournal._transaction(reader) as (local, meta, now):
                    old, _ = audit._verified_history(cp, local)
                    present = set(inspect(cp.connection).get_table_names()) & {
                        bindings.name,
                        events.name,
                    }
                    require(len(present) in (0, 2), "COPY_BASELINE_PARTIAL_SCHEMA")
                    exists = False
                    if present:
                        evidence._schema(cp.connection)
                        exists = (
                            cp.connection.execute(
                                select(bindings.c.scope).where(bindings.c.scope == scope)
                            ).first()
                            is not None
                        )
                    if exists:
                        evidence.verify(cp, directory, account, old)
                    else:
                        path = evidence.witness(directory, scope)
                        require(
                            not path.exists() and not path.is_symlink(),
                            "COPY_BASELINE_ORPHAN_WITNESS",
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
            finally:
                reader.checkpoint.reset(marker)
        after = evidence_snapshot(engine, directory)
        new_tables = {bindings.name, events.name} if created_schema else set()
        require(
            set(after["tables"]) - set(before["tables"]) == new_tables,
            "COPY_BASELINE_TABLE_DELTA_INVALID",
        )
        if bindings.name in after["tables"]:
            current_bindings = [
                dict(r)
                for r in control.execute(
                    select(bindings).order_by(bindings.c.scope).limit(4097)
                ).mappings()
            ]
            if created_binding:
                require(
                    len(current_bindings) == len(old_bindings) + 1
                    and [r for r in current_bindings if r["scope"] != scope] == old_bindings,
                    "COPY_BASELINE_OTHER_BINDINGS_CHANGED",
                )
            else:
                require(current_bindings == old_bindings, "COPY_BASELINE_OTHER_BINDINGS_CHANGED")
        if created_schema:
            require(after["tables"][events.name]["rows"] == 0, "COPY_BASELINE_UNEXPECTED_EVENTS")
        for name, table in before["tables"].items():
            if name != bindings.name:
                require(after["tables"][name] == table, "COPY_BASELINE_EXISTING_ROWS_CHANGED")
        for section, values in before["catalog"].items():
            require(
                all(
                    after["catalog"][section]["objects"].get(k) == h
                    for k, h in values["objects"].items()
                ),
                "COPY_BASELINE_EXISTING_SCHEMA_CHANGED",
            )
        for section, values in after["catalog"].items():
            additions = set(values["objects"]) - set(before["catalog"][section]["objects"])
            for key in additions:
                identity = json.loads(key)
                allowed = section == "functions" and identity == [
                    "read_response_evidence_immutable_v2",
                    "",
                ]
                allowed = allowed or (
                    section in {"relations", "columns", "constraints", "indexes", "triggers"}
                    and identity[0] in new_tables
                )
                # pg_attribute also describes the two automatically created PK
                # indexes. These are separate catalog relations, not extra tables.
                allowed = allowed or (
                    section == "columns" and identity[0] in {name + "_pkey" for name in new_tables}
                )
                require(created_schema and allowed, "COPY_BASELINE_SCHEMA_DELTA_INVALID")
        require(
            all(after["files"].get(p) == h for p, h in before["files"].items()),
            "COPY_BASELINE_OLD_FILES_CHANGED",
        )
        expected = {evidence.witness(directory, scope).name} if created_binding else set()
        require(
            set(after["files"]) - set(before["files"]) == expected,
            "COPY_BASELINE_FILE_DELTA_INVALID",
        )
        post = review_copy_transition(
            engine,
            account,
            directory,
            scope,
            journal,
            installation,
            copy_only_review=True,
            clocks=clock,
        )
        require(
            post["snapshot_sha256"] == after["sha256"]
            and post["clock_matches"]
            and set(post["blockers"]) <= ALLOWED_LOCAL_BLOCKERS
            and (post["current_day_kst"], post["current_week_kst"])
            == (plan["current_day_kst"], plan["current_week_kst"]),
            "COPY_BASELINE_CONTEXT_CHANGED_DURING_SETUP",
        )
        if not created_schema and not created_binding:
            require(before == after, "COPY_BASELINE_NOOP_CHANGED_STATE")
    return dict(
        copy_baseline_setup="EVIDENCE_AUXILIARY_CONFIGURED_KEEP_PAUSED"
        if apply
        else "READ_ONLY_PLAN_KEEP_PAUSED",
        transition_kind=plan["transition_kind"],
        evidence_schema_required=needed_schema,
        evidence_binding_required=needed_binding,
        evidence_schema_created=created_schema,
        evidence_binding_created=created_binding,
        before_snapshot_sha256=before["sha256"],
        after_snapshot_sha256=after["sha256"],
        prior_audit_events_preserved=len(old),
        v1_history_backfilled=False,
        readers_stopped_attested=readers_stopped,
        copy_isolation_independently_verified=False,
        operator_attestation_received=False,
        baseline_source_accepted=False,
        baseline_adoption_approved=False,
        evidence_reads_enabled=False,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        request_states_changed=False,
        shared_journal_changed=False,
        resource_checkpoint_changed=False,
        automatic_retry=False,
        exchange_http_attempts=0,
        order_writes=0,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
