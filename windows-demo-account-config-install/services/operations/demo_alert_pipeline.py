"""One explicitly queued TEST alert; never changes financial state or audit rows."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Engine, insert, select, text

from packages.persistence.demo import accounts, events
from packages.persistence.demo_alerts import AlertEnvelope, alerts, attempts
from services.execution.demo_models import DemoLedger

CODE: Literal["DELIVERY_PIPELINE_TEST"] = "DELIVERY_PIPELINE_TEST"


def _validate_existing(row: Any, account: str, operation: UUID) -> None:
    if row["account_id"] != account or row["alert_id"] != str(operation) or row["code"] != CODE:
        raise ValueError("ALERT_TEST_OPERATION_SCOPE_CONFLICT")
    envelope = AlertEnvelope.model_validate_json(json.dumps(row["payload"]))
    if (
        envelope.severity != "TEST"
        or envelope.code != CODE
        or str(envelope.alert_id) != str(operation)
    ):
        raise ValueError("ALERT_TEST_PAYLOAD_INVALID")
    if (
        str(envelope.event_id) != row["event_id"]
        or envelope.account_ref != hashlib.sha256(account.encode()).hexdigest()[:24]
    ):
        raise ValueError("ALERT_TEST_SOURCE_IDENTITY_INVALID")


def enqueue_test(engine: Engine, account: str, operation: UUID) -> bool:
    if not account.startswith("DEMO-") or operation.version != 4:
        raise ValueError("EXPLICIT_DEMO_TEST_UUID4_REQUIRED")
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout='2s'"))
        conn.execute(text("SET LOCAL lock_timeout='2s'"))
        # Short serialization only; no network/worker wait while holding this lock.
        account_row = (
            conn.execute(select(accounts).where(accounts.c.account_id == account).with_for_update())
            .mappings()
            .one()
        )
        existing = (
            conn.execute(select(alerts).where(alerts.c.alert_id == str(operation)))
            .mappings()
            .first()
        )
        if existing is not None:
            _validate_existing(existing, account, operation)
            return False
        ledger = DemoLedger.model_validate(account_row["state"])
        if (
            not ledger.paused
            or any(ledger.positions.values())
            or any(order.remaining or order.open_quantity for order in ledger.orders.values())
        ):
            raise ValueError("ALERT_TEST_REQUIRES_PAUSED_LOCAL_EMPTY_ACCOUNT")
        source = conn.execute(
            select(events.c.event_id)
            .where(events.c.account_id == account)
            .order_by(events.c.as_of.desc(), events.c.event_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if source is None:
            raise ValueError("ALERT_TEST_SOURCE_AUDIT_MISSING")
        if conn.execute(
            select(alerts.c.alert_id).where(alerts.c.event_id == source, alerts.c.code == CODE)
        ).first():
            raise ValueError("SOURCE_ALREADY_HAS_PIPELINE_TEST_DO_NOT_REQUEUE")
        now = datetime.now(UTC)
        envelope = AlertEnvelope(
            alert_id=operation,
            event_id=UUID(source),
            code=CODE,
            severity="TEST",
            account_ref=hashlib.sha256(account.encode()).hexdigest()[:24],
            occurred_at=now,
            risk_state=ledger.risk_state,
            paused=ledger.paused,
        )
        conn.execute(
            insert(alerts).values(
                alert_id=str(operation),
                event_id=source,
                account_id=account,
                code=CODE,
                payload=envelope.model_dump(mode="json"),
                created_at=now,
                delivered_at=None,
                attempts=0,
                next_attempt_at=now,
                claim_token=None,
                claim_until=None,
                last_failure="NONE",
            )
        )
    return True


def review_test(engine: Engine, account: str, operation: UUID) -> dict[str, object]:
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        conn.execute(text("SET LOCAL statement_timeout='2s'"))
        row = (
            conn.execute(select(alerts).where(alerts.c.alert_id == str(operation)))
            .mappings()
            .first()
        )
        if row is None:
            return {"pipeline_check": "NOT_REGISTERED", "operation_id": str(operation)}
        _validate_existing(row, account, operation)
        if (
            conn.execute(
                select(events.c.event_id).where(
                    events.c.event_id == row["event_id"], events.c.account_id == account
                )
            ).first()
            is None
        ):
            raise ValueError("ALERT_TEST_SOURCE_SCOPE_INVALID")
        history = (
            conn.execute(
                select(attempts)
                .where(attempts.c.alert_id == str(operation))
                .order_by(attempts.c.number)
            )
            .mappings()
            .all()
        )
    latest = history[-1] if history else None
    accepted = bool(
        row["delivered_at"] is not None
        and row["claim_token"] is None
        and row["last_failure"] == "NONE"
        and latest is not None
        and latest["number"] == row["attempts"]
        and latest["result"] == "ACKNOWLEDGED"
        and latest["finished_at"] == row["delivered_at"]
        and len(history) == row["attempts"]
        and [r["number"] for r in history] == list(range(1, row["attempts"] + 1))
    )
    state = (
        "QUEUE_DELIVERY_ACKNOWLEDGED"
        if accepted
        else "INCONSISTENT_DELIVERY_RECORD"
        if row["delivered_at"] is not None
        else "RETRY_PENDING"
        if row["last_failure"] != "NONE"
        else "WORKER_PROCESSING"
        if row["claim_token"] is not None
        else "QUEUED_WAITING_FOR_WORKER"
    )
    return {
        "pipeline_check": state,
        "operation_id": str(operation),
        "alert_id": row["alert_id"],
        "severity": "TEST",
        "code": CODE,
        "attempt_count": row["attempts"],
        "attempt_results": [r["result"] for r in history],
        "last_failure": row["last_failure"],
        "delivered_at": row["delivered_at"].isoformat() if row["delivered_at"] else None,
        "inbox_confirmation": "REQUIRES_USER_CONFIRMATION",
        "trading_state_modified": False,
        "financial_audit_modified": False,
        "orders_enabled": False,
        "live_trading_enabled": False,
    }
