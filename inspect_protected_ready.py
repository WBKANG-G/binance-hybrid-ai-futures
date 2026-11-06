"""Read-only local evidence for one failed protected-worker readiness check."""

import json
import os
from decimal import Decimal, InvalidOperation
from uuid import UUID, uuid5

from packages.domain.config import database_url
from packages.domain.contracts import OrderState, PositionState, RiskState
from packages.persistence.demo import accounts, events
from services.execution.demo_models import PrivateSessionDiagnostic
from sqlalchemy import cast, create_engine, func, select
from sqlalchemy.dialects.postgresql import JSONB

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"
OPERATION = UUID("5bb5814c-55e0-4b7f-882c-a7a449ec2182")
KINDS = {
    "DEMO_PROTECTED_WORKER_START",
    "DEMO_PROTECTED_WORKER_READY",
    "DEMO_PROTECTED_WORKER_FINALLY",
    "DEMO_OPERATOR_RESUME",
    "DEMO_ENTRY_RESERVED",
    "DEMO_ENTRY_INTENT",
    "DEMO_ENTRY_EFFECT",
    "DEMO_RECONCILIATION_FAILED",
    "DEMO_PRIVATE_SESSION_FAILED",
    "DEMO_SL_VERIFIED_QUERY",
    "DEMO_PROTECTION_RECHECK",
    "DEMO_OPERATION_INTENT",
    "DEMO_CANCEL_INTENT",
    "DEMO_CANCEL_EFFECT",
    "DEMO_REDUCE_ONLY_INTENT",
    "DEMO_REDUCE_ONLY_EFFECT",
    "DEMO_FLAT_CONFIRMATION_CHECK",
    "DEMO_STREAM_UNTRUSTED",
    "DEMO_STREAM_CONNECTED",
}
REQUESTS = {
    "NONE",
    "SUBMITTING",
    "ACKNOWLEDGED",
    "UNKNOWN",
    "NOT_SENT",
    "REJECTED",
    "RECONCILED",
    "FILLED",
    "CANCELED",
    "EXPIRED",
}
MISMATCHES = {
    "available_balance",
    "entry_prices",
    "equity",
    "isolated_margins",
    "isolated_wallets",
    "liquidation_prices",
    "margin_used",
    "orders",
    "positions",
    "wallet_balance",
    "leverage",
    "margin_types",
}


def marker(operation, name):
    return str(uuid5(operation, "protected-worker-" + name))


def text(value, allowed):
    return value if isinstance(value, str) and value in allowed else "UNRECOGNIZED"


def number(value):
    try:
        n = Decimal(str(value))
        return str(n) if n.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def session_failure(state):
    raw = state.get("last_private_session_failure")
    if raw is None:
        return None
    try:
        return PrivateSessionDiagnostic.model_validate(raw).model_dump(mode="json")
    except ValueError:
        return {"code": "DIAGNOSTIC_SCHEMA_UNRECOGNIZED"}


def summary(state, operation):
    key = str(operation)
    order = state.get("orders", {}).get(key)
    out = {
        "risk_state": text(state.get("risk_state"), {v.value for v in RiskState}),
        "paused": state.get("paused") if type(state.get("paused")) is bool else None,
        "reconciliation_failure_code": text(
            state.get("reconciliation_failure_code", "NONE"),
            {"NONE", "SNAPSHOT_CHANGED", "SNAPSHOT_INVALID", "OTHER_DEMO_FAILURE"},
        ),
        "mismatch_fields": [
            v for v in state.get("last_reconciliation_mismatch_fields", []) if v in MISMATCHES
        ],
        "session_failure": session_failure(state),
        "stop_verified": state.get("validation_evidence", {}).get(key, {}).get("stop_verified")
        is True,
        "target_verified": state.get("validation_evidence", {}).get(key, {}).get("target_verified")
        is True,
        "order_present": isinstance(order, dict),
    }
    if isinstance(order, dict):
        out["order"] = {
            "entry_state": text(order.get("state"), {v.value for v in OrderState}),
            "position_state": text(order.get("position_state"), {v.value for v in PositionState}),
            "quantity": number(order.get("quantity")),
            "filled": number(order.get("filled")),
            "remaining": number(order.get("remaining")),
            "exit_state": text(order.get("exit_state", "NONE"), REQUESTS),
            "exit_id_present": bool(order.get("exit_id")),
            "request_states": {
                role: text(state.get("requests", {}).get(order.get(field), "NONE"), REQUESTS)
                for role, field in [
                    ("ENTRY", "client_id"),
                    ("STOP", "algo_id"),
                    ("TARGET", "tp_algo_id"),
                    ("EXIT", "exit_id"),
                ]
            },
        }
    return out


def inspect(engine, account=ACCOUNT, operation=OPERATION):
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='3s'")
        row = (
            conn.execute(
                select(accounts.c.state, accounts.c.owner, accounts.c.fence).where(
                    accounts.c.account_id == account
                )
            )
            .mappings()
            .one()
        )
        start_key = marker(operation, "start")
        condition = (
            (events.c.account_id == account)
            & (events.c.kind == "DEMO_PROTECTED_WORKER_START")
            & (cast(events.c.payload, JSONB)["operations"][start_key].astext == "STARTED")
        )
        started = (
            conn.execute(select(events.c.as_of, events.c.fence).where(condition))
            .mappings()
            .one_or_none()
        )
        if started is None:
            raise ValueError("SCOPED_START_NOT_FOUND")
        start, start_fence = started["as_of"], started["fence"]
        scope = (
            (events.c.account_id == account)
            & (events.c.as_of >= start)
            & events.c.kind.in_(KINDS)
            & events.c.fence.in_([start_fence, start_fence + 1])
        )
        total = conn.execute(select(func.count()).select_from(events).where(scope)).scalar_one()
        history = (
            conn.execute(
                select(events.c.as_of, events.c.kind, events.c.fence, events.c.payload)
                .where(scope)
                .order_by(events.c.as_of, events.c.event_id)
                .limit(201)
            )
            .mappings()
            .all()
        )
    state = row["state"]
    operations = state.get("operations", {})
    timeline = []
    for record in history[:200]:
        snapshot = json.loads(record["payload"])
        timeline.append(
            {
                "at_utc": record["as_of"].isoformat(),
                "kind": record["kind"],
                "fence": record["fence"],
                **summary(snapshot, operation),
            }
        )
    # Fixed projection only. No raw ledger, requests, event payloads or exception output.
    return {
        "protected_ready_diagnostic": "READ_ONLY_LOCAL_SCOPED_EVIDENCE_KEEP_PAUSED",
        "operation_id": str(operation),
        "observed_start_utc": start.isoformat(),
        "leader_released": row["owner"] is None,
        "current_fence": row["fence"],
        "ready_marker_recorded": operations.get(marker(operation, "ready"))
        == "FULL_PROTECTED_READY",
        "finally_marker_recorded": operations.get(marker(operation, "finally")) == "FINALLY_RAN",
        "child_failure_exception_recorded": False,
        "current_local_state": summary(state, operation),
        "timeline_count": total,
        "timeline_fence_scope": [start_fence, start_fence + 1],
        "timeline_truncated": total > 200,
        "timeline": timeline,
        "audit_time_scope": "LOCAL_AFTER_ROW_LOCK_BEFORE_APPLY_NOT_EXCHANGE_OR_COMMIT_TIME",
        "exchange_checked": False,
        "database_changed_by_diagnostic": False,
        "unlock_applied": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }


def main():
    engine = None
    try:
        if (
            os.getenv("LIVE_TRADING_ENABLED", "false").lower() != "false"
            or os.getenv("TRADING_MODE", "PAPER").upper() == "LIVE"
        ):
            raise ValueError("DEMO_CONTEXT_REQUIRED")
        engine = create_engine(
            database_url().get_secret_value(), connect_args={"connect_timeout": 2}
        )
        report = inspect(engine)
        code = 0
    except Exception:  # noqa: BLE001 -- never expose credentials, ledger or exception details
        report = {
            "protected_ready_diagnostic": "UNAVAILABLE_KEEP_PAUSED",
            "details_redacted": True,
            "orders_enabled": False,
            "live_trading_enabled": False,
        }
        code = 2
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report), flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
