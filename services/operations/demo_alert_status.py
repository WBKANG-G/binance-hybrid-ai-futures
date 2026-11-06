"""Read-only alert visibility: receiver ACK is distinct from inbox/worker liveness."""

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Engine, and_, func, select

from packages.persistence.demo_alerts import AlertCode, alerts
from services.operations.demo_alert_delivery import FAILURES

ACCOUNT = "DEMO-WINDOWS-ISOLATED-BTC-V1"


class PublicModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmailMetadata(PublicModel):
    state: Literal[
        "CONFIGURED_PRIVATE_RAM", "NOT_CONFIGURED", "BLOCKED_EXPIRED_OR_INVALID", "UNKNOWN"
    ]
    expires_unix: int | None = None
    remaining_seconds: int | None = Field(default=None, ge=0, le=43200)

    @model_validator(mode="after")
    def validity_metadata(self) -> Self:
        if self.state == "CONFIGURED_PRIVATE_RAM":
            if self.expires_unix is None or self.expires_unix <= 0 or not self.remaining_seconds:
                raise ValueError("Configured metadata requires a positive expiry")
        elif self.expires_unix is not None or self.remaining_seconds is not None:
            raise ValueError("Unavailable metadata must not invent an expiry")
        return self


class QueueStatus(PublicModel):
    total: int = Field(ge=0)
    pending: int = Field(ge=0)
    accepted: int = Field(ge=0)
    critical_total: int = Field(ge=0)
    critical_pending: int = Field(ge=0)
    critical_accepted: int = Field(ge=0)
    test_total: int = Field(ge=0)
    test_pending: int = Field(ge=0)
    test_accepted: int = Field(ge=0)
    oldest_pending_at: datetime | None
    oldest_pending_age_seconds: int | None
    timing_consistent: bool


class RecentAlert(PublicModel):
    code: AlertCode
    created_at: datetime
    accepted_at: datetime | None
    attempts: int = Field(ge=0)
    last_failure: str
    delivery_delay_seconds: int | None
    timing_consistent: bool

    @field_validator("last_failure")
    @classmethod
    def safe_failure(cls, value: str) -> str:
        return value if value in FAILURES or value == "NONE" else "UNKNOWN"


class AlertStatus(PublicModel):
    status: Literal["READ_ONLY_ALERT_STATUS", "UNAVAILABLE"]
    as_of: datetime
    is_stale: bool
    email: EmailMetadata
    queue: QueueStatus | None = None
    latest_critical: RecentAlert | None = None
    worker_liveness_checked: Literal[False] = False
    inbox_confirmation_available: Literal[False] = False
    confirmation_scope: Literal["RECEIVER_ACK_ONLY"] = "RECEIVER_ACK_ONLY"
    orders_enabled: Literal[False] = False
    live_trading_enabled: Literal[False] = False


def elapsed(start: datetime, end: datetime) -> int | None:
    if start.tzinfo is None or end.tzinfo is None or end < start:
        return None
    return int((end - start).total_seconds())


def read_queue(
    engine: Engine, account: str, *, now: datetime | None = None
) -> tuple[datetime, QueueStatus, RecentAlert | None]:
    pending = alerts.c.delivered_at.is_(None)
    critical = alerts.c.code != "DELIVERY_PIPELINE_TEST"
    test = alerts.c.code == "DELIVERY_PIPELINE_TEST"
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        conn.exec_driver_sql("SET LOCAL statement_timeout='2s'")
        row = (
            conn.execute(
                select(
                    func.count().label("total"),
                    func.count().filter(pending).label("pending"),
                    func.count().filter(~pending).label("accepted"),
                    func.count().filter(critical).label("critical_total"),
                    func.count().filter(and_(critical, pending)).label("critical_pending"),
                    func.count().filter(and_(critical, ~pending)).label("critical_accepted"),
                    func.count().filter(test).label("test_total"),
                    func.count().filter(and_(test, pending)).label("test_pending"),
                    func.count().filter(and_(test, ~pending)).label("test_accepted"),
                    func.min(alerts.c.created_at).filter(pending).label("oldest"),
                ).where(alerts.c.account_id == account)
            )
            .mappings()
            .one()
        )
        latest = (
            conn.execute(
                select(
                    alerts.c.code,
                    alerts.c.created_at,
                    alerts.c.delivered_at,
                    alerts.c.attempts,
                    alerts.c.last_failure,
                )
                .where(alerts.c.account_id == account, critical)
                .order_by(alerts.c.created_at.desc(), alerts.c.alert_id)
                .limit(1)
            )
            .mappings()
            .first()
        )
    observed = now or datetime.now(UTC)
    age = elapsed(row["oldest"], observed) if row["oldest"] is not None else None
    queue = QueueStatus(
        **{
            k: int(row[k])
            for k in (
                "total",
                "pending",
                "accepted",
                "critical_total",
                "critical_pending",
                "critical_accepted",
                "test_total",
                "test_pending",
                "test_accepted",
            )
        },
        oldest_pending_at=row["oldest"],
        oldest_pending_age_seconds=age,
        timing_consistent=row["oldest"] is None or age is not None,
    )
    recent = None
    if latest is not None:
        created, accepted = latest["created_at"], latest["delivered_at"]
        delay = elapsed(created, accepted) if accepted is not None else None
        valid = elapsed(created, observed) is not None and (
            accepted is None or (delay is not None and elapsed(accepted, observed) is not None)
        )
        recent = RecentAlert(
            code=latest["code"],
            created_at=created,
            accepted_at=accepted,
            attempts=latest["attempts"],
            last_failure=latest["last_failure"],
            delivery_delay_seconds=delay if valid else None,
            timing_consistent=valid,
        )
    return observed, queue, recent
