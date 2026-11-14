"""Read-only copy assessment; no clock rebase, baseline adoption or request repair.

copy_only_review is an operator attestation, never independent Docker isolation
proof. No Windows runner is supplied until that boundary is separately checked.
"""

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import require
from packages.persistence.demo_read_recovery import _clock, _stable, _valid, verify_recovery_backups
from packages.persistence.demo_response_evidence import ResponseEvidenceStore, bindings, events
from packages.persistence.demo_shared_weight import POLICY, STARTUP_HOLD
from services.execution.demo_evidence_snapshot import evidence_snapshot
from services.execution.demo_models import DemoLedger
from services.execution.demo_runtime_storage import RUNTIME_STORAGE_KEY, RuntimeClocks
from services.risk.paper import period_keys


def review_copy_transition(
    engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    *,
    copy_only_review: bool = False,
    clocks: RuntimeClocks | None = None,
) -> dict[str, Any]:
    require(copy_only_review is True, "COPY_TRANSITION_EXPLICIT_COPY_REVIEW_REQUIRED")
    require(
        isinstance(account, str) and account.startswith("DEMO-") and len(account) <= 80,
        "COPY_TRANSITION_DEMO_ACCOUNT_REQUIRED",
    )
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "COPY_TRANSITION_LIVE_CONTEXT_FORBIDDEN",
    )
    clock = clocks or RuntimeClocks()
    initial = _clock(clock)
    evidence = ResponseEvidenceStore(engine)
    require(
        evidence.anchors.installation_id() == installation, "COPY_TRANSITION_INSTALLATION_MISMATCH"
    )
    before = evidence_snapshot(engine, directory)
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        row = c.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
        ledger = DemoLedger.model_validate(row["state"])
    require(
        json.loads(ledger.operations.get(RUNTIME_STORAGE_KEY, "{}"))
        == dict(
            version=1,
            scope=scope,
            journal=str(journal),
            checkpoint_installation=str(installation),
            policy=POLICY,
        ),
        "COPY_TRANSITION_ACCOUNT_BINDING_MISMATCH",
    )
    with evidence.raw(directory, scope, journal) as (cp, local):
        history, state = evidence.audit._verified_history(cp, local)
        verify_recovery_backups(directory, history)
        meta = state[1][0]
        require(
            meta[:3] == [str(journal), scope, POLICY], "COPY_TRANSITION_JOURNAL_BINDING_MISMATCH"
        )
        present = set(inspect(cp.connection).get_table_names()) & {bindings.name, events.name}
        require(len(present) in (0, 2), "COPY_TRANSITION_PARTIAL_V2_SCHEMA")
        bound = False
        evidence_pending = 0
        if present:
            evidence._schema(cp.connection)
            bound = (
                cp.connection.execute(
                    select(bindings.c.scope).where(bindings.c.scope == scope)
                ).first()
                is not None
            )
        if bound:
            _, _, pending, _ = evidence.verify(cp, directory, account, history)
            evidence_pending = len(pending)
        else:
            path = evidence.witness(directory, scope)
            require(not path.exists() and not path.is_symlink(), "COPY_TRANSITION_ORPHAN_WITNESS")
        budget_pending = sum(r[4] is None for r in state[2])
        valid = _valid(meta, initial)
        stopped = bool(meta[8])
    current_day, current_week = period_keys(clock.wall_clock(), ledger.policy.week_start_kst)
    period_match = (current_day, current_week) == (ledger.daily_key, ledger.weekly_key)
    unresolved = sum(
        v in {"SUBMITTING", "UNKNOWN", "NOT_FOUND_UNRESOLVED"} for v in ledger.requests.values()
    )
    local_flat = not any(ledger.positions.values()) and all(
        not o.remaining and not o.open_quantity and o.position_state.value == "CLOSED"
        for o in ledger.orders.values()
    )
    blockers = []
    if row["owner"] is not None or (
        row["lease_until"] is not None and row["lease_until"] > clock.wall_clock()
    ):
        blockers.append("ACTIVE_OWNER_OR_LEASE")
    if not ledger.paused or ledger.risk_state.value != "NORMAL":
        blockers.append("PAUSED_NORMAL_REQUIRED")
    if not local_flat:
        blockers.append("LOCAL_EXPOSURE_NOT_FLAT")
    if stopped:
        blockers.append("SHARED_STORE_STOPPED_NO_RESET")
    if budget_pending or evidence_pending:
        blockers.append("PENDING_READ_OUTCOME_NO_EXPIRY_OR_RETRY")
    if unresolved:
        blockers.append("HISTORICAL_REQUESTS_REQUIRE_INDEPENDENT_REMOTE_PROOF")
    if not period_match:
        blockers.append("NEW_PERIOD_BASELINE_EVIDENCE_REQUIRED_NO_AUTO_ADOPTION")
    if not valid:
        blockers.append(
            "V2_CLOCK_TRANSITION_UNSUPPORTED"
            if bound
            else "EXPLICIT_COPY_CLOCK_TRANSITION_REVIEW_REQUIRED"
        )
    if valid and initial[0] - meta[4] < STARTUP_HOLD:
        blockers.append("STARTUP_HOLD_NOT_ELAPSED")
    _stable(initial, _clock(clock))
    after = evidence_snapshot(engine, directory)
    _stable(initial, _clock(clock))
    require(
        period_keys(clock.wall_clock(), ledger.policy.week_start_kst)
        == (current_day, current_week),
        "COPY_TRANSITION_PERIOD_CHANGED_DURING_REVIEW",
    )
    require(before == after, "COPY_TRANSITION_STATE_CHANGED_DURING_REVIEW")
    return dict(
        copy_transition_review="ASSESSMENT_COMPLETE_KEEP_STOPPED",
        snapshot_sha256=before["sha256"],
        copy_isolation_attested=True,
        copy_isolation_independently_verified=False,
        writers_stopped_verified=False,
        stored_day_kst=ledger.daily_key,
        current_day_kst=current_day,
        stored_week_kst=ledger.weekly_key,
        current_week_kst=current_week,
        period_keys_match=period_match,
        boot_matches=meta[3] == initial[2],
        clock_matches=valid,
        v2_bound=bound,
        prior_audit_events=len(history),
        historical_unresolved_requests=unresolved,
        pending_read_attempts=budget_pending,
        pending_evidence_requests=evidence_pending,
        blockers=blockers,
        review_found_no_local_transition_blockers=not blockers,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        clock_rebased=False,
        request_states_changed=False,
        shared_journal_changed=False,
        resource_checkpoint_changed=False,
        automatic_retry=False,
        exchange_http_attempts=0,
        order_writes=0,
        originals_started_by_review=False,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
        next_action="REVIEW_COPY_ONLY_CLOCK_EVENT_AND_BASELINE_EVIDENCE_CONTRACT_KEEP_STOPPED",
    )
