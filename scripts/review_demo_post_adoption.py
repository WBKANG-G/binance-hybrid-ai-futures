"""Local post-adoption integrity and runtime gates; no credentials, sends, repair or files."""

import fcntl
import hashlib
import inspect as pyinspect
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text

from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from integrations.binance_usdm.demo_tp_weight_model import MODELED_TP_ROUTE_COSTS
from packages.persistence.demo import accounts
from packages.persistence.demo_order_quota import LocalQuotaStopped
from packages.persistence.demo_read_audit import ReadAuditStore
from packages.persistence.demo_read_recovery import (
    _clock,
    _file,
    _private_directory,
    _valid,
    verify_recovery_backups,
)
from packages.persistence.demo_shared_checkpoint import CheckpointContext, SharedWeightAnchorStore
from packages.persistence.demo_shared_weight import STARTUP_HOLD, SharedWeightJournal
from services.execution.demo_managed_runtime import assemble_managed_runtime
from services.execution.demo_models import DemoLedger
from services.execution.demo_quota_startup_review import UNRESOLVED, assess_snapshot
from services.execution.demo_read_audit_bootstrap import _validate_schema
from services.execution.demo_runtime_capacity import capacity_plan
from services.execution.demo_runtime_cost_contract import require_runtime_cost_contract
from services.execution.demo_runtime_storage import RuntimeClocks
from services.execution.demo_unused_read_clock import verify_backups

POLICY = "POST_ADOPTION_LOCAL_READ_ONLY_GATE_V1"


def cost_contract(evidence: Any, common: Any) -> dict[str, Any]:
    common.require(
        isinstance(evidence, dict)
        and evidence.get("sdk_commit") == "f75b4a52c5aebf52b1c6ad671b0ce53b96f8e179",
        "GATE_COST_EVIDENCE_INVALID",
    )
    require_runtime_cost_contract(MODELED_TP_ROUTE_COSTS)
    rows = evidence.get("routes")
    common.require(
        isinstance(rows, list) and len(rows) == len(MODELED_TP_ROUTE_COSTS) == 21,
        "GATE_COST_EVIDENCE_INVALID",
    )
    seen = set()
    for row in rows:
        key = (row["method"], row["path"])
        common.require(
            key not in seen and key in MODELED_TP_ROUTE_COSTS, "GATE_COST_EVIDENCE_ROUTE_INVALID"
        )
        seen.add(key)
        common.require(
            type(row.get("modeled_weight")) is int
            and row["modeled_weight"] == MODELED_TP_ROUTE_COSTS[key]
            and type(row.get("documented_ip_weight_for_approved_shape")) is int
            and 0 <= row["documented_ip_weight_for_approved_shape"] <= row["modeled_weight"],
            "GATE_COST_EVIDENCE_WEIGHT_INVALID",
        )
        common.require(
            isinstance(row.get("weight_excerpt"), str) and bool(row["weight_excerpt"]),
            "GATE_COST_EVIDENCE_INVALID",
        )
    return dict(
        pinned_documented_cost_contract_verified=True,
        documented_routes=21,
        sdk_commit=evidence["sdk_commit"],
        live_venue_cost_upper_bounds_verified=False,
    )


def deny_default(common: Any) -> bool:
    signature = pyinspect.signature(assemble_managed_runtime)
    common.require(
        signature.parameters["allow_synthetic_model_execution"].default is False,
        "GATE_EXECUTION_DEFAULT_CHANGED",
    )
    try:
        invoke: Any = assemble_managed_runtime
        invoke(None, None, None, None, None)
    except LocalQuotaStopped as error:
        common.require(
            str(error) == "RUNTIME_REAL_EXECUTION_DISABLED", "GATE_EXECUTION_DEFAULT_CHANGED"
        )
        return True
    common.require(False, "GATE_EXECUTION_DEFAULT_CHANGED")
    return False


def raw_audit(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    clock: Any,
    common: Any,
) -> tuple[dict[str, Any], Path]:
    anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
    common.require(anchor.installation_id() == installation, "PROBE_INSTALLATION_MISMATCH")
    obj = object.__new__(SharedWeightJournal)
    obj.directory = directory
    obj.scope = scope
    obj._paths()
    dbid, lockid = obj._identity(obj.path), obj._identity(obj.lock_path)
    original = hashlib.sha256(_file(obj.path)).hexdigest()
    with anchor.scope(scope, journal, read_only=True) as cp:
        assert isinstance(cp, CheckpointContext)
        _validate_schema(cp.connection)
        fd = os.open(obj.lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        local = None
        try:
            common.require(
                (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == lockid, "GATE_SHARED_LOCK_REPLACED"
            )
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                common.require(False, "GATE_SHARED_WRITER_BUSY")
            common.require(
                obj._identity(obj.path) == dbid and obj._identity(obj.lock_path) == lockid,
                "GATE_SHARED_FILE_REPLACED",
            )
            common.require(
                not any(Path(str(obj.path) + s).exists() for s in ("-wal", "-shm", "-journal")),
                "GATE_SHARED_STORAGE_BUSY",
            )
            local = sqlite3.connect(
                obj.path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2
            )
            local.row_factory = sqlite3.Row
            local.execute("PRAGMA query_only=ON")
            local.execute("BEGIN")
            rows, snapshot = audit._verified_history(cp, local)
            verify_recovery_backups(directory, rows)
            verify_backups(directory, cp, installation)
            meta = snapshot[1][0]
            common.require(
                meta[7:] == [268, 0] and all(r[4] is not None for r in snapshot[2]),
                "GATE_AUDIT_ACTIVITY_INCOMPLETE_OR_STOPPED",
            )
            sample = _clock(clock)
            return dict(
                meta=list(meta),
                sample=sample,
                audit_events=len(rows),
                shared_total_weight=meta[7],
                active_file_sha256=original,
            ), obj.path
        finally:
            if local is not None:
                local.close()
            os.close(fd)


def historical_diagnostics(ledger: DemoLedger) -> dict[str, Any]:
    """Opaque stable references only; local closure never resolves remote uncertainty."""

    def reference(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    rows = []
    for identifier, state in sorted(ledger.requests.items()):
        if state not in UNRESOLVED:
            continue
        matches = []
        for order in ledger.orders.values():
            for role, attribute in (
                ("ENTRY", "client_id"),
                ("STOP_LOSS", "algo_id"),
                ("TAKE_PROFIT", "tp_algo_id"),
                ("EXIT", "exit_id"),
            ):
                if getattr(order, attribute) == identifier:
                    matches.append(
                        dict(
                            order_reference_sha256=reference(order.client_id),
                            role=role,
                            local_order_state=order.state.value,
                            local_position_state=order.position_state.value,
                            local_exposure_zero=(not order.remaining and not order.open_quantity),
                        )
                    )
        rows.append(
            dict(
                request_reference_sha256=reference(identifier),
                state=state,
                matched_local_orders=matches,
                local_no_send_evidence=ledger.request_no_send_evidence.get(identifier),
                remote_resolution_verified=False,
                automatically_resolvable=False,
            )
        )
    profile = TPModelProfile(historical_orders=len(ledger.orders))
    plan = capacity_plan(profile)
    proof_snapshots = (6 + profile.historical_orders) * profile.snapshot
    proof_queries = 80 * (profile.historical_orders + 1)
    return dict(
        unresolved_requests=rows,
        unresolved_request_count=len(rows),
        unresolved_state_counts=dict(sorted(Counter(r["state"] for r in rows).items())),
        raw_request_identifiers_printed=False,
        historical_orders_retained=len(ledger.orders),
        historical_rows_omitted=0,
        model_assumptions=profile.report(),
        proof_components=dict(
            snapshot_count=6 + profile.historical_orders,
            snapshot_weight=profile.snapshot,
            snapshots_weight=proof_snapshots,
            original_id_query_weight=proof_queries,
            fixed_weight=38,
            reserve=plan["proof_reserve"],
        ),
        entry_components=dict(
            snapshot_count=8,
            snapshot_weight=profile.snapshot,
            snapshots_weight=8 * profile.snapshot,
            fixed_weight=400,
            reserve=plan["entry_required"] - profile.entry,
        ),
        proof_capacity_deficit=max(0, -plan["proof_slack"]),
        entry_capacity_deficit=max(0, plan["entry_required"] - plan["effective_ceiling"]),
        capacity_fits_unchanged_model=bool(
            plan["proof_fits_empty_window"] and plan["entry_fits_empty_window"]
        ),
        history_pruning_permitted=False,
        budget_increase_permitted=False,
        reconciliation_performed=False,
        exchange_checked=False,
        execution_approved=False,
    )


def run(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    fingerprint: Any,
    adoption: Any,
    common: Any,
    evidence: Any,
    *,
    clocks: Any = None,
) -> dict[str, Any]:
    from services.execution.demo_runtime_read_transport import AccountedDemoReadTransport

    AccountedDemoReadTransport._context()
    clock = clocks or RuntimeClocks()
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            common.require(False, "GATE_MAINTENANCE_BUSY")
        sentinel = directory.parent / ("post-adoption-gate-unused-" + str(uuid5(journal, POLICY)))
        common.require(
            not sentinel.exists() and not sentinel.is_symlink(), "GATE_UNEXPECTED_EXCLUSION_PATH"
        )
        raw, path = raw_audit(engine, directory, scope, journal, installation, clock, common)
        before = common.protected_snapshot(engine, directory.parent, path, sentinel, fingerprint)
        resources = common.resource_rows(engine, scope)
        intent = adoption.financial_adoption_witness(
            engine, directory, scope, journal, installation, account, clock, common
        )
        common.require(
            raw["audit_events"] == intent["first_audit_sequence"] + 16,
            "GATE_APPLICATION_AUDIT_COUNT_CHANGED",
        )
        adoption.check_resource_delta(engine, scope, intent, common)
        operation, own = adoption.paths(directory, journal)
        common.require(
            adoption.file_witness(directory.parent, path, own, common)
            == intent["file_witness_sha256"],
            "GATE_PRIOR_FILES_CHANGED",
        )
        with engine.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            row = (
                c.execute(select(accounts).where(accounts.c.account_id == account)).mappings().one()
            )
            ledger = DemoLedger.model_validate(row["state"])
            quota = assess_snapshot(
                ledger,
                account,
                directory.parent / "order-quota",
                now=clock.wall_clock(),
                lease_until=row["lease_until"],
            )
        diagnostics = historical_diagnostics(ledger)
        costs = cost_contract(evidence, common)
        default_blocked = deny_default(common)
        feasibility = capacity_plan(TPModelProfile(historical_orders=len(ledger.orders)))
        common.require(
            before
            == common.protected_snapshot(engine, directory.parent, path, sentinel, fingerprint),
            "GATE_FINANCIAL_OR_STORAGE_CHANGED_DURING_REVIEW",
        )
        common.require(
            resources == common.resource_rows(engine, scope)
            and raw["active_file_sha256"] == hashlib.sha256(_file(path)).hexdigest(),
            "GATE_RESOURCE_CHANGED_DURING_REVIEW",
        )
        final = _clock(clock)
        clock_valid = _valid(raw["meta"], raw["sample"]) and _valid(raw["meta"], final)
        ready = bool(clock_valid and final[0] - raw["meta"][4] >= STARTUP_HOLD)
        reason = (
            "NONE"
            if clock_valid
            else "BOOT_CHANGED"
            if raw["meta"][3] != final[2]
            else "MONOTONIC_BACKWARDS"
            if final[0] < raw["meta"][6]
            else "WALL_MONOTONIC_DRIFT"
        )
        blockers = list(quota["blockers"])
        if not clock_valid:
            blockers.append("SHARED_CLOCK_OR_BOOT_CHANGED")
        elif not ready:
            blockers.append("SHARED_STARTUP_HOLD")
        if quota.get("historical_unresolved_request_count", 0):
            blockers.append("HISTORICAL_UNRESOLVED_REQUESTS_REQUIRE_RECONCILIATION")
        if not feasibility["proof_fits_empty_window"]:
            blockers.append("RUNTIME_PROOF_BATCH_CANNOT_FIT")
        if not feasibility["entry_fits_empty_window"]:
            blockers.append("RUNTIME_ENTRY_BATCH_CANNOT_FIT")
        external = [
            "REMOTE_ACCOUNT_IDENTITY_NOT_BOUND",
            "FRESH_EXCHANGE_STATE_NOT_CHECKED_BY_LOCAL_GATE",
            "EXTERNAL_ACCOUNT_ORDER_USAGE_UNKNOWN",
            "ORDER_USAGE_ENDPOINT_NOT_IN_ACCOUNTED_ROUTE_SET",
            "LIVE_VENUE_COST_UPPER_BOUNDS_NOT_PROVEN",
            "SHARED_IP_CAPACITY_NOT_RESERVED",
            "DEFAULT_EXECUTION_DISABLED",
        ]
        endpoint_supported = ("GET", "/fapi/v1/rateLimit/order") in MODELED_TP_ROUTE_COSTS
        if endpoint_supported:
            external.remove("ORDER_USAGE_ENDPOINT_NOT_IN_ACCOUNTED_ROUTE_SET")
        return dict(
            post_adoption_local_gate="REVIEW_COMPLETED_KEEP_PAUSED",
            review_scope="LOCAL_INTEGRITY_AND_STATIC_RUNTIME_CONTRACT_ONLY",
            local_runtime_review_checked=True,
            day13_adoption_integrity_verified=True,
            prior_financial_history_preserved=True,
            prior_receipts_and_files_preserved=True,
            shared_audit_integrity_verified=True,
            shared_cumulative_modeled_weight=raw["shared_total_weight"],
            shared_audit_events=raw["audit_events"],
            shared_clock_valid=clock_valid,
            shared_clock_reason=reason,
            shared_startup_ready=ready,
            local_storage_checks_passed=quota.get("local_checks_passed", False),
            local_blockers=sorted(set(blockers)),
            remaining_execution_blockers=external,
            managed_quota_startup=quota,
            pinned_cost_contract=costs,
            capacity_assumptions=feasibility,
            historical_blocker_diagnostics=diagnostics,
            default_execution_denial_verified=default_blocked,
            accounted_order_usage_endpoint_supported=endpoint_supported,
            protected_snapshot_sha256=before,
            all_reviewed_state_unchanged=True,
            credential_session_accessed=False,
            exchange_checked=False,
            exchange_account_identity_verified=False,
            account_order_usage_verified=False,
            actual_ip_capacity_reserved=False,
            request_cost_upper_bounds_proven=False,
            session_started=False,
            storage_created=False,
            leader_acquired=False,
            database_changed=False,
            journal_changed=False,
            filesystem_changed=False,
            http_attempts=0,
            modeled_weight_charged=0,
            order_writes=0,
            paused=True,
            orders_enabled=False,
            live_trading_enabled=False,
            execution_approved=False,
            trading_readiness_checked=False,
            next_action="REVIEW_HISTORICAL_REQUEST_EVIDENCE_AND_CAPACITY_KEEP_PAUSED",
        )
    finally:
        os.close(fd)
