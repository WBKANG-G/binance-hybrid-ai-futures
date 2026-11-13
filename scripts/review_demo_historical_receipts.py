"""Non-mutating local receipt and event-state comparison; never sends or resolves requests."""

import fcntl
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from integrations.binance_usdm.demo_tp_admission import TPModelProfile
from packages.domain.contracts import OrderState, PositionState
from packages.persistence.demo import accounts, events
from packages.persistence.demo_read_recovery import _file, _private_directory
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
from services.execution.demo_models import DemoLedger
from services.execution.demo_runtime_capacity import capacity_plan
from services.execution.demo_runtime_storage import RuntimeClocks
from services.execution.readiness_closed_history import INCIDENT, RECEIPT, _matches

EXIT_SOURCE = "ddfe3b89af422864a5afcd6da8bcb65bc7a88e82"
CORRECTION = "CORRECT_CANCELLED_UNFILLED_EXPOSURE"
ACK = "ACKNOWLEDGE_FLAT_EMERGENCY"
REQUEST_STATES = {
    "ABSENT",
    "SUBMITTING",
    "UNKNOWN",
    "ACKNOWLEDGED",
    "REJECTED",
    "NOT_SENT",
    "FILLED",
    "VERIFIED",
    "CANCEL_REQUESTED",
    "NOT_FOUND_UNRESOLVED",
}
NO_SEND = {None, "LOCAL_VALIDATION_FAILED", "POSITION_ALREADY_FLAT", "POSITION_DIRECTION_MISMATCH"}
KINDS = {
    "DEMO_ENTRY_INTENT",
    "DEMO_ENTRY_EFFECT",
    "DEMO_SL_INTENT",
    "DEMO_SL_EFFECT",
    "DEMO_TP_INTENT",
    "DEMO_TP_EFFECT",
    "DEMO_REDUCE_ONLY_INTENT",
    "DEMO_REDUCE_ONLY_EFFECT",
    "DEMO_CANCEL_INTENT",
    "DEMO_CANCEL_EFFECT",
    "DEMO_CANCELLED_EXPOSURE_CORRECTED",
    "DEMO_OPERATOR_ACKNOWLEDGED_FLAT_EMERGENCY",
}


def ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def hex64(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def relations(ledger: Any, plan: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    manual_valid = False
    try:
        manual_valid = _matches(ledger, now)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        pass
    rows = []
    for item in plan:
        o = item["order"]
        correction_rows = []
        for key, receipt in sorted(ledger.cancelled_exposure_corrections.items()):
            for order_key, original in receipt.original_orders.items():
                if original.client_id != o.client_id:
                    continue
                before = original.model_dump(mode="json")
                delta_matches = (
                    o.model_copy(
                        update={
                            "open_quantity": original.open_quantity,
                            "actual_risk": original.actual_risk,
                            "position_state": original.position_state,
                        }
                    ).model_dump(mode="json")
                    == before
                    and not o.open_quantity
                    and not o.actual_risk
                    and o.position_state.value == "CLOSED"
                )
                original_shape = (
                    original.state.value == "CANCELED"
                    and not original.filled
                    and not original.remaining
                    and original.open_quantity == original.quantity
                    and original.position_state.value == "EXIT_PENDING"
                    and original.exit_state == "UNKNOWN"
                    and not original.protected
                )
                stamp = receipt.corrected_at
                shape = (
                    ledger.operations.get(key) == CORRECTION
                    and ledger.orders.get(order_key) is not None
                    and ledger.orders[order_key].client_id == o.client_id
                    and delta_matches
                    and original_shape
                    and all(
                        hex64(v)
                        for v in (
                            receipt.review_token,
                            receipt.evidence_hash,
                            receipt.exchange_fingerprint,
                        )
                    )
                    and stamp.tzinfo is not None
                    and stamp.utcoffset() is not None
                    and stamp.utcoffset().total_seconds() == 0
                    and stamp <= now
                )
                correction_rows.append(
                    dict(
                        receipt_reference_sha256=ref(key),
                        receipt_structure_matches_current_order=shape,
                        original_cancelled_zero_fill_shape=original_shape,
                        permitted_local_exposure_delta_matches=delta_matches,
                        operation_marker_matches=ledger.operations.get(key) == CORRECTION,
                        remote_proof_replayed=False,
                        request_resolution_authorized=False,
                    )
                )
        manual = []
        if o.signal.signal_id == UUID(INCIDENT) and ledger.orders.get(INCIDENT) is o:
            raw = ledger.operations.get(RECEIPT)
            manual.append(
                dict(
                    receipt_present=raw is not None,
                    receipt_reference_sha256=ref(RECEIPT),
                    receipt_sha256=ref(raw) if raw is not None else None,
                    installed_receipt_structure_recognized=manual_valid,
                    remote_proof_replayed=False,
                    request_resolution_authorized=False,
                )
            )
        rows.append(
            dict(
                request_reference_sha256=item["request_reference_sha256"],
                role=item["role"],
                request_state=item["state"],
                order_reference_sha256=ref(o.client_id),
                same_order_request_references=[
                    p["request_reference_sha256"]
                    for p in plan
                    if p is not item and p["order"].client_id == o.client_id
                ],
                local_order_state=o.state.value,
                local_position_state=o.position_state.value,
                local_exit_state=o.exit_state,
                quantity=str(o.quantity),
                filled=str(o.filled),
                remaining=str(o.remaining),
                open_quantity=str(o.open_quantity),
                local_no_send_evidence=ledger.request_no_send_evidence.get(item["identifier"]),
                local_fill_record_count=len(o.seen_fills),
                local_fill_records_are_independent_exchange_proof=False,
                cancelled_exposure_correction_receipts=correction_rows,
                manual_close_receipts=manual,
                remote_resolution_verified=False,
                automatically_resolvable=False,
            )
        )
    acknowledgements = [
        dict(
            receipt_reference_sha256=ref(key),
            token_format_valid=hex64(token),
            operation_marker_matches=ledger.operations.get(key) == ACK,
            request_specific=False,
            request_resolution_authorized=False,
        )
        for key, token in sorted(ledger.recovery_receipts.items())
    ]
    return dict(
        requests=rows,
        flat_emergency_acknowledgements=acknowledgements,
        request_resolution_authorized=False,
        independent_exchange_proof_replayed=False,
    )


def event_signature(payload: Any, item: Any, common: Any) -> dict[str, Any] | None:
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("requests"), dict)
        or not isinstance(payload.get("orders"), dict)
    ):
        return None
    identifier = item["identifier"]
    state = payload["requests"].get(identifier, "ABSENT")
    no_send = payload.get("request_no_send_evidence", {}).get(identifier)
    common.require(
        state in REQUEST_STATES and no_send in NO_SEND, "LOCAL_RECEIPT_EVENT_STATE_INVALID"
    )
    matches = [
        o
        for o in payload["orders"].values()
        if isinstance(o, dict) and o.get("client_id") == item["order"].client_id
    ]
    common.require(len(matches) <= 1, "LOCAL_RECEIPT_EVENT_ORDER_AMBIGUOUS")
    result = dict(request_state=state, no_send_evidence=no_send, order_present=bool(matches))
    if matches:
        o = matches[0]
        common.require(
            o.get("state") in {s.value for s in OrderState}
            and o.get("position_state") in {s.value for s in PositionState}
            and o.get("exit_state", "NONE")
            in {"NONE", "SUBMITTING", "UNKNOWN", "ACKNOWLEDGED", "FILLED"},
            "LOCAL_RECEIPT_EVENT_ORDER_INVALID",
        )
        result.update(
            local_order_state=o["state"],
            local_position_state=o["position_state"],
            local_exit_state=o.get("exit_state", "NONE"),
        )
        for name in ("filled", "remaining", "open_quantity"):
            value = o.get(name)
            common.require(
                isinstance(value, str) and len(value) <= 40, "LOCAL_RECEIPT_EVENT_NUMBER_INVALID"
            )
            assert isinstance(value, str)
            number = Decimal(value)
            common.require(number.is_finite() and number >= 0, "LOCAL_RECEIPT_EVENT_NUMBER_INVALID")
            result[name] = str(number)
    return result


def timelines(
    engine: Any, account: str, plan: Any, common: Any, *, state: Any = None
) -> dict[str, Any]:
    result: dict[str, list[dict[str, Any]]] = {p["request_reference_sha256"]: [] for p in plan}
    previous: dict[str, Any] = {}
    scanned = skipped = total = 0
    started = time.monotonic()
    state = {} if state is None else state
    diagnostic: dict[str, Any] = dict(
        query_strategy="SINGLE_READ_ONLY_STREAM",
        row_limit=4096,
        byte_limit=128 * 1024 * 1024,
        per_row_byte_limit=2 * 1024 * 1024,
        seconds_limit=60,
        events_scanned=0,
        event_bytes_scanned=0,
        elapsed_ms=0,
        phase="EVENT_METADATA",
    )
    state["local_receipt_scan"] = diagnostic

    def checkpoint() -> None:
        diagnostic.update(
            events_scanned=scanned,
            event_bytes_scanned=total,
            elapsed_ms=int(max(0, time.monotonic() - started) * 1000),
        )
        common.require(diagnostic["elapsed_ms"] < 60000, "LOCAL_RECEIPT_EVENT_TIME_LIMIT")

    try:
        with engine.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            c.execute(text("SET LOCAL statement_timeout='2s'"))
            checkpoint()
            count, size, largest = c.execute(
                select(
                    func.count(),
                    func.coalesce(func.sum(func.octet_length(events.c.payload)), 0),
                    func.coalesce(func.max(func.octet_length(events.c.payload)), 0),
                ).where(events.c.account_id == account)
            ).one()
            diagnostic.update(
                planned_event_rows=int(count),
                planned_event_bytes=int(size),
                max_event_row_bytes=int(largest),
            )
            common.require(count <= 4096, "LOCAL_RECEIPT_EVENT_ROW_COUNT_LIMIT")
            common.require(size <= 128 * 1024 * 1024, "LOCAL_RECEIPT_EVENT_BYTE_LIMIT")
            common.require(largest <= 2 * 1024 * 1024, "LOCAL_RECEIPT_EVENT_ROW_LIMIT")
            checkpoint()
            diagnostic["phase"] = "EVENT_ROWS"
            q = (
                select(events)
                .where(events.c.account_id == account)
                .order_by(events.c.as_of, events.c.event_id)
            )
            # A single server cursor avoids re-sorting the entire table for every event.
            with c.execution_options(stream_results=True, yield_per=32).execute(q) as cursor:
                for row in cursor.mappings():
                    checkpoint()
                    body = row["payload"]
                    row_size = len(body.encode())
                    scanned += 1
                    total += row_size
                    diagnostic.update(
                        events_scanned=scanned,
                        event_bytes_scanned=total,
                        last_event_reference_sha256=ref(row["event_id"]),
                    )
                    common.require(scanned <= 4096, "LOCAL_RECEIPT_EVENT_ROW_COUNT_LIMIT")
                    common.require(total <= 128 * 1024 * 1024, "LOCAL_RECEIPT_EVENT_BYTE_LIMIT")
                    common.require(row_size <= 2 * 1024 * 1024, "LOCAL_RECEIPT_EVENT_ROW_LIMIT")
                    payload = json.loads(body)
                    if (
                        not isinstance(payload, dict)
                        or not isinstance(payload.get("requests"), dict)
                        or not isinstance(payload.get("orders"), dict)
                    ):
                        skipped += 1
                        continue
                    for item in plan:
                        signature = event_signature(payload, item, common)
                        key = item["request_reference_sha256"]
                        if signature == previous.get(key):
                            continue
                        previous[key] = signature
                        common.require(len(result[key]) < 128, "LOCAL_RECEIPT_TIMELINE_LIMIT")
                        result[key].append(
                            dict(
                                event_reference_sha256=ref(row["event_id"]),
                                as_of_utc=row["as_of"].astimezone(UTC).isoformat(),
                                event_kind=row["kind"]
                                if row["kind"] in KINDS
                                else "OTHER_STORED_EVENT",
                                local_fields=signature,
                                remote_proof_verified=False,
                            )
                        )
            checkpoint()
            common.require(
                scanned == count and total == size, "LOCAL_RECEIPT_EVENT_SCAN_INCOMPLETE"
            )
            diagnostic["phase"] = "COMPLETE"
    except SQLAlchemyError as error:
        if getattr(getattr(error, "orig", None), "sqlstate", None) == "57014":
            common.require(False, "LOCAL_RECEIPT_EVENT_DATABASE_TIMEOUT")
        raise
    return dict(
        events_scanned=scanned,
        event_bytes_scanned=total,
        non_ledger_payloads_skipped=skipped,
        request_local_state_timelines=result,
        query_strategy="SINGLE_READ_ONLY_STREAM",
        event_metadata_counts_verified=True,
        remote_proof_replayed=False,
    )


def run(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    settings: Any,
    fingerprint: Any,
    adoption: Any,
    gate: Any,
    history: Any,
    common: Any,
    evidence: Any,
    *,
    clocks: Any = None,
    state: Any = None,
) -> dict[str, Any]:
    clock = clocks or RuntimeClocks()
    state = {} if state is None else state
    state["stage"] = "LOCAL_RECEIPT_PRIOR_EXIT_STATUS"

    def forbidden() -> Any:
        common.require(False, "LOCAL_RECEIPT_CREDENTIAL_ACCESS_FORBIDDEN")

    status = history.run(
        engine,
        directory,
        scope,
        journal,
        installation,
        account,
        settings,
        forbidden,
        fingerprint,
        adoption,
        gate,
        common,
        evidence,
        status_only=True,
        exit_followup=True,
        clocks=clock,
        source_reference=EXIT_SOURCE,
    )
    common.require(
        status["shared_total_weight"] == 284
        and status["requests_observed"] == 1
        and status["requests_unqueried"] == 0
        and status["stopped_after_not_found"] is True
        and status["observations"][0]["role"] == "EXIT"
        and status["observations"][0]["observed_status"] == "NOT_FOUND_UNRESOLVED",
        "LOCAL_RECEIPT_REVIEWED_EXIT_RESULT_REQUIRED",
    )
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            common.require(False, "LOCAL_RECEIPT_MAINTENANCE_BUSY")
        anchor = SharedWeightAnchorStore(engine)
        with history.raw_read(directory, scope, journal, anchor) as (_, _, path):
            active = hashlib.sha256(_file(path)).hexdigest()
        sentinel = directory.parent / "local-receipt-review-unused"
        common.require(
            not sentinel.exists() and not sentinel.is_symlink(), "LOCAL_RECEIPT_UNEXPECTED_PATH"
        )
        before = common.protected_snapshot(engine, directory.parent, path, sentinel, fingerprint)
        common.require(
            common.protected_snapshot(
                engine,
                directory.parent,
                path,
                history.paths(directory, journal, exit_followup=True)[1],
                fingerprint,
            )
            == status["protected_snapshot_sha256"],
            "LOCAL_RECEIPT_STATE_CHANGED_AFTER_STATUS",
        )
        resources = common.resource_rows(engine, scope)
        with engine.connect() as c, c.begin():
            c.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            ledger = DemoLedger.model_validate(
                c.execute(
                    select(accounts.c.state).where(accounts.c.account_id == account)
                ).scalar_one()
            )
        plan = history.plan_requests(ledger, common)
        inventory = relations(ledger, plan, clock.wall_clock())
        state["stage"] = "LOCAL_RECEIPT_EVENT_SCAN"
        timeline = timelines(engine, account, plan, common, state=state)
        state["stage"] = "LOCAL_RECEIPT_PRESERVATION_POSTCHECK"
        common.require(
            before
            == common.protected_snapshot(engine, directory.parent, path, sentinel, fingerprint)
            and resources == common.resource_rows(engine, scope)
            and active == hashlib.sha256(_file(path)).hexdigest(),
            "LOCAL_RECEIPT_STATE_CHANGED_DURING_REVIEW",
        )
        return dict(
            local_historical_receipt_review="COMPLETED_KEEP_PAUSED",
            review_scope="LOCAL_RECEIPT_STRUCTURE_AND_STORED_EVENT_FIELDS_ONLY",
            previous_exit_evidence_verified=True,
            local_receipt_relations=inventory,
            local_event_history=timeline,
            capacity_assumptions=capacity_plan(
                TPModelProfile(historical_orders=len(ledger.orders))
            ),
            protected_snapshot_sha256=before,
            all_reviewed_state_unchanged=True,
            request_states_unchanged=True,
            reconciliation_performed=False,
            independent_remote_resolution_verified=False,
            financial_database_changed=False,
            filesystem_changed=False,
            journal_changed=False,
            leader_acquired=False,
            credential_session_accessed=False,
            http_attempts=0,
            modeled_weight_charged=0,
            order_writes=0,
            paused=True,
            execution_approved=False,
            orders_enabled=False,
            live_trading_enabled=False,
            capacity_shortfall_resolved=False,
            trading_readiness_checked=False,
            next_action="REVIEW_LOCAL_RECEIPT_RELATIONS_AND_MISSING_REMOTE_PROOF_KEEP_PAUSED",
        )
    finally:
        os.close(fd)
