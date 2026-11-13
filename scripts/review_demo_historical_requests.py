"""One-shot original-ID DEMO evidence. Never reconciles, mutates orders or retries."""

import fcntl
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text

from integrations.binance_usdm.demo import DemoClient, DemoCredentials
from packages.persistence.demo import accounts
from packages.persistence.demo_read_audit import AuditedDemoReadJournal, ReadAuditStore
from packages.persistence.demo_read_recovery import (
    _file,
    _private_directory,
    _sync_directory,
    _write_new,
    verify_recovery_backups,
)
from packages.persistence.demo_shared_checkpoint import SharedWeightAnchorStore
from packages.persistence.demo_shared_weight import SharedWeightJournal
from services.execution.demo_models import DemoLedger
from services.execution.demo_query_contract import require_signed_query
from services.execution.demo_quota_startup_review import UNRESOLVED
from services.execution.demo_read_audit_bootstrap import validate_account_row
from services.execution.demo_runtime_read_transport import (
    AccountedDemoReadTransport,
    _demo_get_http,
)
from services.execution.demo_runtime_storage import RuntimeClocks
from services.execution.demo_unused_read_clock import verify_backups

POLICY = "HISTORICAL_ORIGINAL_ID_EVIDENCE_20261113_V1"


def ref(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def paths(directory: Path, journal: UUID) -> tuple[str, Path]:
    operation = str(uuid5(journal, POLICY))
    return operation, directory.parent / ("historical-request-evidence-" + operation)


def plan_requests(ledger: DemoLedger, common: Any) -> list[dict[str, Any]]:
    common.require(len(ledger.orders) == 34, "HISTORY_REVIEWED_34_ORDERS_REQUIRED")
    result = []
    for identifier, state in ledger.requests.items():
        if state not in UNRESOLVED:
            continue
        matches = [
            (o, role)
            for o in ledger.orders.values()
            for role, field in (
                ("ENTRY", "client_id"),
                ("STOP_LOSS", "algo_id"),
                ("EXIT", "exit_id"),
            )
            if getattr(o, field) == identifier
        ]
        common.require(len(matches) == 1, "HISTORY_UNIQUE_LOCAL_REQUEST_BINDING_REQUIRED")
        order, role = matches[0]
        common.require(
            order.signal.symbol == "BTCUSDT"
            and order.signal.environment == "DEMO"
            and order.position_state.value == "CLOSED"
            and not order.remaining
            and not order.open_quantity,
            "HISTORY_LOCAL_CLOSED_BTC_REQUIRED",
        )
        path = "/fapi/v1/algoOrder" if role == "STOP_LOSS" else "/fapi/v1/order"
        params = (
            {"clientAlgoId": identifier}
            if role == "STOP_LOSS"
            else {"symbol": "BTCUSDT", "origClientOrderId": identifier}
        )
        require_signed_query("GET", path, params)
        result.append(
            dict(
                identifier=identifier,
                state=state,
                role=role,
                order=order,
                path=path,
                params=params,
                expected_algo_server_id=ledger.algo_server_ids.get(identifier),
                request_reference_sha256=ref(identifier),
            )
        )
    common.require(
        len(result) == 4
        and Counter(r["role"] for r in result) == {"ENTRY": 2, "STOP_LOSS": 1, "EXIT": 1}
        and Counter(r["state"] for r in result) == {"UNKNOWN": 3, "SUBMITTING": 1},
        "HISTORY_REVIEWED_FOUR_REQUESTS_REQUIRED",
    )
    return sorted(
        result,
        key=lambda r: (
            {"ENTRY": 0, "STOP_LOSS": 1, "EXIT": 2}[r["role"]],
            r["request_reference_sha256"],
        ),
    )


def public_plan(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in ("request_reference_sha256", "state", "role", "path")} for r in plan]


def observation(item: dict[str, Any], payload: Any, common: Any) -> dict[str, Any]:
    """Strict identity/shape checks; observation never authorizes history pruning."""
    o, role = item["order"], item["role"]
    common.require(isinstance(payload, dict), "HISTORY_RESPONSE_OBJECT_REQUIRED")
    common.require(
        payload.get("symbol") == "BTCUSDT" and payload.get("positionSide") == "BOTH",
        "HISTORY_RESPONSE_SCOPE_MISMATCH",
    )
    side = "BUY" if o.signal.direction == "LONG" else "SELL"
    if role != "ENTRY":
        side = "SELL" if side == "BUY" else "BUY"
    common.require(payload.get("side") == side, "HISTORY_RESPONSE_SIDE_MISMATCH")

    def number(name: str) -> Decimal:
        value = payload.get(name)
        common.require(
            isinstance(value, str) and 1 <= len(value) <= 40, "HISTORY_RESPONSE_NUMBER_INVALID"
        )
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError, TypeError):
            common.require(False, "HISTORY_RESPONSE_NUMBER_INVALID")
            raise
        common.require(result.is_finite() and result >= 0, "HISTORY_RESPONSE_NUMBER_INVALID")
        return result

    if role == "STOP_LOSS":
        common.require(
            payload.get("clientAlgoId") == item["identifier"]
            and payload.get("algoType") == "CONDITIONAL"
            and payload.get("orderType") == "STOP_MARKET"
            and str(payload.get("closePosition")).lower() == "true"
            and payload.get("workingType") == "MARK_PRICE"
            and str(payload.get("priceProtect")).lower() == "false"
            and number("triggerPrice") == o.signal.initial_stop_price,
            "HISTORY_ALGO_IDENTITY_MISMATCH",
        )
        status = payload.get("algoStatus")
        common.require(
            status
            in {"NEW", "TRIGGERING", "TRIGGERED", "FINISHED", "CANCELED", "REJECTED", "EXPIRED"},
            "HISTORY_RESPONSE_STATUS_INVALID",
        )
        server = payload.get("algoId")
        if item.get("expected_algo_server_id") is not None:
            common.require(
                str(server) == item["expected_algo_server_id"], "HISTORY_SERVER_ID_MISMATCH"
            )
    else:
        common.require(
            payload.get("clientOrderId") == item["identifier"]
            and payload.get("type") == ("LIMIT" if role == "ENTRY" else "MARKET")
            and str(payload.get("reduceOnly")).lower() == ("false" if role == "ENTRY" else "true"),
            "HISTORY_ORDER_IDENTITY_MISMATCH",
        )
        qty, filled = number("origQty"), number("executedQty")
        common.require(qty > 0 and filled <= qty, "HISTORY_RESPONSE_QUANTITY_MISMATCH")
        if role == "ENTRY":
            common.require(
                qty == o.quantity
                and filled >= o.filled
                and number("price") == o.signal.suggested_limit_price,
                "HISTORY_ENTRY_TERMS_MISMATCH",
            )
        status = payload.get("status")
        common.require(
            status
            in {
                "NEW",
                "PARTIALLY_FILLED",
                "FILLED",
                "CANCELED",
                "REJECTED",
                "EXPIRED",
                "EXPIRED_IN_MATCH",
            },
            "HISTORY_RESPONSE_STATUS_INVALID",
        )
        server = payload.get("orderId")
        if role == "ENTRY" and o.server_order_id is not None:
            common.require(str(server) == o.server_order_id, "HISTORY_SERVER_ID_MISMATCH")
    common.require(
        type(server) in {int, str}
        and str(server).isascii()
        and str(server).isdecimal()
        and 0 < len(str(server)) <= 20
        and int(server) > 0,
        "HISTORY_SERVER_ID_INVALID",
    )
    return dict(
        request_reference_sha256=item["request_reference_sha256"],
        role=role,
        response_identity_fields_match=True,
        observed_status=status,
        remote_resolution_verified=False,
        automatically_resolvable=False,
        response_body_bound_to_audit_chain=False,
    )


@contextmanager
def raw_read(directory: Path, scope: str, journal: UUID, anchor: Any) -> Iterator[Any]:
    obj = object.__new__(SharedWeightJournal)
    obj.directory, obj.scope = directory, scope
    obj._paths()
    dbid, lockid = obj._identity(obj.path), obj._identity(obj.lock_path)
    from packages.persistence.demo_read_audit import require

    with anchor.scope(scope, journal, read_only=True) as cp:
        fd = os.open(obj.lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        local = None
        try:
            require((os.fstat(fd).st_dev, os.fstat(fd).st_ino) == lockid, "HISTORY_LOCK_REPLACED")
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                require(False, "HISTORY_SHARED_WRITER_BUSY")
            require(
                obj._identity(obj.path) == dbid and obj._identity(obj.lock_path) == lockid,
                "HISTORY_SHARED_FILE_REPLACED",
            )
            require(
                not any(Path(str(obj.path) + s).exists() for s in ("-wal", "-shm", "-journal")),
                "HISTORY_SQLITE_BUSY",
            )
            local = sqlite3.connect(
                obj.path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=2
            )
            local.row_factory = sqlite3.Row
            local.execute("PRAGMA query_only=ON")
            local.execute("BEGIN")
            yield cp, local, obj.path
        finally:
            if local is not None:
                local.close()
            os.close(fd)


def audit_delta(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    intent: Any,
    common: Any,
) -> dict[str, Any]:
    """Replay immutable audit history even if the wall/monotonic clock changed."""
    anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
    common.require(anchor.installation_id() == installation, "HISTORY_INSTALLATION_CHANGED")
    with raw_read(directory, scope, journal, anchor) as (cp, local, path):
        from services.execution.demo_read_audit_bootstrap import _validate_schema

        _validate_schema(cp.connection)
        rows, snapshot = audit._verified_history(cp, local)
        verify_recovery_backups(directory, rows)
        verify_backups(directory, cp, installation)
        common.require(
            all(r[4] is not None for r in snapshot[2]), "HISTORY_PENDING_READ_REQUIRES_REVIEW"
        )
        meta = snapshot[1][0]
        common.require(meta[8] == 0, "HISTORY_SHARED_STORE_STOPPED")
    resources, actions = common.resource_rows(engine, scope)
    prior = {tuple(k): v for k, v in intent["prior_resources"]}
    common.require(
        all(resources.get(k) == v for k, v in prior.items()), "HISTORY_PRIOR_RESOURCE_CHANGED"
    )
    first = intent["first_sequence"]
    new = [actions[i] for i in range(first, len(actions))]
    common.require(0 <= len(new) <= 12 and len(new) % 2 == 0, "HISTORY_AUDIT_DELTA_INVALID")
    routes = [("/fapi/v1/time", 1), ("/fapi/v1/accountConfig", 5)] + [
        (r["path"], 1) for r in intent["plan"]
    ]
    count = len(new) // 2
    for i in range(count):
        for phase, kind in enumerate(("RESERVE", "FINISH")):
            action = new[2 * i + phase]
            common.require(
                action["kind"] == kind and (action["path"], action["cost"]) == routes[i],
                "HISTORY_AUDIT_ROUTE_MISMATCH",
            )
            if phase:
                common.require(action["sent"] is True, "HISTORY_AUDIT_SEND_UNCONFIRMED")
                common.require(
                    action["status"] == 200
                    or (i == count - 1 and i >= 2 and action["status"] == 400),
                    "HISTORY_AUDIT_HTTP_FAILURE",
                )
    old_revision = max(
        k[2] for k in prior if k[0] == "demo_shared_weight_checkpoints" and k[1] == scope
    )
    expected = {
        ("demo_shared_weight_checkpoints", scope, old_revision + i) for i in range(1, len(new) + 1)
    } | {("demo_read_audit_events", scope, first + i) for i in range(len(new))}
    common.require(
        set(resources) - set(prior) == expected
        and len(rows) == first + len(new)
        and meta[7] == 268 + sum(c for _, c in routes[:count]),
        "HISTORY_RESOURCE_DELTA_INVALID",
    )
    return dict(
        attempts=count,
        last_http_status=new[-1]["status"] if new else None,
        audit_events_added=len(new),
        resource_checkpoints_added=len(new),
        modeled_weight_charged=sum(c for _, c in routes[:count]),
        shared_total_weight=meta[7],
        prior_resource_rows_preserved=True,
        audit_history_verified=True,
    )


def run(
    engine: Any,
    directory: Path,
    scope: str,
    journal: UUID,
    installation: UUID,
    account: str,
    settings: Any,
    credential_loader: Any,
    fingerprint: Any,
    adoption: Any,
    gate: Any,
    common: Any,
    evidence: Any,
    *,
    status_only: bool = False,
    clocks: Any = None,
    sender: Any = None,
    source_reference: str = "",
    state: Any = None,
) -> dict[str, Any]:
    clock = clocks or RuntimeClocks()
    state = {} if state is None else state
    operation, folder = paths(directory, journal)
    AccountedDemoReadTransport._context()
    _private_directory(directory.parent)
    fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    transport = None
    try:
        state["stage"] = "HISTORICAL_READ_ONLY_STATUS" if status_only else "HISTORICAL_PREFLIGHT"
        if not status_only:
            state["intent_created"] = folder.exists() or folder.is_symlink()
            common.require(
                not folder.exists() and not folder.is_symlink(),
                "HISTORY_ALREADY_ATTEMPTED_RUN_STATUS_ONLY",
            )
            # Gate owns a shared parent lock; release it before exclusive reader serialization.
            preflight = gate.run(
                engine,
                directory,
                scope,
                journal,
                installation,
                account,
                fingerprint,
                adoption,
                common,
                evidence,
                clocks=clock,
            )
            common.require(
                preflight["shared_clock_valid"] and preflight["shared_startup_ready"],
                "HISTORY_CLOCK_REVIEW_REQUIRED_NO_AUTOMATIC_REPAIR",
            )
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if status_only else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            common.require(False, "HISTORY_REVIEW_ALREADY_RUNNING")
        anchor, audit = SharedWeightAnchorStore(engine), ReadAuditStore(engine)
        if status_only:
            _private_directory(folder)
            state["intent_created"] = True
            common.require(
                {p.name for p in folder.iterdir()} <= {"intent.json", "complete.json"},
                "HISTORY_UNEXPECTED_RECEIPT_FILE",
            )
            intent = json.loads(_file(folder / "intent.json", 1048576))
            common.require(
                intent["policy"] == POLICY
                and intent["operation_id"] == operation
                and intent["account"] == account
                and intent["scope"] == scope
                and intent["journal"] == str(journal)
                and intent["installation"] == str(installation)
                and intent["source_reference"] == source_reference,
                "HISTORY_INTENT_BINDING_INVALID",
            )
            complete = folder / "complete.json"
            common.require(complete.exists(), "HISTORY_INCOMPLETE_ATTEMPT_NO_RETRY")
            result = json.loads(_file(complete, 1048576))
            delta = audit_delta(engine, directory, scope, journal, installation, intent, common)
            common.require(
                all(result[k] == v for k, v in delta.items()), "HISTORY_SAVED_AUDIT_RESULT_CHANGED"
            )
            with raw_read(directory, scope, journal, anchor) as (_, _, path):
                current = common.protected_snapshot(
                    engine, directory.parent, path, folder, fingerprint
                )
            common.require(
                current
                == intent["protected_snapshot_sha256"]
                == result["protected_snapshot_sha256"],
                "HISTORY_PROTECTED_STATE_CHANGED",
            )
            common.require(
                result["policy"] == POLICY
                and result["operation_id"] == operation
                and result["orders_enabled"] is False
                and result["execution_approved"] is False
                and result["live_trading_enabled"] is False,
                "HISTORY_SAVED_RESULT_INVALID",
            )
            common.require(
                all(
                    result.get(k) is False
                    for k in (
                        "reconciliation_performed",
                        "financial_database_changed",
                        "full_history_proof_replaced",
                        "capacity_shortfall_resolved",
                        "exchange_account_identity_verified",
                        "account_order_usage_verified",
                        "request_cost_upper_bounds_proven",
                        "actual_ip_capacity_reserved",
                        "trading_readiness_checked",
                    )
                ),
                "HISTORY_SAVED_SAFETY_FLAGS_CHANGED",
            )
            with engine.connect() as c, c.begin():
                c.execute(text("SET TRANSACTION READ ONLY"))
                ledger = DemoLedger.model_validate(
                    c.execute(
                        select(accounts.c.state).where(accounts.c.account_id == account)
                    ).scalar_one()
                )
            common.require(
                public_plan(plan_requests(ledger, common)) == intent["plan"],
                "HISTORY_REQUEST_PLAN_CHANGED",
            )
            common.require(
                result["requests_observed"] == len(result["observations"]) == delta["attempts"] - 2
                and result["requests_unqueried"] == 4 - len(result["observations"]),
                "HISTORY_SAVED_COUNT_CHANGED",
            )
            common.require(
                result["stopped_after_not_found"] == (delta["last_http_status"] == 400),
                "HISTORY_SAVED_STOP_REASON_CHANGED",
            )
            for i, (observed, planned) in enumerate(
                zip(result["observations"], intent["plan"], strict=False)
            ):
                not_found = (
                    delta["last_http_status"] == 400 and i == len(result["observations"]) - 1
                )
                allowed = (
                    {
                        "NEW",
                        "TRIGGERING",
                        "TRIGGERED",
                        "FINISHED",
                        "CANCELED",
                        "REJECTED",
                        "EXPIRED",
                    }
                    if planned["role"] == "STOP_LOSS"
                    else {
                        "NEW",
                        "PARTIALLY_FILLED",
                        "FILLED",
                        "CANCELED",
                        "REJECTED",
                        "EXPIRED",
                        "EXPIRED_IN_MATCH",
                    }
                )
                common.require(
                    observed["request_reference_sha256"] == planned["request_reference_sha256"]
                    and observed["role"] == planned["role"]
                    and observed["remote_resolution_verified"] is False
                    and observed["automatically_resolvable"] is False
                    and observed["response_body_bound_to_audit_chain"] is False
                    and re.fullmatch("[0-9a-f]{64}", observed["response_body_sha256"]) is not None
                    and observed["response_identity_fields_match"] is (not not_found)
                    and (
                        observed["observed_status"] == "NOT_FOUND_UNRESOLVED"
                        if not_found
                        else observed["observed_status"] in allowed
                    ),
                    "HISTORY_SAVED_OBSERVATION_CHANGED",
                )
            return dict(
                result,
                status_read_only=True,
                credential_session_accessed=False,
                http_attempts=0,
                modeled_weight_charged=0,
                audit_events_added=0,
                resource_checkpoints_added=0,
                original_http_attempts=delta["attempts"],
                original_attempt_summary=delta,
            )
        common.require(
            not folder.exists() and not folder.is_symlink(),
            "HISTORY_ALREADY_ATTEMPTED_RUN_STATUS_ONLY",
        )
        with engine.connect() as control, control.begin():
            control.execute(text("SET LOCAL lock_timeout='2s'"))
            control.execute(text("SET LOCAL statement_timeout='2s'"))
            row = (
                control.execute(
                    select(accounts)
                    .where(accounts.c.account_id == account)
                    .with_for_update(read=True)
                )
                .mappings()
                .one()
            )
            validate_account_row(row, scope, journal, installation, clock)
            ledger = DemoLedger.model_validate(row["state"])
            plan = plan_requests(ledger, common)
            shared = AuditedDemoReadJournal(
                directory,
                scope,
                journal,
                anchors=anchor,
                audit=audit,
                monotonic=clock.monotonic,
                wall_clock=clock.wall_clock,
                boot=clock.boot,
            )
            before = common.protected_snapshot(
                engine, directory.parent, shared.path, folder, fingerprint
            )
            common.require(
                before == preflight["protected_snapshot_sha256"],
                "HISTORY_STATE_CHANGED_AFTER_PREFLIGHT",
            )
            original = audit.review(shared)
            common.require(
                original["startup_ready"]
                and original["local_read_activity_complete"]
                and original["audit_events"] == preflight["shared_audit_events"]
                and original["shared_total_weight"] == 268
                and original["audit_events"] + 12 <= 1024,
                "HISTORY_AUDIT_NOT_READY",
            )
            resources, _ = common.resource_rows(engine, scope)
            state["stage"] = "HISTORICAL_PRIVATE_CREDENTIAL_PREFLIGHT"
            try:
                credentials = credential_loader()
            except Exception:
                common.require(False, "PROBE_CREDENTIAL_SESSION_UNAVAILABLE")
                raise
            common.require(
                isinstance(credentials, DemoCredentials), "PROBE_CREDENTIAL_SESSION_UNAVAILABLE"
            )
            # Record a sanitized diagnostic before the installed transport rejects HTTP errors.
            wire = sender or _demo_get_http
            last: dict[str, Any] = {}

            def record(method: Any, path: Any, query: Any, key: Any) -> Any:
                response = wire(method, path, query, key)
                last.clear()
                last.update(
                    path=path,
                    status=response.status,
                    body_sha256=hashlib.sha256(response.body).hexdigest(),
                )
                if response.status == 400:
                    try:
                        body = json.loads(response.body)
                        if (
                            isinstance(body, dict)
                            and type(body.get("code")) is int
                            and body["code"] in {-2013, -2011}
                        ):
                            last["not_found_code"] = body["code"]
                    except (ValueError, UnicodeError):
                        pass
                return response

            transport = AccountedDemoReadTransport(
                settings,
                shared,
                enable_reads=True,
                sender=record,
                monotonic=clock.monotonic,
                max_requests=6,
                max_seconds=30,
            )
            client = DemoClient(
                settings,
                credentials,
                send=transport,
                clock_ms=lambda: int(clock.wall_clock().timestamp() * 1000),
            )
            os.mkdir(folder, mode=0o700)
            state["intent_created"] = True
            intent = dict(
                policy=POLICY,
                operation_id=operation,
                account=account,
                scope=scope,
                journal=str(journal),
                installation=str(installation),
                source_reference=source_reference,
                plan=public_plan(plan),
                protected_snapshot_sha256=before,
                first_sequence=original["audit_events"],
                prior_resources=[[list(k), v] for k, v in sorted(resources.items())],
            )
            _write_new(folder / "intent.json", (json.dumps(intent, sort_keys=True) + "\n").encode())
            _sync_directory(folder)
            _sync_directory(directory.parent)
            state["stage"] = "HISTORICAL_ACCOUNTED_TIME_AND_MODE"
            client.sync_clock()
            config = client.request("GET", "/fapi/v1/accountConfig")
            common.require(
                isinstance(config, dict)
                and config.get("dualSidePosition") is False
                and config.get("multiAssetsMargin") is False,
                "HISTORY_ACCOUNT_MODE_MISMATCH",
            )
            observations = []
            for item in plan:
                state["stage"] = "HISTORICAL_ORIGINAL_ID_" + item["role"]
                try:
                    payload = client.request("GET", item["path"], item["params"])
                except Exception:
                    # Preserve the installed stop-on-HTTP-error rule. Never query the remaining IDs.
                    if (
                        last.get("status") == 400
                        and last.get("path") == item["path"]
                        and "not_found_code" in last
                    ):
                        observations.append(
                            dict(
                                request_reference_sha256=item["request_reference_sha256"],
                                role=item["role"],
                                observed_status="NOT_FOUND_UNRESOLVED",
                                response_identity_fields_match=False,
                                response_body_sha256=last["body_sha256"],
                                response_body_bound_to_audit_chain=False,
                                remote_resolution_verified=False,
                                automatically_resolvable=False,
                            )
                        )
                        break
                    raise
                record_value = observation(item, payload, common)
                record_value["response_body_sha256"] = last["body_sha256"]
                observations.append(record_value)
            delta = audit_delta(engine, directory, scope, journal, installation, intent, common)
            metrics = transport.diagnostics()
            common.require(
                delta["attempts"] == metrics["http_attempts"] == 2 + len(observations)
                and metrics["modeled_weight_charged"] == delta["modeled_weight_charged"],
                "HISTORY_TRANSPORT_COUNT_MISMATCH",
            )
            after = common.protected_snapshot(
                engine, directory.parent, shared.path, folder, fingerprint
            )
            common.require(after == before, "HISTORY_PROTECTED_STATE_CHANGED")
            result = dict(
                policy=POLICY,
                operation_id=operation,
                historical_request_evidence="OBSERVED_KEEP_PAUSED",
                **delta,
                http_attempts=delta["attempts"],
                completed_reads=metrics["completed_reads"],
                observations=observations,
                requests_observed=len(observations),
                requests_unqueried=4 - len(observations),
                stopped_after_not_found=delta["last_http_status"] == 400,
                automatic_retry=False,
                protected_snapshot_sha256=after,
                financial_ledger_unchanged_verified=True,
                request_states_unchanged=True,
                reconciliation_performed=False,
                financial_database_changed=False,
                historical_orders_retained=34,
                full_history_proof_replaced=False,
                capacity_shortfall_resolved=False,
                credential_session_accessed=True,
                order_writes=0,
                orders_enabled=False,
                execution_approved=False,
                live_trading_enabled=False,
                paused=True,
                exchange_account_identity_verified=False,
                account_order_usage_verified=False,
                request_cost_upper_bounds_proven=False,
                actual_ip_capacity_reserved=False,
                trading_readiness_checked=False,
                next_action="REVIEW_ORIGINAL_ID_EVIDENCE_NO_RETRY_OR_RECONCILIATION",
            )
            _write_new(
                folder / "complete.json", (json.dumps(result, sort_keys=True) + "\n").encode()
            )
            _sync_directory(folder)
            state["stage"] = "COMPLETE"
            return result
    finally:
        if transport is not None:
            transport.close()
            state["transport"] = transport.diagnostics()
        os.close(fd)
