"""Additive DEMO urgent alert delivery queue; existing audit/outbox untouched."""

from alembic import op
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004_demo_urgent_alerts"
down_revision = "0003_demo"
branch_labels = None
depends_on = None
metadata = MetaData()
Table("demo_events", metadata, Column("event_id", String(36), primary_key=True))
Table("demo_accounts", metadata, Column("account_id", String(80), primary_key=True))
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


def upgrade() -> None:
    alerts.create(op.get_bind())
    attempts.create(op.get_bind())


def downgrade() -> None:
    attempts.drop(op.get_bind())
    alerts.drop(op.get_bind())
