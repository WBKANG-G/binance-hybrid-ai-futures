"""Explicit V1 clock rehearsal on an independently isolated disposable copy.

No baseline adoption, response evidence binding, financial writes or HTTP. Any
failure may leave a changed COPY; preserve it and do not automatically retry.
"""

import hashlib
import math
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import ReadAuditStore, read_events, require
from packages.persistence.demo_read_recovery import (
    PREFIX,
    _clock,
    _stable,
    recover_completed_read_clock,
)
from packages.persistence.demo_response_evidence import ResponseEvidenceStore
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore, checkpoints
from packages.persistence.demo_shared_weight import STARTUP_HOLD
from services.execution.demo_copy_transition_review import review_copy_transition
from services.execution.demo_evidence_snapshot import digest, evidence_snapshot
from services.execution.demo_runtime_storage import RuntimeClocks

ALLOWED_TABLES = {read_events.name, checkpoints.name}
ALLOWED_BLOCKERS = {
    "HISTORICAL_REQUESTS_REQUIRE_INDEPENDENT_REMOTE_PROOF",
    "NEW_PERIOD_BASELINE_EVIDENCE_REQUIRED_NO_AUTO_ADOPTION",
    "EXPLICIT_COPY_CLOCK_TRANSITION_REVIEW_REQUIRED",
}


def _all_auxiliary(engine: Engine) -> dict[str, list[Any]]:
    result = {}
    with engine.connect() as c, c.begin():
        c.execute(text("SET TRANSACTION READ ONLY"))
        c.execute(text("SET LOCAL statement_timeout='2s'"))
        for table in (read_events, checkpoints):
            rows = (
                c.execute(select(table).order_by(*table.primary_key.columns).limit(4097))
                .mappings()
                .all()
            )
            require(len(rows) <= 4096, "COPY_CLOCK_AUXILIARY_LIMIT")
            result[table.name] = [dict(r) for r in rows]
    return result


def _protected(snapshot: dict[str, Any]) -> str:
    return digest(
        dict(
            catalog=snapshot["catalog"],
            tables={k: v for k, v in snapshot["tables"].items() if k not in ALLOWED_TABLES},
        )
    )


def rehearse_copy_clock(
    read_engine: Engine,
    write_engine: Engine,
    account: str,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    expected_snapshot_sha256: str,
    *,
    apply: bool = False,
    copy_only: bool = False,
    clocks: RuntimeClocks | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    progress: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    require(apply is True and copy_only is True, "COPY_CLOCK_EXPLICIT_COPY_APPLY_REQUIRED")
    require(
        read_engine is not write_engine and read_engine.url == write_engine.url,
        "COPY_CLOCK_SAME_COPY_DATABASE_REQUIRED",
    )
    require(
        isinstance(expected_snapshot_sha256, str)
        and len(expected_snapshot_sha256) == 64
        and all(c in "0123456789abcdef" for c in expected_snapshot_sha256),
        "COPY_CLOCK_SNAPSHOT_PIN_INVALID",
    )
    require(
        os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "false"
        and os.getenv("TRADING_MODE", "PAPER").upper() != "LIVE",
        "COPY_CLOCK_LIVE_CONTEXT_FORBIDDEN",
    )
    clock = clocks or RuntimeClocks()
    initial = _clock(clock)
    reader = ResponseEvidenceStore(read_engine)
    # Keep the account row shared lock while the independently fenced scope
    # transaction appends its clock event. No financial row UPDATE is issued.
    with write_engine.connect() as control, control.begin():
        control.execute(text("SET LOCAL lock_timeout='2s'"))
        control.execute(text("SET LOCAL statement_timeout='2s'"))
        control.execute(
            select(accounts.c.account_id)
            .where(accounts.c.account_id == account)
            .with_for_update(read=True)
        ).one()
        control.execute(text("SELECT pg_advisory_xact_lock(717332001228)"))
        before_report = review_copy_transition(
            read_engine,
            account,
            directory,
            scope,
            journal,
            installation,
            copy_only_review=True,
            clocks=clock,
        )
        require(
            before_report["snapshot_sha256"] == expected_snapshot_sha256,
            "COPY_CLOCK_PRIOR_DIAGNOSTIC_MISMATCH",
        )
        require(not before_report["v2_bound"], "COPY_CLOCK_V2_BOUND_FORBIDDEN")
        require(not before_report["clock_matches"], "COPY_CLOCK_TRANSITION_NOT_REQUIRED")
        require(set(before_report["blockers"]) <= ALLOWED_BLOCKERS, "COPY_CLOCK_UNSAFE_LOCAL_STATE")
        before = evidence_snapshot(read_engine, directory)
        require(
            before["sha256"] == expected_snapshot_sha256, "COPY_CLOCK_STATE_CHANGED_BEFORE_APPLY"
        )
        auxiliary = _all_auxiliary(read_engine)
        with reader.raw(directory, scope, journal) as (cp, local):
            old_history, old_state = reader.audit._verified_history(cp, local)
            revision = cp.revision
        require(
            len(old_history) == before_report["prior_audit_events"]
            and all(
                len(auxiliary[name]) == before["tables"][name]["rows"] for name in ALLOWED_TABLES
            ),
            "COPY_CLOCK_STATE_CHANGED_BEFORE_APPLY",
        )
        anchors = SharedWeightAnchorStore(write_engine)
        require(anchors.installation_id() == installation, "COPY_CLOCK_INSTALLATION_MISMATCH")
        result = recover_completed_read_clock(
            directory,
            scope,
            journal,
            anchor=anchors,
            audit=ReadAuditStore(write_engine),
            apply=True,
            clocks=clock,
        )
        require(
            result["shared_journal_changed"]
            and result["resource_checkpoint_changed"]
            and result["clock_recovery_record_appended"],
            "COPY_CLOCK_EVENT_REQUIRED",
        )
        with reader.raw(directory, scope, journal) as (cp, local):
            history, state = reader.audit._verified_history(cp, local)
            require(
                cp.revision == revision + 1
                and len(history) == len(old_history) + 1
                and history[:-1] == old_history
                and history[-1]["action"]["kind"] == "CLOCK_REBASE",
                "COPY_CLOCK_AUDIT_DELTA_INVALID",
            )
            require(state[1][0][7:] == old_state[1][0][7:], "COPY_CLOCK_CHARGE_OR_STOP_CHANGED")
        after_aux = _all_auxiliary(read_engine)
        for table, key, new_value in (
            (read_events, "sequence", len(old_history)),
            (checkpoints, "revision", revision + 1),
        ):
            rows = after_aux[table.name]
            appended = [r for r in rows if r["scope"] == scope and r[key] == new_value]
            preserved = [r for r in rows if not (r["scope"] == scope and r[key] == new_value)]
            require(
                len(appended) == 1 and preserved == auxiliary[table.name],
                "COPY_CLOCK_AUXILIARY_ROWS_CHANGED",
            )
        after = evidence_snapshot(read_engine, directory)
        require(_protected(after) == _protected(before), "COPY_CLOCK_PROTECTED_DATABASE_CHANGED")
        sqlite_name = hashlib.sha256(scope.encode()).hexdigest() + ".weight.sqlite3"
        folder = PREFIX + history[-1]["action"]["recovery"]["operation_id"]
        additions = {folder + "/original.sqlite3", folder + "/intent.json"}
        require(
            set(after["files"]) == set(before["files"]) | additions
            and all(after["files"][p] == h for p, h in before["files"].items() if p != sqlite_name)
            and after["files"][folder + "/original.sqlite3"] == before["files"][sqlite_name],
            "COPY_CLOCK_FILE_DELTA_INVALID",
        )
    # Wait conservatively from AFTER the commit; re-read the Linux clock in a
    # fresh read-only session afterwards. No pressure credit is reset/refunded.
    target = clock.monotonic() + STARTUP_HOLD + 0.1
    while True:
        _stable(initial, _clock(clock))
        remaining = target - clock.monotonic()
        if remaining <= 0:
            break
        if progress is not None:
            progress(math.ceil(remaining))
        sleeper(min(15.0, remaining))
    report = review_copy_transition(
        read_engine,
        account,
        directory,
        scope,
        journal,
        installation,
        copy_only_review=True,
        clocks=clock,
    )
    _stable(initial, _clock(clock))
    require(
        report["clock_matches"] and "STARTUP_HOLD_NOT_ELAPSED" not in report["blockers"],
        "COPY_CLOCK_HOLD_OR_CLOCK_INVALID",
    )
    require(
        (report["current_day_kst"], report["current_week_kst"])
        == (before_report["current_day_kst"], before_report["current_week_kst"]),
        "COPY_CLOCK_PERIOD_CHANGED_DURING_REHEARSAL",
    )
    require(report["snapshot_sha256"] == after["sha256"], "COPY_CLOCK_STATE_CHANGED_DURING_HOLD")
    require(
        report["stored_day_kst"] == before_report["stored_day_kst"]
        and report["stored_week_kst"] == before_report["stored_week_kst"]
        and report["historical_unresolved_requests"]
        == before_report["historical_unresolved_requests"],
        "COPY_CLOCK_FINANCIAL_STATE_CHANGED",
    )
    return dict(
        copy_clock_rehearsal="CLOCK_REBASED_HOLD_VERIFIED_KEEP_STOPPED",
        before_snapshot_sha256=before["sha256"],
        after_snapshot_sha256=after["sha256"],
        protected_database_sha256=_protected(after),
        prior_audit_events=len(old_history),
        audit_events_after=len(history),
        prior_revision=revision,
        revision_after=revision + 1,
        cumulative_weight_before=old_state[1][0][7],
        cumulative_weight_after=state[1][0][7],
        clock_rebased=True,
        startup_hold_seconds=STARTUP_HOLD,
        startup_hold_verified=True,
        prior_database_rows_preserved=True,
        prior_files_preserved=True,
        diagnostic_after=report,
        financial_ledger_changed=False,
        period_baselines_changed=False,
        request_states_changed=False,
        automatic_retry=False,
        exchange_http_attempts=0,
        order_writes=0,
        installation_ready=False,
        execution_approved=False,
        orders_enabled=False,
        live_trading_enabled=False,
    )
