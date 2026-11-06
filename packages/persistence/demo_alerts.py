"""Atomic DEMO urgent alert outbox; no network and no raw ledger payloads."""

import hashlib
from datetime import datetime
from typing import Literal, Self
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import (
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
)
from sqlalchemy.dialects.postgresql import JSONB

from packages.domain.contracts import RiskState, UTCDateTime
from packages.persistence.market import metadata
from services.execution.demo_models import DemoLedger

AlertCode = Literal[
    "STOP_PROTECTION_FAILURE",
    "EXIT_OUTCOME_UNKNOWN",
    "EMERGENCY_ENTERED",
    "RISK_LOCK_ENTERED",
    "PRIVATE_SESSION_FAILED",
    "DELIVERY_PIPELINE_TEST",
]
Signals = dict[AlertCode, set[str]]


class AlertEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1] = 1
    alert_id: UUID
    event_id: UUID
    environment: Literal["DEMO"] = "DEMO"
    severity: Literal["CRITICAL", "TEST"] = "CRITICAL"
    code: AlertCode
    account_ref: str = Field(pattern=r"^[a-f0-9]{24}$")
    occurred_at: UTCDateTime
    risk_state: RiskState
    paused: bool
    order_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def check_test_scope(self) -> Self:
        is_test = self.code == "DELIVERY_PIPELINE_TEST"
        if is_test != (self.severity == "TEST") or (is_test and self.order_ids):
            raise ValueError("ALERT_TEST_SEVERITY_OR_ORDER_SCOPE_INVALID")
        return self


alerts = Table(
    "demo_urgent_alerts",
    metadata,
    Column("alert_id", String(36), primary_key=True),
    Column("event_id", ForeignKey("demo_events.event_id"), nullable=False),
    Column("account_id", ForeignKey("demo_accounts.account_id"), nullable=False),
    Column("code", String(40), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("delivered_at", DateTime(timezone=True)),
    Column("attempts", Integer, nullable=False),
    Column("next_attempt_at", DateTime(timezone=True), nullable=False),
    Column("claim_token", String(36)),
    Column("claim_until", DateTime(timezone=True)),
    Column("last_failure", String(40), nullable=False),
    UniqueConstraint("event_id", "code", name="uq_demo_urgent_event_code"),
    CheckConstraint("attempts >= 0", name="ck_demo_urgent_attempts"),
    CheckConstraint("(claim_token IS NULL) = (claim_until IS NULL)", name="ck_demo_urgent_claim"),
)
attempts = Table(
    "demo_urgent_alert_attempts",
    metadata,
    Column("attempt_id", String(36), primary_key=True),
    Column("alert_id", ForeignKey("demo_urgent_alerts.alert_id"), nullable=False),
    Column("number", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("result", String(40), nullable=False),
    UniqueConstraint("alert_id", "number", name="uq_demo_urgent_attempt_number"),
)


def signals(ledger: DemoLedger) -> Signals:
    found: Signals = {}
    if ledger.risk_state == RiskState.EMERGENCY:
        found["EMERGENCY_ENTERED"] = {"ACCOUNT"}
    elif ledger.risk_state.value in {"DAILY_LOCK", "WEEKLY_LOCK", "DRAWDOWN_LOCK", "UNHEALTHY"}:
        found["RISK_LOCK_ENTERED"] = {ledger.risk_state.value}
    for order in ledger.orders.values():
        reference = str(order.signal.signal_id)
        if (
            order.open_quantity
            and order.algo_id
            and ledger.requests.get(order.algo_id) in {"UNKNOWN", "NOT_SENT", "REJECTED"}
        ):
            found.setdefault("STOP_PROTECTION_FAILURE", set()).add(reference)
        if order.exit_id and order.exit_state == "UNKNOWN":
            found.setdefault("EXIT_OUTCOME_UNKNOWN", set()).add(reference + ":" + order.exit_id)
    return found


def enqueue(
    conn: Connection,
    event_id: str,
    account: str,
    at: datetime,
    kind: str,
    before: Signals,
    after: DemoLedger,
) -> None:
    new: Signals = {
        code: tokens - before.get(code, set())
        for code, tokens in signals(after).items()
        if tokens - before.get(code, set())
    }
    if kind == "DEMO_PRIVATE_SESSION_FAILED":
        new["PRIVATE_SESSION_FAILED"] = set()
    for code, tokens in new.items():
        affected = tuple(
            sorted(
                {
                    UUID(token.split(":", 1)[0])
                    for token in tokens
                    if token
                    not in {"ACCOUNT", "DAILY_LOCK", "WEEKLY_LOCK", "DRAWDOWN_LOCK", "UNHEALTHY"}
                },
                key=str,
            )
        )
        identifier = uuid5(UUID(event_id), code)
        envelope = AlertEnvelope(
            alert_id=identifier,
            event_id=UUID(event_id),
            code=code,
            account_ref=hashlib.sha256(account.encode()).hexdigest()[:24],
            occurred_at=at,
            risk_state=after.risk_state,
            paused=after.paused,
            order_ids=affected,
        )
        conn.execute(
            insert(alerts).values(
                alert_id=str(identifier),
                event_id=event_id,
                account_id=account,
                code=code,
                payload=envelope.model_dump(mode="json"),
                created_at=at,
                delivered_at=None,
                attempts=0,
                next_attempt_at=at,
                claim_token=None,
                claim_until=None,
                last_failure="NONE",
            )
        )
